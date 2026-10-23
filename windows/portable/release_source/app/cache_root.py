"""Filesystem-backed root identity and crash-safe optional-cache initialization.

On Windows an opened directory's volume serial/file index, not its spelling,
is the identity.  The identity-keyed kernel mutex is outside the unmarked root,
is shared by independent processes and is abandoned automatically after death.
Nothing is placed in an unmarked root until a complete marker is atomically
published.  A nonempty unmarked root is never adopted, including old roots.
"""
from __future__ import annotations

import contextlib
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
import uuid
from typing import Any

ROOT_PROTOCOL = "labimage-directory-identity-v1"
INITIALIZATION_TIMEOUT_SECONDS = 10.0
MAX_MARKER_BYTES = 4096
INITIALIZATION_STAGE_PROTOCOL = "v2"


def _event(events: list[dict[str, Any]] | None, stage: str, **fields: Any) -> None:
    if events is not None:
        events.append(dict(stage=stage, time_ns=time.time_ns(), pid=os.getpid(),
                           thread_id=threading.get_ident(), **fields))


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _reject_reparse(path: Path) -> None:
    value = path.lstat()
    if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
        raise ValueError("cache root and marker must not be reparse points")


def directory_identity(path: str | Path) -> tuple[Path, dict[str, str]]:
    """Return a post-creation operational path and the system directory ID.

    Final paths are obtained through the *existing* directory handle.  Resolving
    this existing final path is only for consistent downstream pathlib checks;
    neither the returned spelling nor case folding decides marker acceptance.
    Root-level junctions/symlinks are intentionally rejected. Existing ancestor
    aliases resolve to their physical location via the directory handle.
    """
    path = Path(path).absolute()
    _reject_reparse(path)
    if not path.is_dir():
        raise ValueError("cache root is not a directory")
    if os.name != "nt":
        value = path.stat()
        return path.resolve(strict=True), {"kind": "posix-device-inode-v1",
                                          "device": str(value.st_dev), "inode": str(value.st_ino)}

    from ctypes import wintypes

    class FileInformation(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("creation", wintypes.FILETIME),
                    ("access", wintypes.FILETIME), ("write", wintypes.FILETIME),
                    ("volume", wintypes.DWORD), ("size_high", wintypes.DWORD),
                    ("size_low", wintypes.DWORD), ("links", wintypes.DWORD),
                    ("index_high", wintypes.DWORD), ("index_low", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(FileInformation)]
    kernel.GetFileInformationByHandle.restype = wintypes.BOOL
    kernel.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                               wintypes.DWORD, wintypes.DWORD]
    kernel.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    kernel.GetVolumeInformationByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD,
                                                    ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
                                                    ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR, wintypes.DWORD]
    kernel.GetVolumeInformationByHandleW.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # FILE_READ_ATTRIBUTES; share read/write/delete; OPEN_EXISTING;
    # FILE_FLAG_BACKUP_SEMANTICS (directory handle).
    handle = kernel.CreateFileW(str(path), 0x80, 0x7, None, 3, 0x02000000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        information = FileInformation()
        if not kernel.GetFileInformationByHandle(handle, ctypes.byref(information)):
            raise ctypes.WinError(ctypes.get_last_error())
        if information.attributes & 0x400:
            raise ValueError("cache root must not be a reparse point")
        filesystem = ctypes.create_unicode_buffer(64)
        serial = wintypes.DWORD()
        maximum_component = wintypes.DWORD()
        flags = wintypes.DWORD()
        if not kernel.GetVolumeInformationByHandleW(handle, None, 0, ctypes.byref(serial),
                                                    ctypes.byref(maximum_component), ctypes.byref(flags),
                                                    filesystem, len(filesystem)):
            raise ctypes.WinError(ctypes.get_last_error())
        # The 64-bit ID API is not guaranteed unique on ReFS; remote servers can
        # return partial identities. Do not pretend those unqualified stores
        # have the local-NTFS identity guarantees used by this protocol.
        if filesystem.value != "NTFS" or not (information.index_high or information.index_low):
            raise ValueError("cache root requires NTFS with usable system file IDs")
        required = kernel.GetFinalPathNameByHandleW(handle, None, 0, 0)
        if not required:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_unicode_buffer(required + 1)
        length = kernel.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            raise ctypes.WinError(ctypes.get_last_error())
        if buffer.value.upper().startswith("\\\\?\\UNC\\"):
            raise ValueError("remote UNC cache roots are not qualified by this local protocol")
        operational = Path(buffer.value).resolve(strict=True)
        identity = {"kind": "win32-volume-file-index-v1", "volume_serial": f"{information.volume:08x}",
                    "file_index": f"{information.index_high:08x}{information.index_low:08x}"}
        return operational, identity
    finally:
        kernel.CloseHandle(handle)


@contextlib.contextmanager
def identity_mutex(identity: dict[str, str], *, timeout_seconds: float = INITIALIZATION_TIMEOUT_SECONDS):
    """Wait boundedly; lock ownership is released by the OS after process death."""
    name = "LabImageFeatureCacheInit-v1-" + hashlib.sha256(_canonical(identity)).hexdigest()
    if os.name != "nt":
        import fcntl
        lock_dir = Path(tempfile.gettempdir()) / "labimage-feature-cache-init-v1"
        lock_dir.mkdir(mode=0o700, exist_ok=True)
        with (lock_dir / name).open("a+b") as lock:
            deadline = time.monotonic() + timeout_seconds
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("cache root initialization mutex timeout")
                    time.sleep(.01)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)
        return

    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.ReleaseMutex.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateMutexW(None, False, "Local\\" + name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    acquired = False
    try:
        outcome = kernel.WaitForSingleObject(handle, int(timeout_seconds * 1000))
        if outcome == 0x102:
            raise TimeoutError("cache root initialization mutex timeout")
        if outcome not in (0, 0x80):  # WAIT_OBJECT_0 or WAIT_ABANDONED
            raise ctypes.WinError(ctypes.get_last_error())
        acquired = True
        yield
    finally:
        if acquired:
            kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def marker_payload(identity: dict[str, str], magic: str, schema: str) -> dict[str, Any]:
    return {"magic": magic, "schema": schema, "root_protocol": ROOT_PROTOCOL,
            "root_identity": identity}


def verify_root(root: Path, identity: dict[str, str] | None, *, magic: str,
                schema: str, marker_name: str) -> None:
    if identity is None:
        raise ValueError("cache root has no verified system identity")
    _, current = directory_identity(root)
    if current != identity:
        raise ValueError("cache root directory identity changed")
    marker = root / marker_name
    _reject_reparse(marker)
    if marker.stat().st_size > MAX_MARKER_BYTES:
        raise ValueError("cache root marker too large")
    value = json.loads(marker.read_text(encoding="utf-8"))
    if value != marker_payload(identity, magic, schema):
        raise ValueError("not this feature-cache root identity/protocol")


def initialize_root(requested: str | Path, *, magic: str, schema: str,
                    marker_name: str, events: list[dict[str, Any]] | None = None
                    ) -> tuple[Path, dict[str, str]]:
    requested = Path(requested).absolute()
    _event(events, "before_mkdir", requested=str(requested), exists=requested.exists())
    requested.mkdir(parents=True, exist_ok=True)
    _event(events, "directory_created", requested=str(requested))
    root, identity = directory_identity(requested)
    _event(events, "directory_identified", root=str(root), identity=identity)
    started = time.monotonic()
    with identity_mutex(identity):
        _event(events, "mutex_acquired", wait_seconds=time.monotonic() - started)
        # Re-check after waiting: do not initialize a replacement at this path.
        _, current = directory_identity(root)
        if current != identity:
            raise ValueError("cache root changed during initialization")
        expected_payload = _canonical(marker_payload(identity, magic, schema))
        temporary_prefix = ".feature-cache-init-" + INITIALIZATION_STAGE_PROTOCOL + "-" + hashlib.sha256(_canonical(identity)).hexdigest() + "-"
        stage_name = re.compile(re.escape(temporary_prefix) + r"[0-9a-f]{32}\.tmp\Z")
        blocked_stages = []
        # A new writer creates at most one staging file while owning this
        # identity mutex. Every possible interrupted write to an initially empty
        # file is a prefix of this exact payload (including zero bytes). Reclaim
        # only that protocol-recognized format/content, never arbitrary files.
        # If an orphan cannot be identified/read/deleted, fail closed before any
        # new staging file can be created. Repeated failures cannot add files.
        for abandoned in root.parent.glob(temporary_prefix + "*"):
            try:
                if not stage_name.fullmatch(abandoned.name):
                    raise ValueError("unrecognized initialization staging filename")
                _reject_reparse(abandoned)
                if not abandoned.is_file() or abandoned.stat().st_size > len(expected_payload):
                    raise ValueError("unrecognized initialization staging entry")
                content = abandoned.read_bytes()
                if not expected_payload.startswith(content):
                    raise ValueError("unrecognized initialization staging content")
                abandoned.unlink()
                _event(events, "abandoned_initialization_removed", temporary=str(abandoned), reclaimed_bytes=len(content))
            except (OSError, ValueError) as error:
                blocked_stages.append(str(abandoned))
                _event(events, "abandoned_initialization_blocked", temporary=str(abandoned), reason=type(error).__name__ + ": " + str(error))
        marker = root / marker_name
        if not marker.exists():
            if any(root.iterdir()):
                raise ValueError("cache root is not empty and has no cache marker")
            if blocked_stages:
                raise ValueError("unrecognized or inaccessible initialization staging entry; no new staging file created")
            # A crash before publication leaves the root empty and recoverable.
            # Staging in the physical parent also guarantees same-volume rename.
            temporary = root.parent / (temporary_prefix + uuid.uuid4().hex + ".tmp")
            descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                _event(events, "temporary_created", temporary=str(temporary), maximum_bytes=len(expected_payload))
                with os.fdopen(descriptor, "wb") as stream:
                    descriptor = None
                    stream.write(expected_payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                _event(events, "temporary_fsynced", temporary=str(temporary))
                # Hard-link publication is atomic and refuses any pre-existing
                # marker. It cannot overwrite a concurrent unknown marker.
                try:
                    os.link(temporary, marker)
                except FileExistsError:
                    _event(events, "marker_publish_competitor")
                else:
                    _event(events, "marker_published", marker=str(marker))
            finally:
                if descriptor is not None:
                    os.close(descriptor)
                temporary.unlink(missing_ok=True)
        verify_root(root, identity, magic=magic, schema=schema, marker_name=marker_name)
        _event(events, "root_verified", root=str(root), identity=identity)
    return root, identity
