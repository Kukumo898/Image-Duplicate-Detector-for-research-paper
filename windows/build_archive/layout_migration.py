"""GUI-facing, offline export/restore bridge for the tidy portable layout.

Installed at 程序文件/tools/layout_migration.py. No app/engine changes.
Successful stdout ends with one JSON object; child output is never forwarded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
import uuid

INNER = Path(__file__).resolve().parents[1]
OUTER_NAMES = {"启动图片查重系统.exe", "迁移数据.exe", "使用说明.txt"}
CORE_TOOLS = (
    "daily_backup.py", "daily_common.py", "daily_launcher.py",
    "daily_server.py", "portable_controller.py", "portable_migration.py",
)
FORBIDDEN_ROOTS = {
    "user_data", "backups", "restored_data", "restored_packages",
    "launcher_errors.log", "restore_result.json", "desktop_launcher.log", "migration_entry.log",
}


class MigrationError(RuntimeError):
    pass


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def safe_path(path, *, exists=False):
    # daily_backup checks every existing ancestor before resolve hides aliases.
    from daily_backup import _root
    value = _root(Path(path), must_exist=False)
    if exists and not value.exists():
        raise MigrationError("路径不存在：" + str(value))
    return value


def checked_relative(value):
    if (not isinstance(value, str) or not value or "\\" in value or ":" in value or
            "\x00" in value or any(p in ("", ".", "..") for p in value.split("/"))):
        raise MigrationError("发行清单包含不安全的路径。")
    relative = PurePosixPath(value)
    if relative.is_absolute():
        raise MigrationError("发行清单必须使用相对路径。")
    reserved = {"CON", "PRN", "AUX", "NUL", *("COM" + str(i) for i in range(1, 10)),
                *("LPT" + str(i) for i in range(1, 10))}
    for part in relative.parts:
        if (part.endswith((".", " ")) or part.split(".")[0].upper() in reserved or
                any(ord(c) < 32 or c in '<>"|?*' for c in part)):
            raise MigrationError("发行清单包含无效 Windows 文件名。")
    return Path(*relative.parts)


def load_manifest(path):
    value = read_json(path)
    files = value.get("files") if isinstance(value, dict) else None
    if not isinstance(files, dict) or not files:
        raise MigrationError("发行文件清单缺失或格式错误：" + Path(path).name)
    result = {}
    folded = set()
    for name, spec in files.items():
        checked_relative(name)
        if name.casefold() in folded:
            raise MigrationError("发行清单包含大小写冲突文件。")
        folded.add(name.casefold())
        checksum = spec if isinstance(spec, str) else spec.get("sha256") if isinstance(spec, dict) else None
        size = spec.get("bytes") if isinstance(spec, dict) else None
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
            raise MigrationError("发行清单 SHA-256 无效：" + name)
        if size is not None and (not isinstance(size, int) or size < 0):
            raise MigrationError("发行清单文件大小无效：" + name)
        result[name] = (checksum.lower(), size)
    return result


def verify_file(root, relative, expected):
    root = safe_path(root, exists=True)
    path = safe_path(root / checked_relative(relative), exists=True)
    if not path.is_relative_to(root) or not path.is_file():
        raise MigrationError("发行文件越界或不是普通文件：" + relative)
    checksum, size = expected
    if size is not None and path.stat().st_size != size:
        raise MigrationError("发行文件大小校验失败：" + relative)
    if digest(path) != checksum:
        raise MigrationError("发行文件校验失败：" + relative)
    return path


def source_package(path=None):
    from daily_backup import _source_manifest
    source = safe_path(path or INNER, exists=True)
    if (source / "程序文件" / "tools" / "daily_launcher.py").is_file():
        source = safe_path(source / "程序文件", exists=True)
    if not (source / "tools" / "daily_launcher.py").is_file():
        raise MigrationError("请选择图片查重系统便携版文件夹或其“程序文件”目录。")
    if _source_manifest(source) != _source_manifest(INNER):
        raise MigrationError("所选旧版的应用源码与本版不一致，不能直接迁移；请使用原版备份恢复。")
    for name in CORE_TOOLS:
        old = safe_path(source / "tools" / name, exists=True)
        current = safe_path(INNER / "tools" / name, exists=True)
        if digest(old) != digest(current):
            raise MigrationError("所选旧版的安全控制工具不兼容：" + name)
    # The supplied interpreter is executed only after matching this package's
    # native entry points; source control code is independently matched above.
    for name in ("python.exe", "python314.dll", "python3.dll", "python314._pth"):
        old = safe_path(source / "runtime" / "python" / name, exists=True)
        current = safe_path(INNER / "runtime" / "python" / name, exists=True)
        if digest(old) != digest(current):
            raise MigrationError("所选旧版的随包运行环境不兼容：" + name)
    profile = read_json(source / "daily_profile.json")
    if profile.get("runtime") != "user_data":
        raise MigrationError("仅迁移包内 user_data，不接管外部运行库。")
    runtime = safe_path(source / "user_data", exists=True)
    if not (runtime / "data" / "labimage.sqlite3").is_file():
        raise MigrationError("所选软件尚无可迁移数据库；请先使用软件生成资料。")
    return source, runtime


def safe_child_error(output):
    # Status/identity data must not leak credentials into GUI/stdout/logs.
    lines = [line for line in output.splitlines()
             if not re.search(r"token|secret|authorization|session", line, re.I)]
    preferred = [line for line in lines if "操作未完成" in line or "RuntimeError:" in line]
    return "\n".join(preferred[-2:] or lines[-3:])[:1200]


def run_launcher(source, action):
    command = [str(source / "runtime" / "python" / "python.exe"), "-B", "-X", "utf8",
               str(source / "tools" / "daily_launcher.py"), action]
    environment = {k: v for k, v in os.environ.items()
                   if not k.upper().startswith(("PYTHON", "LABIMAGE_"))}
    environment["LABIMAGE_NO_BROWSER"] = "1"
    for attempt in range(2):
        try:
            completed = subprocess.run(command, cwd=str(source), env=environment,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=600)
        except subprocess.TimeoutExpired as exc:
            raise MigrationError("迁移操作超时；未强制停止服务，请检查当前图片查重系统状态。") from exc
        output = completed.stdout + "\n" + completed.stderr
        if completed.returncode == 0:
            return
        # One revalidation retry only for the observed Windows permission race;
        # busy/state/ownership failures are not swallowed or retried.
        if action == "stop" and attempt == 0 and "WinError 5" in output:
            time.sleep(0.3)
            source_package(source)
            continue
        raise MigrationError("无法安全" + ("关闭图片查重系统" if action == "stop" else "备份资料") +
                             "：\n" + safe_child_error(output))


def export_data(output, source=None):
    source, runtime = source_package(source)
    target = safe_path(output)
    if target.suffix.lower() != ".zip":
        raise MigrationError("导出文件请使用 .zip 扩展名。")
    if target.exists():
        raise MigrationError("导出文件已经存在，不会覆盖：" + str(target))
    if target.is_relative_to(runtime):
        raise MigrationError("导出 ZIP 不能放入正在备份的 user_data。")
    target.parent.mkdir(parents=True, exist_ok=True)
    run_launcher(source, "stop")
    run_launcher(source, "backup")
    record = read_json(runtime / "records" / "last_backup.json")
    archive = safe_path(record.get("archive", ""), exists=True)
    if (not archive.is_relative_to(source / "backups") or not archive.is_file() or
            digest(archive) != record.get("sha256") or archive.stat().st_size != record.get("bytes")):
        raise MigrationError("刚生成的备份路径或校验信息不一致；未导出。")
    created = False
    try:
        with target.open("xb") as out, archive.open("rb") as inp:
            created = True
            shutil.copyfileobj(inp, out, 1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        if digest(target) != record["sha256"]:
            raise MigrationError("导出复制校验失败。")
    except Exception:
        if created:
            target.unlink(missing_ok=True)
        raise
    return {"ok": True, "action": "export", "archive": str(target),
            "sha256": record["sha256"], "bytes": record["bytes"],
            "source_package": str(source), "database": record.get("database"),
            "source_stopped": True, "source_backup_retained": True}


def import_data(archive, target):
    from daily_backup import restore, _tree
    from daily_common import MAGIC, write_json
    inner = safe_path(INNER, exists=True)
    outer = inner.parent
    archive = safe_path(archive, exists=True)
    target = safe_path(target)
    if target == outer or target.is_relative_to(outer):
        raise MigrationError("恢复目标不能是当前软件目录或其子目录。")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise MigrationError("恢复只允许全新空目录，不覆盖或合并现有资料：" + str(target))
    files = load_manifest(inner / "file_manifest.json")
    outer_files = load_manifest(inner / "outer_manifest.json")
    if set(outer_files) != OUTER_NAMES:
        raise MigrationError("外层入口清单不完整。")
    required = {"tools/layout_migration.py", "tools/daily_backup.py", "tools/daily_launcher.py",
                "runtime/python/python.exe", "图鉴.exe", "daily_profile.json", "source_manifest.json"}
    if not required.issubset(files):
        raise MigrationError("发行文件清单缺少必要程序组件。")
    for name, expected in files.items():
        parts = checked_relative(name).parts
        # Runtime wheels legitimately ship nested dotfiles/directories, e.g.
        # fastapi/.agents and numpy/.f2py_f2cmap. Their exact bytes are still
        # bound by the immutable file manifest; do not confuse those bundled
        # resources with root-level runtime identity or private user state.
        hidden_root = parts[0].startswith(".")
        hidden_outside_runtime = (parts[0].casefold() != "runtime" and
                                  any(part.startswith(".") for part in parts[1:]))
        if parts[0].casefold() in FORBIDDEN_ROOTS or hidden_root or hidden_outside_runtime:
            raise MigrationError("发行文件清单包含用户资料或隐藏状态文件。")
        if name == "file_manifest.json":
            raise MigrationError("文件清单不能要求自身哈希。")
        verify_file(inner, name, expected)
    for name, expected in outer_files.items():
        verify_file(outer, name, expected)
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.parent / ("." + target.name + ".layout-restore-" + uuid.uuid4().hex)
    stage.mkdir()
    try:
        staging_inner = stage / "程序文件"
        staging_inner.mkdir()
        for name, expected in files.items():
            source = verify_file(inner, name, expected)
            destination = staging_inner / checked_relative(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if digest(destination) != expected[0]:
                raise MigrationError("复制后的发行文件校验失败：" + name)
        for name in ("file_manifest.json", "outer_manifest.json"):
            if name not in files:
                shutil.copyfile(inner / name, staging_inner / name)
        for name, expected in outer_files.items():
            shutil.copyfile(verify_file(outer, name, expected), stage / name)
            if digest(stage / name) != expected[0]:
                raise MigrationError("复制后的入口校验失败：" + name)
        final_inner = target / "程序文件"
        final_runtime = final_inner / "user_data"
        staged_runtime = staging_inner / "user_data"
        evidence = restore(archive, staged_runtime, inner, _final_runtime=final_runtime)
        info = staged_runtime.stat()
        write_json(staged_runtime / ".daily-runtime.json", {
            "magic": MAGIC, "runtime": str(final_runtime), "release": str(final_inner),
            "id": uuid.uuid4().hex, "directory_identity": [info.st_dev, info.st_ino],
            "restored": True, "portable": True,
        })
        result = {"ok": True, "action": "import", "package": str(target),
                  "entry": str(target / "启动图片查重系统.exe"), "runtime": str(final_runtime),
                  "archive_sha256": evidence["archive_sha256"], "database": evidence["database"],
                  "standalone_package": True, "cache_inherited": False, "sessions_inherited": False}
        write_json(staged_runtime / "layout_restore_evidence.json", result)
        # Both locations remain on one volume; final publication is a rename.
        # Recheck the empty destination immediately before publishing.
        safe_path(target)
        if target.exists():
            if any(target.iterdir()):
                raise MigrationError("恢复目录在操作中变为非空；没有覆盖任何资料。")
            target.rmdir()
        stage.rename(target)
        return result
    finally:
        if stage.exists():
            if stage.parent != target.parent or not stage.name.startswith("." + target.name + ".layout-restore-"):
                raise MigrationError("临时目录身份不符，拒绝清理。")
            _tree(stage)  # Never follow reparse points during scoped cleanup.
            shutil.rmtree(stage)


def main():
    parser = argparse.ArgumentParser(description="图片查重系统资料迁移（不覆盖旧资料）")
    commands = parser.add_subparsers(dest="command", required=True)
    exp = commands.add_parser("export")
    exp.add_argument("--output", required=True)
    exp.add_argument("--source-package")
    imp = commands.add_parser("import")
    imp.add_argument("--archive", required=True)
    imp.add_argument("--target", required=True)
    args = parser.parse_args()
    if args.command == "export":
        result = export_data(args.output, args.source_package)
    else:
        result = import_data(args.archive, args.target)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("迁移未完成：" + safe_child_error(str(error)), file=sys.stderr)
        sys.exit(1)
