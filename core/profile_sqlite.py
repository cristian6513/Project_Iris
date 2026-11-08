"""A SQLite store in the mutable Iris profile, opened the hardened way (shared by F04 and F10).

Extracted unchanged from the automations store (F04–F07), where each guarantee answers a review:

* every real open checks the profile boundary again (Q02): the folder must still be a plain
  directory of the profile and the database and its sidecars plain files with one link, the
  same file before and after connecting;
* while a connection is open (T01) the profile root, the store's folder and the database are
  pinned on Windows: handles with read access and without FILE_SHARE_DELETE, so none of them can
  be renamed, deleted or replaced while SQLite opens the file. Each connection also proves it
  reached this store (its ``store_id``), not another one under the same path;
* SQLite never opens a sidecar Iris did not establish (S01): every connection takes EXCLUSIVE
  locking before its first access, so no ``-shm`` exists; the ``-wal`` of a writing connection
  is created here and pinned before SQLite can open it; one connection at a time (a lock file
  in the folder, across threads and processes);
* a new store is built in memory (C01), written by Iris to a file it creates under a random
  name, already in WAL mode with its schema, and only then given its real name;
* a store written by a newer Iris is refused before anything is written (C02): its version is
  read from the database's header, or from page 1 of a WAL left by a crash, read the way SQLite
  recovers it, before SQLite opens the store.

A store names its folder, file, schema, messages and error classes in a ``StoreSpec``.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import struct
import threading
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar, Iterator


@dataclass(frozen=True)
class StoreSpec:
    folder: tuple[str, ...]  # inside the profile, e.g. ("automations",) or ("connections", "google")
    filename: str
    lock_name: str
    noun: str  # «almacén de automatizaciones»: every message names the store
    schema: tuple[str, ...]
    schema_version: int
    store_name: str
    path_error: type[Exception]
    version_error: type[Exception]
    busy_error: type[Exception]


def profile_store_path(spec: StoreSpec, home: Path) -> Path:
    """``<profile>/<folder…>/<file>``, refusing links inside the profile."""
    from core.profile_paths import _plain_directory, validate_profile_home

    home = validate_profile_home(home)
    folder = home.joinpath(*spec.folder)
    validate_profile_home(folder)
    _plain_directory(folder, home)
    return folder / spec.filename


SIDECARS = ("", "-wal", "-shm", "-journal")

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _KERNEL32.CreateFileW.restype = wintypes.HANDLE
    _KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]

    class _HandleInfo(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("created", wintypes.FILETIME),
                    ("accessed", wintypes.FILETIME), ("written", wintypes.FILETIME),
                    ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD), ("size_low", wintypes.DWORD),
                    ("links", wintypes.DWORD), ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

    _KERNEL32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(_HandleInfo)]
    _INVALID_HANDLE = wintypes.HANDLE(-1).value


class Pins:
    """Handles that keep the store's path fixed while SQLite uses it (T01; Windows only).

    Read access makes the share mode bind; without FILE_SHARE_DELETE nobody can
    open these for deleting or renaming, so the chain cannot be swapped and
    put back between the checks and SQLite's own open. SQLite, which opens
    with read/write sharing, keeps working.
    """

    def __init__(self, spec: StoreSpec) -> None:
        self.spec = spec
        self.handles: list[Any] = []

    def pin(self, path: Path, *, directory: bool) -> None:
        if os.name != "nt":
            return
        error, noun = self.spec.path_error, self.spec.noun
        flags = 0x00200000 | (0x02000000 if directory else 0)  # OPEN_REPARSE_POINT (+ BACKUP_SEMANTICS)
        handle = _KERNEL32.CreateFileW(str(path), 0x1, 0x1 | 0x2, None, 3, flags, None)  # read data, share R|W
        if handle in (None, _INVALID_HANDLE):
            raise error(f"No se pudo fijar la ruta del {noun}.")
        self.handles.append(handle)
        info = _HandleInfo()
        if not _KERNEL32.GetFileInformationByHandle(handle, ctypes.byref(info)):
            raise error(f"No se pudo identificar la ruta del {noun}.")
        is_directory = bool(info.attributes & 0x10)
        if info.attributes & 0x400 or is_directory != directory or (not directory and info.links != 1):
            raise error(f"El {noun} contiene un enlace o un archivo no permitido.")

    def release(self) -> None:
        while self.handles:
            _KERNEL32.CloseHandle(self.handles.pop())


class StoreLock:
    """One connection to a store at a time, across threads and processes (S01).

    Shared per lock file; reentrant within a thread. The file lock covers one
    byte and writes nothing, so even a planted link changes no content.
    """

    _registry: dict[str, StoreLock] = {}
    _registry_guard = threading.Lock()

    @classmethod
    def for_folder(cls, folder: Path, spec: StoreSpec) -> StoreLock:
        path = folder / spec.lock_name
        key = os.path.normcase(os.path.abspath(path))
        with cls._registry_guard:
            return cls._registry.setdefault(key, cls(path, spec))

    def __init__(self, path: Path, spec: StoreSpec) -> None:
        self.path = path
        self.spec = spec
        self._thread_lock = threading.RLock()
        self._depth = 0
        self._fd: int | None = None

    def acquire(self, timeout: float = 30.0) -> None:
        self._thread_lock.acquire()
        try:
            if self._depth == 0:
                fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, "O_BINARY", 0)
                             | getattr(os, "O_NOFOLLOW", 0), 0o600)
                try:
                    plain_file(self.path, self.spec)
                    deadline = time.monotonic() + timeout
                    while True:
                        try:
                            if os.name == "nt":
                                import msvcrt

                                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                            else:
                                import fcntl

                                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            break
                        except OSError:
                            if time.monotonic() >= deadline:
                                raise self.spec.busy_error(f"El {self.spec.noun} está ocupado.") from None
                            time.sleep(0.01)
                except BaseException:
                    os.close(fd)
                    raise
                self._fd = fd
            self._depth += 1
        except BaseException:
            self._thread_lock.release()
            raise

    def release(self) -> None:
        self._depth -= 1
        try:
            if self._depth == 0 and self._fd is not None:
                fd, self._fd = self._fd, None
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl

                        fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
        finally:
            self._thread_lock.release()


class PinnedConnection(sqlite3.Connection):
    """A connection that releases its pins, and then the store's lock, when it closes.

    The pins go first: SQLite still holds its database and ``-wal`` open, so
    nothing can be swapped, and it must be free to delete its ``-wal`` on close.
    The store's lock goes last, once SQLite has closed.
    """

    pins: Pins | None = None
    store_lock: StoreLock | None = None

    def close(self) -> None:
        try:
            if self.pins is not None:
                self.pins.release()
                self.pins = None
        finally:
            try:
                super().close()
            finally:
                if self.store_lock is not None:
                    lock, self.store_lock = self.store_lock, None
                    lock.release()


def plain_file(path: Path, spec: StoreSpec) -> tuple[int, int] | None:
    """``(device, inode)`` of a plain, single-link file; None if it does not exist."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if not stat.S_ISREG(info.st_mode) or reparse or info.st_nlink != 1:
        raise spec.path_error(f"El {spec.noun} contiene un enlace o un archivo no permitido.")
    return info.st_dev, info.st_ino


