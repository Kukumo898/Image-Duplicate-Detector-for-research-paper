"""Offline whole-folder relocation, using verified paths and rollback staging.

This module never inspects, contacts or controls an old location's processes.
Only a copied, normally stopped portable runtime can be relocated. The optional
feature-cache directory and all process/control identities are not carried over.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
import uuid
from pathlib import Path

SETTINGS_SCHEMA = 'labimage-portable-settings-v1'
JOURNAL_SCHEMA = 'labimage-portable-relocation-v1'


def portable_settings(runtime):
    path = Path(runtime) / 'settings.json'
    if not path.exists():
        return {'schema': SETTINGS_SCHEMA, 'preferred_mode': 'accelerated'}
    from daily_common import read_json, no_reparse
    no_reparse(path)
    value = read_json(path)
    if (not isinstance(value, dict) or set(value) != {'schema', 'preferred_mode'} or
            value.get('schema') != SETTINGS_SCHEMA or
            value.get('preferred_mode') not in ('accelerated', 'compatible')):
        raise RuntimeError('模式偏好文件无效；未以默认选择覆盖原文件。')
    return value


def _delete_owned(path, release, prefix):
    from daily_backup import _tree
    path = Path(path)
    if path.parent != release or not path.name.startswith(prefix) or path == release:
        raise RuntimeError('迁移临时目录范围校验失败；没有删除任何文件。')
    if path.exists():
        _tree(path)  # reject reparse points before recursive cleanup
        shutil.rmtree(path)


def recover_relocation(runtime):
    """Recover only the tightly scoped two-rename transaction of this package."""
    from daily_common import RELEASE, read_json, write_json
    journal = RELEASE / '.portable-migration.json'
    if not journal.exists():
        return
    value = read_json(journal)
    if (value.get('schema') != JOURNAL_SCHEMA or value.get('runtime') != str(runtime) or
            not isinstance(value.get('transaction_id'), str) or
            len(value['transaction_id']) != 32 or any(c not in '0123456789abcdef' for c in value['transaction_id'])):
        raise RuntimeError('迁移恢复日志身份无效；请保留目录供维护人检查。')
    sid = value['transaction_id']
    stage = RELEASE / ('.' + runtime.name + '.migrating-' + sid)
    previous = RELEASE / ('.' + runtime.name + '.previous-' + sid)
    committed = False
    if runtime.exists() and (runtime / '.daily-runtime.json').exists():
        committed = read_json(runtime / '.daily-runtime.json').get('id') == value['new_marker_id']
    if committed:
        _delete_owned(stage, RELEASE, '.' + runtime.name + '.migrating-')
        _delete_owned(previous, RELEASE, '.' + runtime.name + '.previous-')
    else:
        if previous.exists():
            if runtime.exists():
                raise RuntimeError('迁移恢复遇到两个无法确认的资料根；未覆盖任何资料。')
            previous.replace(runtime)
        if not runtime.exists():
            raise RuntimeError('迁移原资料根缺失；未创建空库，请保留迁移暂存目录。')
        _delete_owned(stage, RELEASE, '.' + runtime.name + '.migrating-')
    journal.unlink()


def _normal_shutdown(runtime, marker):
    from daily_common import read_json
    records = runtime / 'records'
    controller = records / 'controller.current.json'
    if controller.exists():
        ctrl = read_json(controller)
        csid = ctrl.get('controller_id', '')
        if (not isinstance(csid, str) or len(csid) != 32 or any(c not in '0123456789abcdef' for c in csid) or
                ctrl.get('schema') != 'labimage-portable-controller-v1' or
                ctrl.get('runtime') != marker['runtime'] or ctrl.get('release') != marker['release']):
            raise RuntimeError('复制的控制器身份无效；未接管旧实例。')
        ctrl_exit = records / (csid + '.controller-exit.json')
        if not ctrl_exit.is_file():
            raise RuntimeError('控制入口没有正常退出凭据；请在原位置通过“关闭图鉴”完整退出后再复制。')
        ended = read_json(ctrl_exit)
        if (ended.get('normal_exit') is not True or
                ended.get('process_id') != ctrl.get('service', {}).get('process_id') or
                not isinstance(ended.get('ended_at'), (float, int)) or
                ended['ended_at'] < ctrl.get('started_at', ctrl.get('requested_at', 0))):
            raise RuntimeError('控制器正常退出凭据校验失败；没有接管旧实例。')
    current = records / 'current.json'
    if not current.exists():
        # A restored backup has no process identity by design. A never-started
        # runtime has no DB. All other used runtimes must carry an exit receipt.
        if (runtime / 'data' / 'labimage.sqlite3').exists() and not (runtime / 'restore_evidence.json').is_file():
            raise RuntimeError('迁移缺少最后一次正常退出凭据；请在原位置正常关闭，再整体复制。')
        return
    rec = read_json(current)
    sid = rec.get('session_id', '')
    if (not isinstance(sid, str) or len(sid) != 32 or any(c not in '0123456789abcdef' for c in sid) or
            rec.get('schema') != 'labimage-daily-session-v1' or
            rec.get('runtime') != marker['runtime'] or rec.get('release') != marker['release']):
        raise RuntimeError('复制的会话与旧资料根身份不一致；拒绝迁移。')
    exit_path = records / (sid + '.exit.json')
    if not exit_path.is_file():
        raise RuntimeError('原软件没有正常关闭的凭据。不要复制运行中的资料；请先在原位置双击“关闭图鉴”，再重新复制整个文件夹。')
    ended = read_json(exit_path)
    if (ended.get('pid') != rec.get('service', {}).get('process_id') or
            not isinstance(ended.get('ended_at'), (float, int)) or
            ended['ended_at'] < rec.get('started_at', rec.get('requested_at', 0)) or
            'WAL checkpoint' not in str(ended.get('method', ''))):
        raise RuntimeError('正常退出凭据未通过校验；未接管旧会话。')


def relocate_runtime(runtime, marker):
    """Prepare a fresh identity and atomically replace only the copied runtime."""
    from daily_common import (RELEASE, MAGIC, file_lock, service_locked, no_reparse,
                              read_json, write_json)
    from daily_backup import _root, _tree, _verify_db, _json_paths
    runtime = no_reparse(runtime)
    if runtime.parent != RELEASE or runtime.name != 'user_data':
        raise RuntimeError('自动迁移仅用于完整便携包内的 user_data；不接管外部数据目录。')
    if marker.get('portable') is not True or marker.get('magic') != MAGIC:
        raise RuntimeError('非本便携版数据身份；不会自动迁移或合并旧版真实库。')
    old_runtime, old_release = Path(marker.get('runtime', '')), Path(marker.get('release', ''))
    if (not old_runtime.is_absolute() or not old_release.is_absolute() or
            old_runtime.parent != old_release or old_runtime.name != runtime.name):
        raise RuntimeError('旧资料根与软件根布局不一致；拒绝路径猜测。')
    if old_runtime == runtime and old_release == RELEASE:
        raise RuntimeError('数据目录已被替换而不是整包换路径；拒绝收养不同身份。')
    if not isinstance(marker.get('directory_identity'), list) or len(marker['directory_identity']) != 2:
        raise RuntimeError('旧资料根目录身份字段无效。')
    with file_lock(runtime / 'operation.lock', timeout=3), file_lock(runtime / 'service.lock'):
        # Locks are against the NEW copied directory only, never the old root.
        _tree(runtime)
        _normal_shutdown(runtime, marker)
        prefs = portable_settings(runtime)
        sid, new_id = uuid.uuid4().hex, uuid.uuid4().hex
        stage = RELEASE / ('.' + runtime.name + '.migrating-' + sid)
        previous = RELEASE / ('.' + runtime.name + '.previous-' + sid)
        stage.mkdir()
        try:
            physical = stage / 'data'
            data = runtime / 'data'
            verification = None
            if data.exists():
                shutil.copytree(data, physical)
                database = physical / 'labimage.sqlite3'
                if database.exists():
                    # A normally stopped backend checkpoints WAL before its
                    # exit receipt. Never trust a manually copied live WAL.
                    if (data / 'labimage.sqlite3-wal').exists() and (data / 'labimage.sqlite3-wal').stat().st_size:
                        raise RuntimeError('资料含非空 WAL，不能当作正常停机迁移；请用离线备份或重新正常关闭后复制。')
                    for suffix in ('-wal', '-shm', '-journal'):
                        (physical / ('labimage.sqlite3' + suffix)).unlink(missing_ok=True)
                    rebound = _verify_db(database, old_runtime / 'data', physical, runtime / 'data')
                    changed = 0
                    for metadata in (physical / 'annotations').glob('*/metadata.json'):
                        decoded = read_json(metadata)
                        adjusted = _json_paths(decoded, old_runtime / 'data', physical, runtime / 'data')
                        if decoded != adjusted:
                            write_json(metadata, adjusted)
                            changed += 1
                    verification = {'rebind': rebound, 'validation': _verify_db(database, runtime / 'data', physical),
                                    'rebound_metadata_files': changed}
                elif any(physical.iterdir()):
                    raise RuntimeError('非空资料目录缺少数据库；没有用空库覆盖。')
            write_json(stage / 'settings.json', prefs)
            stat_value = stage.stat()
            fresh = {'magic': MAGIC, 'runtime': str(runtime), 'release': str(RELEASE), 'id': new_id,
                     'directory_identity': [stat_value.st_dev, stat_value.st_ino], 'restored': False,
                     'portable': True}
            write_json(stage / '.daily-runtime.json', fresh)
            write_json(stage / 'migration_evidence.json', {
                'schema': JOURNAL_SCHEMA, 'old_runtime': str(old_runtime), 'new_runtime': str(runtime),
                'old_marker_id': marker.get('id'), 'new_marker_id': new_id,
                'completed_at': time.time(), 'database': verification, 'settings': prefs,
                'sessions_inherited': False, 'cache_copied': False,
                'cache_policy': 'No copied marker trusted; fresh empty cache is initialized by unchanged cache_root.py.'})
            write_json(RELEASE / '.portable-migration.json', {'schema': JOURNAL_SCHEMA, 'runtime': str(runtime),
                        'transaction_id': sid, 'new_marker_id': new_id})
        except Exception:
            _delete_owned(stage, RELEASE, '.' + runtime.name + '.migrating-')
            raise
    # Windows cannot rename directories while our own file-lock handles remain
    # open. The outer package migration lock serializes the following two swaps.
    try:
        runtime.replace(previous)
        stage.replace(runtime)
    except Exception:
        if previous.exists() and not runtime.exists():
            previous.replace(runtime)
        raise
    recover_relocation(runtime)
    return read_json(runtime / '.daily-runtime.json')
