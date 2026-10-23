"""Optional, content-addressed single-image artifacts; never pair results.

The detector algorithms remain in engine.py.  Every restored array and mutable
wrapper belongs to its calling worker.  No pickle, shared detector, eager SIFT,
or application-global PreparedImage cache is used here.
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import platform
import re
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import PIL

from . import cache_root, engine
from .annotations import AnnotationAnalysis

CACHE_VERSION = "1.1.3"
CACHE_SCHEMA = "labimage-single-image-npz-v1"
ROOT_MAGIC = "labimage-single-image-feature-cache"
DEFAULT_LIMIT_BYTES = 2 * 1024**3
DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60
DEFAULT_SNAPSHOT_LIMIT_BYTES = 512 * 1024**2
MAX_INPUT_SNAPSHOTS = 4096
MAX_ENTRY_BYTES = 128 * 1024**2
MAX_MANIFEST_BYTES = 2 * 1024**2
SOURCE_ROOT = Path(__file__).resolve().parent
DEFAULT_CACHE_DIR = SOURCE_ROOT.parents[1] / "cache"
MARKER_NAME = ".feature-cache-root.json"
_HEX = re.compile(r"^[0-9a-f]{64}$")
_LOCK = threading.Lock()
_STATS: dict[str, Counter] = {}
_VARIANTS: dict[str, Counter] = {}
_CONFIGS: dict[str, dict[str, Any]] = {}
_ROOT_IMPLEMENTATION_PATH = Path(cache_root.__file__).resolve()
_ROOT_IMPLEMENTATION_SHA = hashlib.sha256(_ROOT_IMPLEMENTATION_PATH.read_bytes()).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


# Capture at module import, as main.py does for algorithm provenance. A live
# service must not label already-loaded old code with a later on-disk edit's
# hash when it creates the next worker. Absolute paths are observations only,
# not part of the reusable content key (identical source copies may share).
_SOURCE_HASHES: dict[str, str] = {}
_SOURCE_PATHS: dict[str, str] = {}
_SOURCE_ERRORS: list[str] = []
for _module_name in ("engine", "annotations", "semantics", "blot_candidates"):
    try:
        _module_path = Path(sys.modules[__package__ + "." + _module_name].__file__).resolve()
        _SOURCE_PATHS[_module_name] = str(_module_path)
        _SOURCE_HASHES[_module_name + ".py"] = _sha(_module_path.read_bytes())
    except Exception as _error:
        _SOURCE_ERRORS.append(_module_name + ":" + type(_error).__name__)


def _count(kind: str, event: str, value: float = 1, variant: str | None = None) -> None:
    with _LOCK:
        _STATS.setdefault(kind, Counter())[event] += value
        if variant is not None:
            _VARIANTS.setdefault(variant, Counter())[event] += value


def reset_stats() -> None:
    with _LOCK:
        _STATS.clear()
        _VARIANTS.clear()


def _enabled() -> bool:
    return os.environ.get("LABIMAGE_FEATURE_CACHE", "0").strip().lower() in {"1", "true", "yes", "on"}


def get_stats(reset: bool = False) -> dict[str, Any]:
    """Process-local observations only; not evidence or scientific scores."""
    with _LOCK:
        available_count = sum(bool(value["available"]) for value in _CONFIGS.values())
        unavailable_count = len(_CONFIGS) - available_count
        result = {"enabled": _enabled(), "cache_version": CACHE_VERSION, "schema": CACHE_SCHEMA,
                  "implementation_path": str(Path(__file__).resolve()),
                  "implementation_sha256": _IMPLEMENTATION_SHA,
                  "root_identity_implementation_path": str(_ROOT_IMPLEMENTATION_PATH),
                  "root_identity_implementation_sha256": _ROOT_IMPLEMENTATION_SHA,
                  "available": bool(available_count and not unavailable_count) if _CONFIGS else None,
                  "available_root_count": available_count,
                  "unavailable_root_count": unavailable_count,
                  "algorithm_module_paths": dict(_SOURCE_PATHS), "source_capture_errors": list(_SOURCE_ERRORS),
                  "by_kind": {k: dict(v) for k, v in _STATS.items()},
                  "by_variant": {k: dict(v) for k, v in _VARIANTS.items()},
                  "configurations": list(_CONFIGS.values()),
                  "scope": "current_process; inclusive worker durations are not wall time"}
        if reset:
            _STATS.clear()
            _VARIANTS.clear()
    return result


def _setting(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
        if value < 0:
            raise ValueError("negative cache setting")
        return value
    except (TypeError, ValueError, OverflowError):
        _count("maintenance", "configuration_fallback")
        return default


def _namespace(max_side: int) -> dict[str, Any]:
    # Conservative complete algorithm-module dependency namespace. main.py and
    # this adapter's implementation digest are recorded separately, not falsely
    # presented as unchanged algorithm source.
    if _SOURCE_ERRORS:
        raise ValueError("cannot bind cache to loaded source provenance")
    return {"schema": CACHE_SCHEMA, "cache_contract_version": CACHE_VERSION,
            "root_identity_contract": {"protocol": cache_root.ROOT_PROTOCOL,
                                       "implementation_sha256": _ROOT_IMPLEMENTATION_SHA},
            "sources": dict(_SOURCE_HASHES),
            "environment": {"python": sys.version, "implementation": platform.python_implementation(),
                            "machine": platform.machine(), "system": platform.system(), "byteorder": sys.byteorder,
                            "opencv": cv2.__version__, "opencv_build_sha256": _sha(cv2.getBuildInformation().encode()),
                            "opencv_threads": cv2.getNumThreads(), "opencv_use_optimized": cv2.useOptimized(),
                            "opencv_cpu_features": cv2.getCPUFeaturesLine(), "opencv_opencl": cv2.ocl.useOpenCL(),
                            "numpy": np.__version__, "pillow": PIL.__version__},
            "parameters": {"max_side": max_side,
                           "sift": {"nfeatures": 2200, "nOctaveLayers": 3, "contrastThreshold": .025,
                                    "edgeThreshold": 12, "sigma": 1.6, "descriptorType": "CV_32F",
                                    "other_defaults_bound_by_opencv_build": True}},
            "artifacts": ["prepare_rgb_gray_information_annotation", "naturally_requested_sift_points_descriptors"],
            "view_contract": "gray/R/G/B each normal/mirrored; original engine channel and mirror rules"}


class CacheStore:
    """Single-file atomic NPZ entries, bounded root-wide quota, optional failure."""

    def __init__(self, max_side: int = 1280, *, root: str | Path | None = None):
        self.namespace_payload = _namespace(max(512, min(int(max_side), 2000)))
        self.namespace = _sha(_canonical(self.namespace_payload))
        self.root_requested = str(root if root is not None else os.environ.get("LABIMAGE_FEATURE_CACHE_DIR", str(DEFAULT_CACHE_DIR)))
        self.root = Path(self.root_requested).absolute()
        self.root_identity = None
        self.root_error = None
        self.root_initialization: list[dict[str, Any]] = []
        self.limit_bytes = _setting("LABIMAGE_FEATURE_CACHE_LIMIT_BYTES", DEFAULT_LIMIT_BYTES)
        self.ttl_seconds = _setting("LABIMAGE_FEATURE_CACHE_TTL_SECONDS", DEFAULT_TTL_SECONDS)
        self.snapshot_limit_bytes = _setting("LABIMAGE_FEATURE_CACHE_SNAPSHOT_LIMIT_BYTES", DEFAULT_SNAPSHOT_LIMIT_BYTES)
        self.available = False
        try:
            self.root, self.root_identity = cache_root.initialize_root(
                self.root_requested, magic=ROOT_MAGIC, schema=CACHE_SCHEMA,
                marker_name=MARKER_NAME, events=self.root_initialization)
            self._verify_root()
            self.available = True
            inputs = self.root / ".inputs"
            if inputs.is_dir() and not inputs.is_symlink():
                # Snapshots are private to a prepare call. Keep recent files
                # (including other live workers); remove only >24h leftovers.
                for old in inputs.glob("input-*"):
                    try:
                        if not old.is_symlink() and time.time() - old.stat().st_mtime > 24 * 60 * 60:
                            old.unlink()
                            _count("maintenance", "abandoned_input_removed")
                    except OSError:
                        _count("maintenance", "cleanup_failure")
        except Exception as error:
            self.root_error = type(error).__name__ + ": " + str(error)
            _count("maintenance", "root_unavailable")
        with _LOCK:
            _CONFIGS[self.namespace + ":" + str(self.root)] = {
                "namespace": self.namespace, "namespace_payload": self.namespace_payload,
                "root": str(self.root), "available": self.available,
                "root_requested": self.root_requested, "root_identity": self.root_identity,
                "root_protocol": cache_root.ROOT_PROTOCOL, "root_error": self.root_error,
                "root_initialization": self.root_initialization,
                "initialization_staging_disk": {"protocol": cache_root.INITIALIZATION_STAGE_PROTOCOL,
                    "maximum_active_or_recognized_orphan_files_per_root": 1,
                    "maximum_payload_bytes_per_root": cache_root.MAX_MARKER_BYTES,
                    "recovery": "identity mutex; exact staging name and expected payload prefix including empty; unknown/inaccessible/undeletable entries preserved and block any new staging publication"},
                "limit_bytes": self.limit_bytes, "ttl_seconds": self.ttl_seconds,
                "snapshot_limit_bytes": self.snapshot_limit_bytes, "snapshot_file_limit": MAX_INPUT_SNAPSHOTS,
                "max_entry_bytes": MAX_ENTRY_BYTES,
                "temporary_disk_bound": "quota plus at most one 128 MiB publishing temporary; abandoned temporary removed by next writer",
                "input_snapshot_disk": "private root/.inputs independently hard-bounded by snapshot_limit_bytes (default 512 MiB) including reservations and crash leftovers; exclusive reservation lock; at most 4096 files; successful calls remove it; >24h leftovers cleaned on startup",
                "memory": "no application-wide image cache; NPZ returns worker-owned writable arrays; original worker LRU unchanged"}

    def _verify_root(self) -> None:
        cache_root.verify_root(self.root, self.root_identity, magic=ROOT_MAGIC,
                               schema=CACHE_SCHEMA, marker_name=MARKER_NAME)

    def _key(self, content_sha: str, kind: str, view: str = "gray", mirrored: bool = False) -> dict[str, Any]:
        if not _HEX.fullmatch(content_sha) or kind not in {"prepare", "sift"} or view not in {"gray", "R", "G", "B"}:
            raise ValueError("invalid feature cache key")
        return {"namespace": self.namespace, "content_sha256": content_sha, "kind": kind,
                "view": view, "mirrored": bool(mirrored)}

    def path_for(self, content_sha: str, kind: str, view: str = "gray", mirrored: bool = False) -> Path:
        return self.root / self.namespace / (_sha(_canonical(self._key(content_sha, kind, view, mirrored))) + ".npz")

    def reserve_snapshot(self, size: int, suffix: str) -> tuple[int, Path] | None:
        """Reserve exact snapshot length under an independent nonwaiting lock.

        Reservations are real files with their full final length, so process
        death cannot erase accounting or let later processes oversubscribe the
        byte quota. Writers must keep that length while filling the file (r+b,
        never truncating wb). Failure only forfeits optional cache preparation.
        """
        lock = self.root / ".snapshot.lock"
        token = uuid.uuid4().hex
        acquired = False
        descriptor = None
        snapshot = None
        try:
            if type(size) is not int or size < 0 or size > self.snapshot_limit_bytes:
                _count("prepare", "snapshot_quota_fallback")
                return None
            self._verify_root()
            try:
                lock_descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if time.time() - lock.stat().st_mtime > 300:
                    lock.unlink()
                    _count("maintenance", "stale_snapshot_lock_recovered")
                    lock_descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                else:
                    _count("prepare", "snapshot_contention_fallback")
                    return None
            with os.fdopen(lock_descriptor, "w", encoding="ascii") as stream:
                stream.write(token)
            acquired = True
            inputs = self.root / ".inputs"
            inputs.mkdir(exist_ok=True)
            if inputs.is_symlink() or inputs.resolve().parent != self.root:
                raise OSError("input snapshots must remain inside marked cache root")
            reserved_bytes = 0
            files = 0
            for existing in inputs.iterdir():
                if existing.is_symlink() or not existing.is_file():
                    raise OSError("unrecognized snapshot entry")
                try:
                    reserved_bytes += existing.stat().st_size
                    files += 1
                except FileNotFoundError:
                    # An active owner may have just completed and removed it.
                    continue
            if files >= MAX_INPUT_SNAPSHOTS or reserved_bytes + size > self.snapshot_limit_bytes:
                _count("prepare", "snapshot_quota_fallback")
                return None
            descriptor, name = tempfile.mkstemp(prefix=f"input-{os.getpid()}-", suffix=suffix, dir=inputs)
            snapshot = Path(name)
            os.ftruncate(descriptor, size)
            # Both size and the on-disk reservation are now visible before
            # releasing the lock. No in-memory reservation ledger is required.
            _count("prepare", "snapshot_reservations")
            _count("prepare", "snapshot_reserved_bytes", size)
            result = descriptor, snapshot
            descriptor = None
            snapshot = None
            return result
        except Exception:
            _count("prepare", "snapshot_reservation_failure")
            return None
        finally:
            if descriptor is not None:
                with contextlib.suppress(OSError):
                    os.close(descriptor)
            if snapshot is not None:
                with contextlib.suppress(OSError):
                    snapshot.unlink(missing_ok=True)
            if acquired:
                try:
                    if lock.read_text(encoding="ascii") == token:
                        lock.unlink()
                except OSError:
                    _count("maintenance", "snapshot_lock_cleanup_failure")

    @contextlib.contextmanager
    def _maintenance(self):
        """No wait: contention only forfeits an optional cache write."""
        lock = self.root / ".maintenance.lock"
        acquired = False
        token = uuid.uuid4().hex
        try:
            self._verify_root()
            try:
                descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                # The critical section has no compute and is bounded to small
                # local IO. Recover an abandoned writer after five minutes.
                if time.time() - lock.stat().st_mtime > 300:
                    lock.unlink()
                    _count("maintenance", "stale_lock_recovered")
                    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                else:
                    yield False
                    return
            with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                stream.write(token)
            acquired = True
            # Only a maintenance-lock holder creates these files. Therefore
            # leftovers cannot belong to a current legitimate publishing writer.
            for folder in self._namespace_folders():
                for path in folder.glob(".publish-*.tmp"):
                    try:
                        if not path.is_symlink():
                            path.unlink()
                            _count("maintenance", "abandoned_temporary_removed")
                    except OSError:
                        _count("maintenance", "cleanup_failure")
            yield True
        finally:
            if acquired:
                try:
                    if lock.read_text(encoding="ascii") == token:
                        lock.unlink()
                except OSError:
                    _count("maintenance", "lock_cleanup_failure")

    def _namespace_folders(self):
        return [p for p in self.root.iterdir() if _HEX.fullmatch(p.name) and p.is_dir() and not p.is_symlink()]

    def _entries(self):
        for folder in self._namespace_folders():
            for path in folder.glob("*.npz"):
                if _HEX.fullmatch(path.stem) and not path.is_symlink() and path.is_file():
                    yield path

    def _load(self, content_sha: str, kind: str, *, view: str = "gray", mirrored: bool = False):
        variant = kind + "." + view + (".mirrored" if mirrored else ".normal")
        _count(kind, "requests", variant=variant)
        started = time.perf_counter()
        path = self.path_for(content_sha, kind, view, mirrored)
        if not self.available:
            _count(kind, "miss", variant=variant)
            _count(kind, "fallback", variant=variant)
            return None
        try:
            stat = path.stat()
        except FileNotFoundError:
            _count(kind, "miss", variant=variant)
            return None
        except Exception:
            _count(kind, "invalid", variant=variant)
            _count(kind, "fallback", variant=variant)
            return None
        if time.time() - stat.st_mtime > self.ttl_seconds:
            _count(kind, "expired", variant=variant)
            _count(kind, "miss", variant=variant)
            return None
        try:
            if path.is_symlink() or path.parent.is_symlink() or stat.st_size > MAX_ENTRY_BYTES:
                raise ValueError("invalid NPZ size or symlink")
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                expected_names = {"__manifest__.npy", "rgb.npy", "gray.npy", "annotation_mask.npy", "uncertain_mask.npy", "data_mask.npy", "labels.npy"} if kind == "prepare" else {"__manifest__.npy", "points.npy", "descriptors.npy"}
                names = {member.filename for member in members}
                if (len(members) != len(names) or len(members) > 8 or not names.issubset(expected_names)
                        or "__manifest__.npy" not in names or sum(m.file_size for m in members) > MAX_ENTRY_BYTES
                        or any(m.flag_bits & 1 for m in members)):
                    raise ValueError("invalid NPZ archive layout")
                if archive.getinfo("__manifest__.npy").file_size > MAX_MANIFEST_BYTES:
                    raise ValueError("oversized manifest")
                # Validate NPY headers before numpy can allocate from a forged
                # enormous shape. ZIP expansion limits alone are insufficient.
                for member in members:
                    with archive.open(member) as stream:
                        version = np.lib.format.read_magic(stream)
                        if version == (1, 0):
                            shape, _, dtype = np.lib.format.read_array_header_1_0(stream)
                        elif version == (2, 0):
                            shape, _, dtype = np.lib.format.read_array_header_2_0(stream)
                        else:
                            raise ValueError("unsupported NPY header version")
                        expected_dtype = np.dtype(np.float32) if member.filename in {"points.npy", "descriptors.npy"} else np.dtype(np.uint8)
                        if (dtype != expected_dtype or len(shape) > 3 or any(type(n) is not int or n < 0 for n in shape)
                                or math.prod(shape) * dtype.itemsize != member.file_size - stream.tell()
                                or math.prod(shape) * dtype.itemsize > MAX_ENTRY_BYTES):
                            raise ValueError("invalid NPY allocation header")
            with np.load(path, allow_pickle=False) as data:
                encoded = data["__manifest__"]
                if encoded.dtype != np.uint8 or encoded.ndim != 1 or encoded.size > MAX_MANIFEST_BYTES:
                    raise ValueError("invalid manifest encoding")
                manifest = json.loads(encoded.tobytes().decode("utf-8"))
                checksum = manifest.pop("manifest_sha256")
                if checksum != _sha(_canonical(manifest)):
                    raise ValueError("manifest digest mismatch")
                if (manifest.get("schema") != CACHE_SCHEMA or manifest.get("key") != self._key(content_sha, kind, view, mirrored)
                        or set(data.files) != set(manifest["arrays"]) | {"__manifest__"}):
                    raise ValueError("manifest key or schema mismatch")
                arrays = {}
                for name, spec in manifest["arrays"].items():
                    value = data[name]
                    if (value.dtype.hasobject or value.dtype.str != spec["dtype"] or list(value.shape) != spec["shape"]
                            or value.nbytes > MAX_ENTRY_BYTES or _array_sha(value) != spec["sha256"]):
                        raise ValueError("array structure or digest mismatch")
                    # np.load creates separate owning arrays, no mmap or global
                    # object table. Preserve writeability expected by the engine.
                    arrays[name] = value
                metadata = manifest["metadata"]
                if not isinstance(metadata, dict):
                    raise ValueError("invalid artifact metadata")
            _count(kind, "bytes_read", stat.st_size, variant=variant)
            _count(kind, "read_seconds", time.perf_counter() - started, variant=variant)
            return arrays, metadata
        except Exception:
            _count(kind, "invalid", variant=variant)
            _count(kind, "fallback", variant=variant)
            _count(kind, "read_seconds", time.perf_counter() - started, variant=variant)
            return None

    def _hit(self, content_sha: str, kind: str, view: str = "gray", mirrored: bool = False) -> None:
        variant = kind + "." + view + (".mirrored" if mirrored else ".normal")
        _count(kind, "hit", variant=variant)
        try:
            os.utime(self.path_for(content_sha, kind, view, mirrored), None)
        except OSError:
            _count(kind, "touch_failure", variant=variant)

    def _invalid_constructor(self, kind: str):
        _count(kind, "invalid")
        _count(kind, "fallback")

    def _put(self, content_sha: str, kind: str, arrays: dict[str, np.ndarray], metadata: dict[str, Any], *, view: str = "gray", mirrored: bool = False) -> None:
        variant = kind + "." + view + (".mirrored" if mirrored else ".normal")
        started = time.perf_counter()
        temporary = None
        if not self.available:
            _count(kind, "write_failure", variant=variant)
            return
        try:
            specs = {name: {"dtype": value.dtype.str, "shape": list(value.shape), "sha256": _array_sha(value)}
                     for name, value in arrays.items()}
            manifest = {"schema": CACHE_SCHEMA, "key": self._key(content_sha, kind, view, mirrored),
                        "writer_cache_sha256": _IMPLEMENTATION_SHA, "metadata": metadata, "arrays": specs}
            manifest["manifest_sha256"] = _sha(_canonical(manifest))
            encoded = _canonical(manifest)
            if len(encoded) > MAX_MANIFEST_BYTES or sum(a.nbytes for a in arrays.values()) + len(encoded) + 16384 > MAX_ENTRY_BYTES:
                _count(kind, "oversize_not_stored", variant=variant)
                return
            with self._maintenance() as acquired:
                if not acquired:
                    _count(kind, "write_contention", variant=variant)
                    _count(kind, "fallback", variant=variant)
                    return
                path = self.path_for(content_sha, kind, view, mirrored)
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.parent.is_symlink() or path.parent.resolve().parent != self.root:
                    raise ValueError("cache namespace must remain inside marked root")
                temporary = path.parent / (".publish-" + uuid.uuid4().hex + ".tmp")
                with temporary.open("xb") as stream:
                    np.savez(stream, __manifest__=np.frombuffer(encoded, np.uint8), **arrays)
                    stream.flush()
                    os.fsync(stream.fileno())
                size = temporary.stat().st_size
                if size > min(MAX_ENTRY_BYTES, self.limit_bytes):
                    _count(kind, "oversize_not_stored", variant=variant)
                    return
                entries = []
                now = time.time()
                for candidate in self._entries():
                    try:
                        stat = candidate.stat()
                        if now - stat.st_mtime > self.ttl_seconds:
                            candidate.unlink()
                            _count("maintenance", "expired_eviction")
                            continue
                        if candidate != path:
                            entries.append((stat.st_mtime, candidate, stat.st_size))
                    except OSError:
                        _count("maintenance", "eviction_failure")
                total = sum(item[2] for item in entries)
                for _, candidate, old_size in sorted(entries):
                    if total + size <= self.limit_bytes:
                        break
                    try:
                        candidate.unlink()
                        total -= old_size
                        _count("maintenance", "eviction")
                    except OSError:
                        _count("maintenance", "eviction_failure")
                if total + size > self.limit_bytes:
                    _count(kind, "quota_not_stored", variant=variant)
                    return
                os.replace(temporary, path)
                temporary = None
                _count(kind, "writes", variant=variant)
                _count(kind, "bytes_written", size, variant=variant)
        except Exception:
            _count(kind, "write_failure", variant=variant)
            _count(kind, "fallback", variant=variant)
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    _count("maintenance", "cleanup_failure")
            _count(kind, "write_seconds", time.perf_counter() - started, variant=variant)

    def stats(self) -> dict[str, Any]:
        try:
            self._verify_root()
            entries = list(self._entries())
            inputs = self.root / ".inputs"
            snapshots = list(inputs.iterdir()) if inputs.is_dir() else []
            return {"root": str(self.root), "namespace": self.namespace, "entries": len(entries),
                    "bytes": sum(p.stat().st_size for p in entries), "limit_bytes": self.limit_bytes,
                    "input_snapshot_files": len(snapshots), "input_snapshot_bytes": sum(p.stat().st_size for p in snapshots if p.is_file()),
                    "snapshot_limit_bytes": self.snapshot_limit_bytes, "snapshot_file_limit": MAX_INPUT_SNAPSHOTS,
                    "ttl_seconds": self.ttl_seconds, "available": self.available}
        except Exception as error:
            return {"root": str(self.root), "available": False, "error": type(error).__name__}

    def clear(self) -> dict[str, Any]:
        """Delete only recognized cache entries, after exact root-marker check."""
        self._verify_root()
        removed = 0
        failures = 0
        with self._maintenance() as acquired:
            if not acquired:
                return {"removed": 0, "busy": True, "failures": 0}
            for path in list(self._entries()):
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    # A reader/writer race only loses an optional cache entry.
                    failures += 1
        return {"removed": removed, "busy": False, "failures": failures}


def _array_sha(value: np.ndarray) -> str:
    """Hash the exact C-order bytes without a whole-array temporary bytes copy.

    Naturally produced/stored arrays are C contiguous. The defensive unusual
    noncontiguous input keeps the old C-order contract with one explicit copy.
    hashlib reads the array buffer synchronously and retains no reference.
    """
    if not value.flags.c_contiguous:
        _count("maintenance", "hash_contiguous_copy_bytes", value.nbytes)
        value = np.ascontiguousarray(value)
    return hashlib.sha256(value).hexdigest()


def _stream_input(path: Path, destination=None, maximum: int | None = None) -> tuple[str, int]:
    """Hash/copy one complete read using a reusable 256 KiB input buffer.

    A cold build is decoded only from a private snapshot whose second-read
    digest/length matches the lookup identity. A changing file therefore cannot
    publish decoded bytes under an unrelated first-read key. Enforce the exact
    pre-reserved length before each write, never temporarily exceed disk quota.
    """
    digest = hashlib.sha256()
    size = 0
    buffer = bytearray(256 * 1024)
    view = memoryview(buffer)
    with path.open("rb") as stream:
        while count := stream.readinto(buffer):
            size += count
            if maximum is not None and size > maximum:
                raise ValueError("input grew beyond its snapshot reservation")
            piece = view[:count]
            digest.update(piece)
            if destination is not None:
                destination.write(piece)
    return digest.hexdigest(), size


class _ResourcePreparedImage(engine.PreparedImage):
    """Private lossless storage for the naturally assigned candidate mask.

    Only _data_mask is eligible: the frozen engine's candidate mask is all
    uint8 ones, and every consumer reads it. Annotation/verification masks,
    pixels, channels and feature arrays retain their original storage contract.
    Each compact mask has its own immutable scalar; nothing is shared between
    images or workers. None remains None until the original engine assigns it.
    """

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_data_mask" and value is not None:
            original = value
            try:
                gray = self.__dict__.get("gray")
                if (type(value) is np.ndarray and type(gray) is np.ndarray
                        and value.dtype == np.uint8 and value.ndim == 2
                        and value.shape == gray.shape and value.size
                        and value.min() == 1 and value.max() == 1):
                    # Only exact ndarrays qualify: subclasses may override
                    # scalar reductions or other array semantics.
                    # min/max are scalar reductions, not a full boolean plane.
                    # The dense ones allocated by the original _mask_for dies
                    # after this natural assignment, before that worker's SIFT.
                    scalar = np.array(1, dtype=np.uint8)
                    scalar.flags.writeable = False
                    compact = np.broadcast_to(scalar, value.shape)
                    value = compact
                    _count("resource", "constant_candidate_mask_compactions")
                    _count("resource", "constant_candidate_mask_logical_bytes", compact.nbytes)
                    _count("resource", "constant_candidate_mask_scalar_bytes", scalar.nbytes)
            except Exception:
                # Optional storage optimization must not reject valid original
                # computation, reinterpret a non-one mask or alter its flags.
                value = original
                _count("resource", "constant_candidate_mask_storage_fallback")
        super().__setattr__(name, value)


def _resource_image(image: engine.PreparedImage) -> engine.PreparedImage:
    """Attach storage semantics in place; retain object/dict/array identities.

    Both classes are ordinary Python heap classes with inherited __dict__ and
    weakref storage and no extra slots. Unsupported custom PreparedImage types
    remain untouched rather than losing their custom behavior.
    """
    if isinstance(image, _ResourcePreparedImage):
        return image
    if type(image) is not engine.PreparedImage:
        _count("resource", "prepared_storage_unsupported_type")
        return image
    try:
        image.__class__ = _ResourcePreparedImage
    except (TypeError, AttributeError):
        _count("resource", "prepared_storage_wrapper_fallback")
    else:
        _count("resource", "prepared_storage_wrapped")
    return image


def _restore_prepare(arrays: dict[str, np.ndarray], metadata: dict[str, Any], path: Path) -> engine.PreparedImage:
    expected = {"rgb", "gray", "annotation_mask", "uncertain_mask", "data_mask", "labels"}
    if set(arrays) != expected or set(metadata) != {"width", "height", "pixel_hash", "scale_x", "scale_y", "information", "annotation_metadata"}:
        raise ValueError("prepare fields mismatch")
    rgb, gray = arrays["rgb"], arrays["gray"]
    if (rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3 or min(rgb.shape[:2]) < 1
            or max(rgb.shape[:2]) > 2000 or gray.dtype != np.uint8 or gray.shape != rgb.shape[:2]):
        raise ValueError("invalid prepared pixel arrays")
    for name in expected - {"rgb", "gray"}:
        if arrays[name].dtype != np.uint8 or arrays[name].shape != gray.shape:
            raise ValueError("invalid annotation array")
    for name in ("annotation_mask", "uncertain_mask", "data_mask"):
        if np.any(arrays[name] > 1):
            raise ValueError("invalid binary annotation mask")
    if np.any(arrays["labels"] > 4):
        raise ValueError("invalid annotation labels")
    width, height = metadata["width"], metadata["height"]
    if (type(width) is not int or type(height) is not int or min(width, height) < 1 or width * height > 80_000_000
            or not isinstance(metadata["pixel_hash"], str) or not _HEX.fullmatch(metadata["pixel_hash"])
            or metadata["scale_x"] != width / gray.shape[1] or metadata["scale_y"] != height / gray.shape[0]
            or set(metadata["information"]) != {"entropy", "standard_deviation", "edge_fraction"}
            or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in metadata["information"].values())):
        raise ValueError("invalid prepared scalar metadata")
    annotation_metadata = metadata["annotation_metadata"]
    if (not isinstance(annotation_metadata, dict) or annotation_metadata.get("analysis_width") != gray.shape[1]
            or annotation_metadata.get("analysis_height") != gray.shape[0]):
        raise ValueError("invalid annotation metadata")
    analysis = AnnotationAnalysis(*(arrays[name] for name in ("annotation_mask", "uncertain_mask", "data_mask", "labels")), annotation_metadata)
    return _resource_image(engine.PreparedImage(str(path.resolve()), width, height, metadata["pixel_hash"], rgb, gray,
                                metadata["scale_x"], metadata["scale_y"], metadata["information"], annotations=analysis))


def _restore_sift(arrays: dict[str, np.ndarray], metadata: dict[str, Any]):
    if set(metadata) != {"descriptors_none"} or type(metadata["descriptors_none"]) is not bool:
        raise ValueError("invalid SIFT metadata")
    expected = {"points"} if metadata["descriptors_none"] else {"points", "descriptors"}
    if set(arrays) != expected:
        raise ValueError("invalid SIFT fields")
    points = arrays["points"]
    if points.dtype != np.float32 or points.ndim != 2 or points.shape[1] != 2 or len(points) > 65536 or not np.isfinite(points).all():
        raise ValueError("invalid SIFT points")
    descriptors = arrays.get("descriptors")
    if descriptors is not None and (descriptors.dtype != np.float32 or descriptors.shape != (len(points), 128) or not np.isfinite(descriptors).all()):
        raise ValueError("invalid SIFT descriptors")
    if descriptors is None and len(points):
        raise ValueError("missing SIFT descriptors")
    return points, descriptors


class UncachedDetector(engine.Detector):
    """True service-off path: inherit every original engine method unchanged.

    The service selects this class once at process startup. No cache/resource
    wrappers, counters or disk operations are installed on this class. The
    original PreparedImage representation, lazy behavior and worker LRU apply.
    """

    pass


class CachedDetector(engine.Detector):
    """Identical computation with optional prepare and naturally lazy SIFT IO."""

    def __init__(self, max_side: int = engine._MAX_SIDE):
        super().__init__(max_side)
        self.cache = None
        if _enabled():
            try:
                self.cache = CacheStore(self.max_side)
            except Exception:
                _count("maintenance", "configuration_failure")

    def _base_prepare(self, path: str | Path) -> engine.PreparedImage:
        started = time.perf_counter()
        result = super().prepare(path)
        # This enabled-service implementation keeps private resource storage
        # even when a root/configuration fails. The completely disabled service
        # selects UncachedDetector instead; do not infer disabled from cache=None.
        result = _resource_image(result)
        _count("prepare", "compute_calls")
        _count("prepare", "annotation_compute_calls")
        _count("prepare", "compute_seconds", time.perf_counter() - started)
        return result

    def prepare(self, path: str | Path) -> engine.PreparedImage:
        if self.cache is None or not self.cache.available:
            return self._base_prepare(path)
        current = Path(path)
        started = time.perf_counter()
        try:
            content_sha, content_size = _stream_input(current)
        except OSError:
            _count("prepare", "identity_fallback")
            # Original invalid/missing inputs must still fail normally.
            return self._base_prepare(path)
        _count("prepare", "hash_seconds", time.perf_counter() - started)
        _count("prepare", "input_bytes_read", content_size)
        loaded = self.cache._load(content_sha, "prepare")
        if loaded is not None:
            try:
                result = _restore_prepare(*loaded, current)
            except Exception:
                self.cache._invalid_constructor("prepare")
                # A rejected but fully loaded NPZ must not coexist with the
                # complete fallback decode/annotation/SIFT workspace.
                loaded = None
            else:
                self.cache._hit(content_sha, "prepare")
                _count("prepare", "annotation_skipped")
                result._feature_cache_identity = (content_sha, self.cache.namespace, "gray")
                return result
        # Decode the exact bytes that were hashed, not a possibly modified path.
        # If the optional snapshot cannot be written, compute the original path
        # without attaching a cache identity and never publish under the old key.
        snapshot = None
        snapshot_started = time.perf_counter()
        reservation = self.cache.reserve_snapshot(content_size, current.suffix)
        if reservation is None:
            _count("prepare", "snapshot_fallback")
            return self._base_prepare(path)
        descriptor, snapshot = reservation
        try:
            # r+b deliberately preserves the full-length reservation while the
            # input bytes are written outside the short allocation lock.
            with os.fdopen(descriptor, "r+b") as stream:
                snapshot_sha, snapshot_size = _stream_input(current, stream, content_size)
                if snapshot_sha != content_sha or snapshot_size != content_size:
                    raise ValueError("input changed between identity and snapshot reads")
        except Exception:
            with contextlib.suppress(OSError):
                os.close(descriptor)
            if snapshot is not None:
                with contextlib.suppress(OSError):
                    Path(snapshot).unlink(missing_ok=True)
            _count("prepare", "snapshot_fallback")
            return self._base_prepare(path)
        _count("prepare", "snapshot_seconds", time.perf_counter() - snapshot_started)
        _count("prepare", "snapshot_bytes_written", content_size)
        try:
            result = self._base_prepare(snapshot)
        finally:
            with contextlib.suppress(OSError):
                Path(snapshot).unlink(missing_ok=True)
        result.path = str(current.resolve())
        result._feature_cache_identity = (content_sha, self.cache.namespace, "gray")
        arrays = {"rgb": result.rgb, "gray": result.gray,
                  **{name: getattr(result.annotations, name) for name in ("annotation_mask", "uncertain_mask", "data_mask", "labels")}}
        metadata = {"width": result.width, "height": result.height, "pixel_hash": result.pixel_hash,
                    "scale_x": result.scale_x, "scale_y": result.scale_y, "information": result.information,
                    "annotation_metadata": result.annotations.metadata}
        self.cache._put(content_sha, "prepare", arrays, metadata)
        return result

    def _features(self, image: engine.PreparedImage, mirrored: bool = False):
        if mirrored in image._features:
            _count("sift", "worker_memory_hit")
            return super()._features(image, mirrored)
        identity = getattr(image, "_feature_cache_identity", None)
        if self.cache is not None and identity is not None and identity[1] == self.cache.namespace:
            content_sha, _, view = identity
            loaded = self.cache._load(content_sha, "sift", view=view, mirrored=mirrored)
            if loaded is not None:
                try:
                    result = _restore_sift(*loaded)
                except Exception:
                    self.cache._invalid_constructor("sift")
                    loaded = None
                else:
                    # Preserve the base lazy call's one-image state side effect.
                    engine._mask_for(image)
                    image._features[mirrored] = result
                    self.cache._hit(content_sha, "sift", view, mirrored)
                    return result
        started = time.perf_counter()
        result = super()._features(image, mirrored)
        _count("sift", "compute_calls")
        _count("sift", "compute_seconds", time.perf_counter() - started)
        if self.cache is not None and identity is not None and identity[1] == self.cache.namespace:
            points, descriptors = result
            arrays = {"points": points}
            if descriptors is not None:
                arrays["descriptors"] = descriptors
            self.cache._put(identity[0], "sift", arrays, {"descriptors_none": descriptors is None}, view=identity[2], mirrored=mirrored)
        return result

    @staticmethod
    def _channel_view(image: engine.PreparedImage, name: str) -> engine.PreparedImage:
        started = time.perf_counter()
        existing = name in image._channels
        result = engine.Detector._channel_view(image, name)
        if isinstance(image, _ResourcePreparedImage):
            result = _resource_image(result)
        identity = getattr(image, "_feature_cache_identity", None)
        if identity is not None:
            result._feature_cache_identity = (identity[0], identity[1], name)
        if not existing:
            _count("channel", "compute_calls")
            _count("channel", "compute_seconds", time.perf_counter() - started)
        return result


_IMPLEMENTATION_SHA = _sha(Path(__file__).read_bytes())


def _main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or safely clear only a marked single-image feature cache.")
    parser.add_argument("command", choices=("stats", "clear"))
    parser.add_argument("--cache-dir", required=True)
    args = parser.parse_args()
    # CLI must never create/adopt a target supplied to clear/stats.
    target = Path(args.cache_dir).resolve()
    marker = target / MARKER_NAME
    if not marker.is_file():
        parser.error("target is not a marked feature-cache directory")
    store = CacheStore(root=target)
    store._verify_root()
    print(json.dumps(store.clear() if args.command == "clear" else store.stats(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _main()