def plain_folder(folder: Path, spec: StoreSpec) -> None:
    """Without a profile root (tests, tools): at least the store's own folder is plain."""
    try:
        real = os.path.normcase(str(folder.resolve(strict=False)))
        expected = os.path.normcase(str(folder.parent.resolve(strict=False) / folder.name))
        if (folder.is_symlink() or (hasattr(folder, "is_junction") and folder.is_junction())
                or real != expected or (folder.exists() and not folder.is_dir())):
            raise spec.path_error(f"La carpeta del {spec.noun} es un enlace.")
    except OSError as exc:
        raise spec.path_error(f"No se pudo validar la carpeta del {spec.noun}.") from exc


SQLITE_MAGIC = b"SQLite format 3\x00"
_WAL_MAGIC = 0x377F0682  # the low bit gives the checksums' byte order
_WAL_FORMAT = 3007000


def _wal_checksum(data: bytes, big_endian: bool, s0: int, s1: int) -> tuple[int, int]:
    """SQLite's cumulative WAL checksum over ``data`` (pairs of 32-bit words), from ``(s0, s1)``."""
    words = struct.unpack(f"{'>' if big_endian else '<'}{len(data) // 4}I", data)
    for i in range(0, len(words), 2):
        s0 = (s0 + words[i] + s1) & 0xFFFFFFFF
        s1 = (s1 + words[i + 1] + s0) & 0xFFFFFFFF
    return s0, s1


