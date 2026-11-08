"""F08a draft: one opt-in Win32 observation, without hooks or input contents.

Core is the native bridge shared by Desktop and Companion. Keeping the probe
here avoids relying on JavaScript timers in minimized/hidden WebViews.
"""
from __future__ import annotations

from dataclasses import dataclass
import sys

from core.presence_state import MAX_DESKTOP_GAP, PresenceTracker


class NativeObservationError(RuntimeError):
    """No current trustworthy observation; never evidence of absence."""


@dataclass(frozen=True)
class InputSample:
    uptime_ms: int
    last_input_tick: int


class WindowsInputReader:
    """Lazy Win32 bindings. No DLL or native call before the user's opt-in."""

    supported = sys.platform == 'win32'

    def __init__(self):
        self._bindings = None

    def _api(self):
        if not self.supported:
            raise NativeObservationError('unsupported_platform')
        if self._bindings is None:
            import ctypes as C
            from ctypes import wintypes as W

            class LASTINPUTINFO(C.Structure):
                _fields_ = [('cbSize', W.UINT), ('dwTime', W.DWORD)]

            user = C.WinDLL('user32', use_last_error=True)
            kernel = C.WinDLL('kernel32', use_last_error=True)
            wts = C.WinDLL('wtsapi32', use_last_error=True)
            user.GetLastInputInfo.argtypes = [C.POINTER(LASTINPUTINFO)]
            user.GetLastInputInfo.restype = W.BOOL
            user.OpenInputDesktop.argtypes = [W.DWORD, W.BOOL, W.DWORD]
            user.OpenInputDesktop.restype = W.HANDLE
            user.GetUserObjectInformationW.argtypes = [W.HANDLE, C.c_int, W.LPVOID, W.DWORD, C.POINTER(W.DWORD)]
            user.GetUserObjectInformationW.restype = W.BOOL
            user.CloseDesktop.argtypes = [W.HANDLE]
            user.CloseDesktop.restype = W.BOOL
            kernel.GetTickCount64.argtypes = []
            kernel.GetTickCount64.restype = C.c_ulonglong
            wts.WTSQuerySessionInformationW.argtypes = [W.HANDLE, W.DWORD, C.c_int, C.POINTER(W.LPVOID), C.POINTER(W.DWORD)]
            wts.WTSQuerySessionInformationW.restype = W.BOOL
            wts.WTSFreeMemory.argtypes = [W.LPVOID]
            wts.WTSFreeMemory.restype = None
            self._bindings = C, W, user, kernel, wts, LASTINPUTINFO
        return self._bindings

    def interactive(self) -> None:
        """Check this session/desktop without reading input timestamps."""
        C, W, user, kernel, wts, LASTINPUTINFO = self._api()
        # Current session only; disconnected RDP and switched-away users are
        # unknown. Never fall back to the console or another user's session.
        memory, size = W.LPVOID(), W.DWORD()
        try:
            if not wts.WTSQuerySessionInformationW(None, 0xFFFFFFFF, 8, C.byref(memory), C.byref(size)):
                raise NativeObservationError('session_unavailable')
            if not memory or size.value != C.sizeof(C.c_int):
                raise NativeObservationError('session_unavailable')
            if C.cast(memory, C.POINTER(C.c_int)).contents.value != 0:  # WTSActive
                raise NativeObservationError('session_inactive')
        finally:
            if memory:
                wts.WTSFreeMemory(memory)
        desktop = user.OpenInputDesktop(0, False, 1)  # DESKTOP_READOBJECTS; no switch/hook rights
        if not desktop:
            raise NativeObservationError('desktop_unavailable')
        try:
            name, needed = C.create_unicode_buffer(256), W.DWORD()
            if not user.GetUserObjectInformationW(desktop, 2, name, C.sizeof(name), C.byref(needed)):
                raise NativeObservationError('desktop_unavailable')
            if name.value.casefold() != 'default':
                raise NativeObservationError('secure_desktop')
        finally:
            user.CloseDesktop(desktop)

    def read(self) -> InputSample:
        self.interactive()
        C, W, user, kernel, wts, LASTINPUTINFO = self._api()
        info = LASTINPUTINFO(C.sizeof(LASTINPUTINFO), 0)
        if not user.GetLastInputInfo(C.byref(info)):
            raise NativeObservationError('input_unavailable')
        return InputSample(int(kernel.GetTickCount64()), int(info.dwTime))


def windows_notifications_allowed() -> bool:
    """Windows' documented notification advisory, fail closed on unknown.

    This is not a promise to detect every Windows 11 Focus/Do-not-disturb rule.
    Iris also provides its own explicit Focus/silence control.
    """
    try:
        reader = WindowsInputReader()
        reader.interactive()
        C, _, _, _, _, _ = reader._api()
        shell = C.WinDLL('shell32', use_last_error=True)
        query = shell.SHQueryUserNotificationState
        query.argtypes, query.restype = [C.POINTER(C.c_int)], C.c_long
        state = C.c_int()
        return query(C.byref(state)) == 0 and state.value == 5  # QUNS_ACCEPTS_NOTIFICATIONS
    except (OSError, NativeObservationError, AttributeError):
        return False


class DesktopIdleObserver:
    """Accumulates only continuous samples seen *after* consent, never idle-age.

    A reboot, secure desktop, suspend gap, source failure or consent revision
    resets the baseline and episode. A changed input tick ends the episode
    even when Windows reports a non-increasing tick (SendInput is allowed to).
    """

    def __init__(self, tracker: PresenceTracker, *, reader=None):
        self.tracker = tracker
        self.reader = reader if reader is not None else WindowsInputReader()
        self._previous: InputSample | None = None
        self._revision: int | None = None
        self._warming = True

    @property
    def supported(self) -> bool:
        return self.reader.supported is True

    def reset(self, reason='disabled') -> None:
        self._previous = None
        self._warming = True
        self.tracker.desktop_unavailable(reason)

    def tick(self, *, enabled: bool, revision: int) -> bool:
        """True only for a directly observed idle→active transition."""
        if revision != self._revision:
            self.reset('consent_changed')
            self._revision = revision
        if not enabled or not self.supported:
            self.reset('disabled' if not enabled else 'unsupported_platform')
            return False
        try:
            sample = self.reader.read()
            if (type(sample.uptime_ms) is not int or type(sample.last_input_tick) is not int
                    or sample.uptime_ms < 0 or not 0 <= sample.last_input_tick <= 0xFFFFFFFF):
                raise NativeObservationError('invalid_sample')
            # A tick apparently in the future, or >24 days old, cannot be used
            # to infer continuity. The exact idle age is neither stored nor sent.
            if ((sample.uptime_ms & 0xFFFFFFFF) - sample.last_input_tick) & 0xFFFFFFFF > 0x7FFFFFFF:
                raise NativeObservationError('invalid_input_clock')
        except (OSError, NativeObservationError, ValueError):
            self.reset('native_unavailable')
            return False
        previous = self._previous
        self._previous = sample
        if previous is None:
            self.tracker.desktop_unavailable('warming_up')
            return False
        delta = (sample.uptime_ms - previous.uptime_ms) / 1000
        if not 0 < delta <= MAX_DESKTOP_GAP:
            self.reset('observation_gap')
            self._previous = sample
            return False
        prior = self.tracker.state('desktop_idle')
        if prior.status != 'known' and not self._warming:
            self.reset('clock_or_observation_gap')
            self._previous = sample
            return False
        self._warming = False
        unchanged = sample.last_input_tick == previous.last_input_tick
        self.tracker.desktop_sample(idle=unchanged)
        return not unchanged and prior.inactive
