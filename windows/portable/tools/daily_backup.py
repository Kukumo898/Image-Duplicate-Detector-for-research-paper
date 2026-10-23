"""Offline, checksummed backups for the daily-use wrapper (stdlib only).

The caller MUST hold the runtime operation lock and confirm its service lifetime
lock is free for the entire call.  No process is stopped and no existing library
is overwritten here.  Cache/session identities are deliberately not copied.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

FORMAT = "labimage-offline-backup-v1"
DB_NAME = "labimage.sqlite3"
JSON_COLUMNS = {
    "jobs": ("uploads", "settings", "warnings", "diagnostics"),
    "images": ("bbox", "metadata", "annotations"),
    "findings": ("result",),
    "pair_diagnostics": ("diagnostic",),
}
PATH_KEYS = {"path", "file", "file_path", "source_path", "directory", "data_root", "data_directory"}


class BackupError(RuntimeError):
    """A backup/restore cannot be safely completed; message is user-readable."""


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _plain(path):
    info = Path(path).lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise BackupError(f"拒绝符号链接或 Windows 重解析点：{path}")
    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
        raise BackupError(f"不支持的文件类型：{path}")


def _root(path, must_exist=True):
    path = Path(os.path.abspath(path))
    # Reject aliases before resolve() could hide their identity.
    for ancestor in reversed((path, *path.parents)):
        if ancestor.exists() or ancestor.is_symlink():
            _plain(ancestor)
    if must_exist and not path.is_dir():
        raise BackupError(f"目录不存在：{path}")
    return path.resolve()


def _tree(root):
    files, directories = [], []
    def visit(folder):
        _plain(folder)
        directories.append(folder.relative_to(root).as_posix())
        for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
            _plain(path)
            if path.is_dir():
                visit(path)
            else:
                files.append(path)
    visit(root)
    return files, directories


def _source_root(release):
    release = _root(release)
    source = release / "release_source" if (release / "release_source").is_dir() else release
    if not (source / "app" / "main.py").is_file():
        raise BackupError(f"找不到本交付的应用源码：{source}")
    return source


def _source_manifest(release):
    source = _source_root(release)
    paths = sorted((source / "app").glob("*.py"))
    if (source / "requirements-lock.txt").is_file():
        paths.append(source / "requirements-lock.txt")
    result = {}
    for path in paths:
        _plain(path)
        result[path.relative_to(source).as_posix()] = _sha(path)
    return result


def _pure(value):
    # A backup is local Windows data, but this also keeps POSIX test fixtures and
    # explicitly recorded roots unambiguous without depending on cwd.
    return PureWindowsPath(value) if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\") else PurePosixPath(value)


def _relative(value, old_root):
    if not isinstance(value, str):
        raise BackupError("数据库文件路径不是字符串")
    stored, root = _pure(value), _pure(str(old_root))
    if not stored.is_absolute() or ".." in stored.parts:
        raise BackupError(f"数据库含不明确的相对路径或父目录跳转：{value}")
    try:
        rel = stored.relative_to(root)
    except (ValueError, TypeError):
        raise BackupError(f"数据库引用数据目录以外的外部文件，无法独立恢复：{value}") from None
    if not rel.parts:
        raise BackupError(f"数据库文件路径错误地指向数据根目录：{value}")
    return Path(*rel.parts)


def _local_file(value, old_root, physical_root):
    rel = _relative(value, old_root)
    path = Path(physical_root) / rel
    if not path.is_file():
        raise BackupError(f"数据库引用文件缺失：{path}")
    _plain(path)
    if not path.resolve().is_relative_to(Path(physical_root).resolve()):
        raise BackupError(f"数据库文件引用超出数据根目录：{path}")
    return path


def _json_paths(value, old_root, physical_root, new_root=None, key=""):
    if isinstance(value, dict):
        return {k: _json_paths(v, old_root, physical_root, new_root, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_paths(v, old_root, physical_root, new_root, key) for v in value]
    if not isinstance(value, str):
        return value
    path_key = key.lower() in PATH_KEYS or key.lower().endswith(("_path", "_directory"))
    windows_absolute = bool(re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\"))
    # /api/... is an application URL, not a POSIX file dependency.
    if value.startswith(("/api/", "http://", "https://")):
        return value
    old = _pure(str(old_root))
    parsed = _pure(value)
    is_local = False
    if parsed.is_absolute():
        try:
            parsed.relative_to(old)
            is_local = True
        except (ValueError, TypeError):
            pass
    # In engine evidence, ``path`` is a computation-route tag such as
    # "global_gray" or "local_geometry", NOT a filesystem field.  Only absolute
    # paths may be inferred from generic JSON.  Actual persisted file fields
    # (images.path and each jobs.uploads[].path) are independently and strictly
    # checked by _verify_db, including rejecting relative/missing paths.
    if windows_absolute or is_local or (path_key and parsed.is_absolute()):
        path = _local_file(value, old_root, physical_root)
        return str(Path(new_root) / path.relative_to(physical_root)) if new_root else value
    return value


def _verify_db(path, old_root, physical_root, new_root=None):
    """Verify all persisted path-bearing columns; optionally rebind the copy."""
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    remapped = 0
    try:
        integrity = [r[0] for r in con.execute("PRAGMA integrity_check")]
        if integrity != ["ok"]:
            raise BackupError(f"SQLite 完整性检查失败：{integrity}")
        foreign = list(con.execute("PRAGMA foreign_key_check"))
        if foreign:
            raise BackupError(f"SQLite 外键检查失败：{[tuple(x) for x in foreign[:10]]}")
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"jobs", "images", "findings"}.issubset(tables):
            raise BackupError("备份数据库缺少 jobs/images/findings，非本应用运行库")
        active = list(con.execute("SELECT id,status FROM jobs WHERE status IN ('queued','running','cancelling')"))
        if active:
            raise BackupError("数据库仍有活动或未完成任务；请先完成或取消后再离线备份")
        count = {}
        for table in ("jobs", "images", "findings", "pair_diagnostics"):
            if table not in tables:
                continue
            count[table] = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            columns = {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
            for row in con.execute(f'SELECT rowid AS _backup_rowid_, * FROM "{table}"').fetchall():
                updates = {}
                if table == "images":
                    image = _local_file(row["path"], old_root, physical_root)
                    if new_root:
                        updates["path"] = str(Path(new_root) / image.relative_to(physical_root))
                    thumbnail = Path(physical_root) / "thumbnails" / f'{row["id"]}.jpg'
                    if not thumbnail.is_file():
                        raise BackupError(f"图片缩略图缺失：{thumbnail}")
                for column in JSON_COLUMNS.get(table, ()):
                    if column not in columns or row[column] is None or row[column] == "":
                        continue
                    try:
                        decoded = json.loads(row[column])
                    except (TypeError, ValueError) as error:
                        raise BackupError(f"数据库 JSON 无效：{table}.{column}：{error}") from error
                    rebound = _json_paths(decoded, old_root, physical_root, new_root)
                    if table == "jobs" and column == "uploads":
                        if not isinstance(decoded, list):
                            raise BackupError("jobs.uploads 不是来源文件清单")
                        for upload in decoded:
                            source = _local_file(upload.get("path"), old_root, physical_root)
                            if upload.get("sha256") and _sha(source) != upload["sha256"]:
                                raise BackupError(f"原始上传文件哈希不符：{source}")
                            if upload.get("bytes") is not None and source.stat().st_size != upload["bytes"]:
                                raise BackupError(f"原始上传文件大小不符：{source}")
                    if rebound != decoded:
                        updates[column] = json.dumps(rebound, ensure_ascii=False, separators=(",", ":"))
                if updates:
                    sql = f'UPDATE "{table}" SET ' + ",".join(f'"{column}"=?' for column in updates) + " WHERE rowid=?"
                    con.execute(sql, [*updates.values(), row["_backup_rowid_"]])
                    remapped += 1
        con.commit()
        # Produce a standalone file after rewriting rather than leaving new WAL.
        con.execute("PRAGMA journal_mode=DELETE")
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or list(con.execute("PRAGMA foreign_key_check")):
            raise BackupError("路径重绑定后的数据库一致性检查失败")
        return {"integrity_check": "ok", "foreign_key_check": "ok", "rows": count, "rebound_rows": remapped}
    finally:
        con.close()


def backup(runtime: Path, output_dir: Path, release: Path) -> dict:
    """Create offline ZIP + embedded hash manifest; caller owns lifecycle locks."""
    runtime = _root(runtime)
    data = _root(runtime / "data")
    if not (data / DB_NAME).is_file():
        raise BackupError("本机尚无数据库；请先启动图鉴初始化，再关闭后备份。")
    files, directories = _tree(data)
    output_dir = _root(output_dir, must_exist=False)
    if output_dir.is_relative_to(data):
        raise BackupError("备份输出目录不能放在被备份的 data 内")
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact = output_dir / ("图鉴离线备份_" + datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8] + ".zip")
    partial = artifact.with_suffix(".zip.partial")
    try:
        with tempfile.TemporaryDirectory(prefix="labimage-backup-") as temp:
            snapshot = Path(temp) / DB_NAME
            # SQLite's backup API reads both the database and committed WAL.
            source = sqlite3.connect((data / DB_NAME).as_uri() + "?mode=ro", uri=True)
            dest = sqlite3.connect(snapshot)
            try:
                source.backup(dest)
            finally:
                dest.close()
                source.close()
            database = _verify_db(snapshot, data, data)
            entries = {}
            with zipfile.ZipFile(partial, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
                for path in files:
                    if path.name in (DB_NAME, DB_NAME + "-wal", DB_NAME + "-shm", DB_NAME + "-journal") and path.parent == data:
                        continue
                    name = "data/" + path.relative_to(data).as_posix()
                    entries[name] = {"bytes": path.stat().st_size, "sha256": _sha(path)}
                    archive.write(path, name)
                name = "data/" + DB_NAME
                entries[name] = {"bytes": snapshot.stat().st_size, "sha256": _sha(snapshot)}
                archive.write(snapshot, name)
                manifest = {
                    "format": FORMAT, "created_at": _utc(), "old_data_root": str(data),
                    "database": database, "files": entries,
                    "directories": ["data" if name == "." else "data/" + name for name in directories],
                    "source_files": _source_manifest(release),
                    "portable_settings": __import__('portable_migration').portable_settings(runtime),
                    "restore_config": {"data_subdirectory": "data", "sqlite_filename": DB_NAME,
                                       "single_process": True, "cache_included": False, "cache_rebuildable": True,
                                       "new_session_required": True},
                    "included": ["数据库一致性快照", "原始上传与来源", "解码图和面板", "缩略图", "遮罩及标注元数据", "任务设置及复核记录", "报告重建所需数据"],
                    "excluded": ["可重建缓存", "进程与会话身份", "日志", "与图鉴无关的外部原始实验资料"],
                }
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
        partial.replace(artifact)
        return {"archive": str(artifact), "sha256": _sha(artifact), "bytes": artifact.stat().st_size,
                "files_count": len(manifest["files"]), "database": database, "cache_included": False}
    except Exception:
        partial.unlink(missing_ok=True)
        raise


def _member(name):
    if not isinstance(name, str) or not name or "\\" in name or ":" in name or "\x00" in name:
        raise BackupError(f"备份包含不安全的文件名：{name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("..", ".", "") for part in name.split("/")):
        raise BackupError(f"备份包含目录跳转：{name}")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}
    for part in path.parts:
        if part.endswith((".", " ")) or part.split(".")[0].upper() in reserved or any(ord(c) < 32 or c in '<>"|?*' for c in part):
            raise BackupError(f"备份文件名不能安全还原到 Windows：{name}")
    if path.parts[0] != "data":
        raise BackupError(f"备份包含非数据文件：{name}")
    return Path(*path.parts)


def restore(archive: Path, target_runtime: Path, release: Path, *, _final_runtime=None) -> dict:
    """Restore only to a NEW EMPTY runtime; rewrite all persisted local paths."""
    archive = Path(archive).resolve(strict=True)
    _plain(archive)
    target = _root(target_runtime, must_exist=False)
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise BackupError(f"恢复仅允许新的空目录，拒绝覆盖：{target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / ("." + target.name + ".restore-" + uuid.uuid4().hex)
    staging.mkdir()
    try:
        with zipfile.ZipFile(archive) as zipped:
            infos = zipped.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(n.casefold() for n in names)) or names.count("manifest.json") != 1:
                raise BackupError("备份包有重复文件或缺少唯一 manifest.json")
            if zipped.getinfo("manifest.json").file_size > 16 * 1024 * 1024:
                raise BackupError("备份清单异常过大")
            try:
                manifest = json.loads(zipped.read("manifest.json"))
            except (ValueError, UnicodeError) as error:
                raise BackupError(f"备份清单无效：{error}") from error
            if manifest.get("format") != FORMAT or manifest.get("restore_config", {}).get("sqlite_filename") != DB_NAME:
                raise BackupError("不支持的备份格式或配置")
            if manifest.get("source_files") != _source_manifest(release):
                raise BackupError("备份的应用源码版本与当前交付不一致；请使用生成备份的同版交付恢复")
            entries = manifest.get("files")
            if not isinstance(entries, dict) or set(names) != {"manifest.json", *entries} or "data/" + DB_NAME not in entries:
                raise BackupError("备份包文件与清单不一致")
            for directory in manifest.get("directories", []):
                (staging / _member(directory)).mkdir(parents=True, exist_ok=True)
            for name, expected in entries.items():
                rel = _member(name)
                info = zipped.getinfo(name)
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode) or info.is_dir() or not isinstance(expected, dict):
                    raise BackupError(f"备份包文件类型错误：{name}")
                if info.file_size != expected.get("bytes"):
                    raise BackupError(f"备份包文件大小不符：{name}")
                destination = staging / rel
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest, size = hashlib.sha256(), 0
                with zipped.open(info) as source, destination.open("xb") as out:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        size += len(chunk)
                        if size > info.file_size:
                            raise BackupError(f"备份包解压大小异常：{name}")
                        digest.update(chunk)
                        out.write(chunk)
                if size != expected["bytes"] or digest.hexdigest() != expected.get("sha256"):
                    raise BackupError(f"备份包文件哈希不符：{name}")
        physical = staging / "data"
        final_runtime = Path(_final_runtime) if _final_runtime is not None else target
        restored_data = final_runtime / "data"
        old_data = manifest.get("old_data_root")
        if not isinstance(old_data, str) or not _pure(old_data).is_absolute():
            raise BackupError("备份清单没有明确旧数据根目录")
        database = _verify_db(physical / DB_NAME, old_data, physical, restored_data)
        metadata_count = 0
        for metadata in (physical / "annotations").glob("*/metadata.json"):
            decoded = json.loads(metadata.read_text(encoding="utf-8"))
            rebound = _json_paths(decoded, old_data, physical, restored_data)
            if rebound != decoded:
                metadata.write_text(json.dumps(rebound, ensure_ascii=False, indent=2), encoding="utf-8")
                metadata_count += 1
        # Validate references *against the extracted copy*, never the old root.
        validated = _verify_db(physical / DB_NAME, restored_data, physical)
        evidence = {"archive": str(archive), "archive_sha256": _sha(archive), "restored_at": _utc(),
                    "old_data_root": old_data, "new_data_root": str(restored_data),
                    "database": database, "post_rebind_validation": validated,
                    "rebound_metadata_files": metadata_count, "verified_archive_files": len(entries),
                    "cache_included": False, "new_session_required": True}
        preferences = manifest.get('portable_settings', {'schema': 'labimage-portable-settings-v1', 'preferred_mode': 'accelerated'})
        if (not isinstance(preferences, dict) or set(preferences) != {'schema','preferred_mode'} or
                preferences.get('schema') != 'labimage-portable-settings-v1' or
                preferences.get('preferred_mode') not in ('accelerated','compatible')):
            raise BackupError('备份模式偏好无效；未创建恢复目录')
        (staging / 'settings.json').write_text(json.dumps(preferences, ensure_ascii=False, indent=2), encoding='utf-8')
        evidence['settings'] = preferences
        (staging / "restore_evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        if target.exists():
            if any(target.iterdir()):
                raise BackupError("恢复目录在操作中变为非空；未覆盖任何已有内容")
            target.rmdir()
        staging.replace(target)
        return evidence | {"runtime": str(final_runtime), "data_directory": str(restored_data)}
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def restore_package(archive: Path, target_package: Path, release: Path) -> dict:
    """Restore into a NEW standalone copy, never an old absolute BAT target.

    The source distribution is copied without user data, caches, sessions, logs
    or other restored copies. Publication happens only after archive/path/DB
    checks have all succeeded. The bundled runtime is carried with the copy.
    """
    from daily_common import MAGIC, write_json
    release = _root(release)
    target = _root(target_package, must_exist=False)
    if target == release or target.is_relative_to(release / 'user_data'):
        raise BackupError('恢复目标不能覆盖当前包或放入当前用户资料目录')
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise BackupError('恢复仅允许新的空软件目录，不覆盖任何已有文件：' + str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / ('.' + target.name + '.package-restore-' + uuid.uuid4().hex)
    stage.mkdir()
    exclusions = {'user_data', 'backups', 'restored_data', 'restored_packages', 'launcher_errors.log', 'restore_result.json'}
    try:
        copied = []
        for source in release.iterdir():
            if source.name in exclusions or source.name.startswith('.') or source.resolve() in (target, stage):
                continue
            # Only distribution components belong in an independently restored
            # package; unknown user-created folders are not silently copied.
            if source.is_dir() and source.name.lower() not in {'release_source', 'tools', 'runtime', 'licenses'}:
                continue
            _plain(source)
            destination = stage / source.name
            if source.is_dir():
                _tree(source)
                shutil.copytree(source, destination, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
            else:
                shutil.copy2(source, destination)
            copied.append(source.name)
        if not (stage / '启动图鉴.bat').is_file() or not any((stage / p).is_file() for p in ('runtime/python/python.exe','runtime/python.exe')):
            raise BackupError('当前发行包缺少唯一启动入口或随包 Python；未交付不完整的恢复副本')
        runtime = stage / 'user_data'
        final_runtime = target / 'user_data'
        result = restore(archive, runtime, release, _final_runtime=final_runtime)
        info = runtime.stat()
        write_json(runtime / '.daily-runtime.json', {
            'magic': MAGIC, 'runtime': str(final_runtime), 'release': str(target), 'id': uuid.uuid4().hex,
            'directory_identity': [info.st_dev, info.st_ino], 'restored': True, 'portable': True})
        evidence = result | {'package': str(target), 'entry': str(target / '启动图鉴.bat'),
                             'standalone_package': True, 'distribution_components': sorted(copied)}
        write_json(stage / 'restore_result.json', evidence)
        if target.exists():
            if any(target.iterdir()):
                raise BackupError('恢复目标在操作中变为非空；没有覆盖已有资料')
            target.rmdir()
        stage.replace(target)
        return evidence
    finally:
        if stage.exists():
            _tree(stage)
            shutil.rmtree(stage)