def committed_page_one(wal, spec: StoreSpec) -> bytes | None:
    """Page 1 as the WAL's last committed transaction left it, or None if no committed frame has it.

    Read the way SQLite's recovery reads it (sqlite.org/fileformat.html, "The Write-Ahead Log"): a
    header SQLite would ignore means an empty WAL; frames count while their salts and cumulative
    checksums hold, and only up to the last commit frame.
    """
    header = wal.read(32)
    if len(header) < 32:
        return None
    magic, version, page_size, _, salt1, salt2, sum0, sum1 = struct.unpack(">8I", header)
    if magic & 0xFFFFFFFE != _WAL_MAGIC or page_size & (page_size - 1) or not 512 <= page_size <= 65536:
        return None
    big_endian = bool(magic & 1)
    running = _wal_checksum(header[:24], big_endian, 0, 0)
    if running != (sum0, sum1):
        return None
    if version != _WAL_FORMAT:  # SQLite would refuse to open it: a newer format, never touched
        raise spec.version_error(f"El {spec.noun} usa un formato más nuevo; no se modificó.")
    latest = committed = None
    while len(frame := wal.read(24 + page_size)) == 24 + page_size:
        page, commit_size, frame_salt1, frame_salt2, sum0, sum1 = struct.unpack(">6I", frame[:24])
        if page == 0 or (frame_salt1, frame_salt2) != (salt1, salt2):
            break
        running = _wal_checksum(frame[:8], big_endian, *running)
        running = _wal_checksum(frame[24:], big_endian, *running)
        if running != (sum0, sum1):
            break
        if page == 1:
            latest = frame[24:]
        if commit_size:
            committed = latest
    return committed


def new_store_image(spec: StoreSpec, store_id: str) -> bytes:
    """The bytes of an empty current store, built by SQLite in memory: no file is involved."""
    with closing(sqlite3.connect(":memory:", isolation_level=None)) as conn:
        conn.execute("BEGIN")
        for statement in spec.schema:
            conn.execute(statement)
        conn.execute("INSERT INTO meta VALUES ('store', ?)", (spec.store_name,))
        conn.execute("INSERT INTO meta VALUES ('store_id', ?)", (store_id,))
        conn.execute(f"PRAGMA user_version = {spec.schema_version}")
        conn.execute("COMMIT")
        image = bytearray(conn.serialize())
    # WAL mode is a property of the file: header bytes 18 and 19 (read and write versions) = 2.
    image[18] = image[19] = 2
    return bytes(image)


