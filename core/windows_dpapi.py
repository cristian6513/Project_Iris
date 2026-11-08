"""The Windows user-bound protection primitive shared by Google client and token storage."""
import os


class ProtectionError(RuntimeError):
    pass


def crypt(data: bytes, *, protect: bool, entropy: bytes) -> bytes:
    if os.name != 'nt':
        raise ProtectionError('Windows protection is unavailable.')
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_char))]

    def blob(raw):
        buffer = ctypes.create_string_buffer(raw, len(raw))
        return Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer

    crypt32, kernel32 = ctypes.WinDLL('crypt32', use_last_error=True), ctypes.WinDLL('kernel32')
    function = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR if protect else ctypes.c_void_p,
                         ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    (source, _keep), (extra, _keep_extra), result = blob(data), blob(entropy), Blob()
    if not function(ctypes.byref(source), 'Iris OS Google connection' if protect else None,
                    ctypes.byref(extra), None, None, 0x1, ctypes.byref(result)):
        raise ProtectionError('The protected data cannot be opened by this user and profile.')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel32.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))