class ProfileSqliteStore:
    """Base of a profile store: hardened opening, one write transaction at a time, reads.

    Subclasses set ``SPEC`` and may override ``_upgrade`` (an older schema found at open),
    ``_new_id`` and ``_new_image``. ``_meta_table`` must exist in every schema.
    """

    SPEC: ClassVar[StoreSpec]

    def __init__(self, path: Path, *, root: Path | None = None) -> None:
        """``root``: the profile the store belongs to; every open checks the path from it."""
        self.path = Path(path)
        self.root = Path(root) if root is not None else None
        self._lock = threading.Lock()
        self._store_id: str | None = None  # learnt at the first open; every later connection must match
        self._open()

    # ── Hooks ───────────────────────────────────────────────────────────

    def _new_id(self) -> str:
        return uuid.uuid4().hex

    def _new_image(self) -> bytes:
        return new_store_image(self.SPEC, self._new_id())

    def _pins(self) -> Pins:
        return Pins(self.SPEC)

    def _upgrade(self, conn: sqlite3.Connection, version: int) -> None:
        """An older schema found at open (``version`` 0 means a store that had none yet)."""

    # ── Connection and schema ───────────────────────────────────────────

    def _boundary(self) -> tuple[int, int] | None:
        """Q02: the folder is still a plain part of the profile, and the database and its
        sidecars plain single-link files. Returns the database file's identity."""
        spec = self.SPEC
        if self.root is not None:
            from core.profile_paths import ProfilePathError

            try:
                if profile_store_path(spec, self.root) != self.path:
                    raise spec.path_error(f"El {spec.noun} ya no está en su perfil.")
            except ProfilePathError as exc:
                raise spec.path_error(str(exc)) from exc
        else:
            plain_folder(self.path.parent, spec)
        identity = None
        for suffix in SIDECARS:
            found = plain_file(self.path.with_name(self.path.name + suffix), spec)
            if suffix == "":
                identity = found
        return identity

    def _sidecar(self, suffix: str) -> Path:
        return self.path.with_name(self.path.name + suffix)

    def _checked(self, open_connection: Callable[[type], sqlite3.Connection], *,
                 writable: bool = True) -> sqlite3.Connection:
        """Open only inside the boundary, with the path pinned, and make sure the store reached is this one."""
        spec = self.SPEC
        pins = self._pins()
        lock, held = None, False
        try:
            if self.root is not None:
                pins.pin(self.root, directory=True)
            pins.pin(self.path.parent, directory=True)
            lock = StoreLock.for_folder(self.path.parent, spec)  # inside the pinned folder
            lock.acquire()
            held = True
            if not self.path.exists():  # SQLite never creates the real path (see _create_new)
                raise spec.path_error(f"El {spec.noun} no está en su carpeta.")
            pins.pin(self.path, directory=False)
            wal = self._sidecar("-wal")
            wal_found = plain_file(wal, spec) is not None  # left by a crash: pinned before it is read
            if wal_found:
                pins.pin(wal, directory=False)
            self._refuse_newer(wal_found)  # C02: before SQLite could recover or checkpoint anything
            if writable and not wal_found:
                # S01: the -wal is ours, plain and pinned before SQLite can open it.
                try:
                    os.close(os.open(wal, os.O_CREAT | os.O_EXCL | os.O_RDWR
                                     | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0), 0o600))
                    created = True
                except FileExistsError:
                    created = False  # appeared meanwhile: checked like one found
                pins.pin(wal, directory=False)
                if not created:
                    self._refuse_newer(True)
            before = self._boundary()
            conn = open_connection(PinnedConnection)
        except BaseException:
            pins.release()
            if held:
                lock.release()
            raise
        conn.pins, conn.store_lock = pins, lock
        try:
            # Before any access (S01): the WAL index stays in this connection's memory, so no
            # -shm is ever created, opened or written. Other connections wait for this one.
            conn.execute("PRAGMA locking_mode = EXCLUSIVE")
            conn.execute("PRAGMA schema_version").fetchone()  # touches the file: it is open now
            after = self._boundary()
            if after is None or after != before:
                raise spec.path_error(f"El archivo del {spec.noun} cambió al abrirlo.")
            if self._store_id is not None:
                row = conn.execute("SELECT value FROM meta WHERE key = 'store_id'").fetchone()
                if row is None or row[0] != self._store_id:
                    raise spec.path_error(f"La conexión no llegó al {spec.noun} de este perfil.")
        except BaseException:
            conn.close()
            raise
        return conn

    def _connect(self) -> sqlite3.Connection:
        conn = self._checked(lambda factory: sqlite3.connect(str(self.path), timeout=10, isolation_level=None,
                                                             factory=factory))
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA synchronous = FULL")
        return conn

    def _create_new(self) -> None:
        """C01: build a new store in memory (WAL mode, schema, identity), write it to a file Iris
        creates under a random name, and only then give that file its real name. SQLite never
        opens the new file, so none of its sidecars exists to be linked elsewhere meanwhile."""
        spec = self.SPEC
        folder = self.path.parent
        folder.mkdir(parents=True, exist_ok=True)
        temp = folder / f".{self.path.name}.{self._new_id()}.new"
        pins, lock, held, ours = self._pins(), StoreLock.for_folder(folder, spec), False, False
        try:
            if self.root is not None:
                pins.pin(self.root, directory=True)
            pins.pin(folder, directory=True)
            lock.acquire()
            held = True
            if self.path.exists():
                return  # another Iris process created it while this one waited
            self._boundary()  # the folder is still a plain part of the profile
            image = self._new_image()
            try:
                fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
                             | getattr(os, "O_NOFOLLOW", 0), 0o600)
            except FileExistsError:
                raise spec.path_error(f"El {spec.noun} no se pudo crear en su carpeta.") from None
            ours = True
            try:
                info = os.fstat(fd)
                identity = (info.st_dev, info.st_ino)
                view = memoryview(image)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
            if plain_file(temp, spec) != identity:
                raise spec.path_error(f"El {spec.noun} cambió mientras se creaba.")
            try:
                os.rename(temp, self.path)
            except FileExistsError:
                return  # another Iris process created it meanwhile: that store is the one
            ours = False
            if plain_file(self.path, spec) != identity:  # never written: refused here and by every open
                raise spec.path_error(f"El {spec.noun} cambió mientras se creaba.")
        finally:
            pins.release()
            if held:
                lock.release()
            if ours:
                try:
                    temp.unlink()  # a name only, whatever it points to now: no content is written
                except OSError:
                    pass

    def _refuse_newer(self, wal_found: bool) -> None:
        """C02: refuse a store whose last committed transaction is from a newer Iris, read
        without SQLite (which could recover or checkpoint it): the pinned database's header
        (offset 60) or, after a crash, page 1 as its pinned WAL commits it."""
        spec = self.SPEC
        with open(self.path, "rb") as handle:
            header = handle.read(100)
        if len(header) < 100 or not header.startswith(SQLITE_MAGIC):
            raise spec.path_error(f"El {spec.noun} no es una base de datos válida.")
        if wal_found:
            with open(self._sidecar("-wal"), "rb") as handle:
                page = committed_page_one(handle, spec)
            if page is not None:
                if not page.startswith(SQLITE_MAGIC):
                    raise spec.path_error(f"El {spec.noun} no es una base de datos válida.")
                header = page
        version = int.from_bytes(header[60:64], "big")
        if version > spec.schema_version:
            raise spec.version_error(
                f"El {spec.noun} es de una versión más nueva de Iris ({version}); no se modificó.")

    def _open(self) -> None:
        spec = self.SPEC
        if self._boundary() is None:
            self._create_new()
        # A newer store is refused by _checked before SQLite opens it, so it is never touched.
        with closing(self._connect()) as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("BEGIN IMMEDIATE")
            try:
                version = conn.execute("PRAGMA user_version").fetchone()[0]
                if version > spec.schema_version:  # written meanwhile by a newer Iris
                    raise spec.version_error(f"El {spec.noun} es de una versión más nueva de Iris.")
                for statement in spec.schema:
                    conn.execute(statement)
                self._upgrade(conn, version)
                if version == 0:
                    conn.execute("INSERT OR IGNORE INTO meta VALUES ('store', ?)", (spec.store_name,))
                if version < spec.schema_version:
                    conn.execute(f"PRAGMA user_version = {spec.schema_version}")
                conn.execute("INSERT OR IGNORE INTO meta VALUES ('store_id', ?)", (self._new_id(),))
                store_id = conn.execute("SELECT value FROM meta WHERE key = 'store_id'").fetchone()[0]
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        self._store_id = store_id

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """One write transaction; any exception rolls everything back."""
        with self._lock, closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

    def _read(self) -> sqlite3.Connection:
        return closing(self._connect())
