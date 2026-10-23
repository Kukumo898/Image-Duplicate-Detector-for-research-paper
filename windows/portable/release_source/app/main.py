"""Local-first research image review service. No outbound network requests."""
from __future__ import annotations

import csv
import asyncio
import hashlib
import hmac
import html
import io
import json
import logging
import os
import sqlite3
import threading
import sys
import uuid
import cv2
import numpy as np
from collections import OrderedDict, Counter, deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.background import BackgroundTask

from .daily_lifecycle import AdmissionCoordinator, MutationAdmissionMiddleware, cache_state

from .engine import (ENGINE_VERSION, ANNOTATION_VERIFICATION_POLICY,
                     verification_mask_layers, VERIFICATION_MASK_VERSION, FIXED_GRID_VERIFICATION_POLICY)
from .feature_cache import (CachedDetector, UncachedDetector, _enabled,
                            get_stats as feature_cache_stats)

# Freeze this process's configured computation path before any worker exists.
# Enabled-but-unavailable roots must stay on CachedDetector's resource controls.
# Changing cache mode requires a new process; the worker-local LRU is unchanged.
Detector = CachedDetector if _enabled() else UncachedDetector
CACHE_ENABLED = Detector is CachedDetector
from .ingest import ingest_file
from .panels import extract_panels, PANEL_VERSION
from .annotations import analyze_annotations, ANNOTATION_VERSION, ANNOTATION_POLICY
from .semantics import (enrich_finding, semantic_counts, SEMANTIC_SCHEMA_VERSION,
                        CONTENT_ROLES, COUNT_LABELS)

ROOT = Path(__file__).resolve().parents[1]
# Capture at process startup, not after a source edit while a scan is running.
ENGINE_SHA256 = hashlib.sha256((ROOT / 'app' / 'engine.py').read_bytes()).hexdigest()
PANEL_SHA256 = hashlib.sha256((ROOT / 'app' / 'panels.py').read_bytes()).hexdigest()
ANNOTATION_SHA256 = hashlib.sha256((ROOT / 'app' / 'annotations.py').read_bytes()).hexdigest()
SEMANTICS_SHA256 = hashlib.sha256((ROOT / 'app' / 'semantics.py').read_bytes()).hexdigest()
ANNOTATION_POLICY_SHA256 = hashlib.sha256(json.dumps(ANNOTATION_POLICY, sort_keys=True).encode()).hexdigest()
VERIFICATION_POLICY_SHA256 = hashlib.sha256(json.dumps(ANNOTATION_VERIFICATION_POLICY, sort_keys=True).encode()).hexdigest()
FIXED_GRID_POLICY_SHA256 = hashlib.sha256(json.dumps(FIXED_GRID_VERIFICATION_POLICY, sort_keys=True).encode()).hexdigest()
EVIDENCE_CLASSES = {'data_supported': '数据支持线索（仅视觉对应）', 'annotation_only': '仅标注/模板相似',
                    'inconclusive': '不确定/需复核', 'legacy_unclassified': '历史记录（未按数据区验证）'}
ANNOTATION_LAYERS = {'annotation', 'uncertain', 'data', 'labels', 'annotation_overlay', 'data_overlay', 'uncertain_overlay',
                     'verification_excluded', 'verification_data', 'legacy_overlay', 'verification_data_overlay'}
DATA = Path(os.environ.get('LABIMAGE_DATA', str(ROOT / 'data'))).resolve()
MAX_FILES = 100
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_TOTAL_BYTES = 500 * 1024 * 1024
MAX_IMAGES = 600
MAX_PAIRS = 80000
COMPARISON_WORKERS = max(1, min(4, (os.cpu_count() or 1) // 4))
ALLOWED = {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.bmp', '.webp', '.pdf', '.docx'}
POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix='image-review')
CANCELLED: set[str] = set()
JOB_STATE_LOCK = threading.RLock()
log = logging.getLogger('labimage')


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    con = sqlite3.connect(DATA / 'labimage.sqlite3', timeout=30)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        yield con
        con.commit()
    finally:
        con.close()


def initialize():
    cv2.setNumThreads(max(1, min(4, os.cpu_count() or 1)))
    for name in ('uploads', 'images', 'thumbnails', 'annotations'):
        (DATA / name).mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript('''
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, mode TEXT NOT NULL,
            status TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT,
            progress REAL DEFAULT 0, total_pairs INTEGER DEFAULT 0,
            processed_pairs INTEGER DEFAULT 0, images_count INTEGER DEFAULT 0,
            findings_count INTEGER DEFAULT 0, error TEXT,
            warnings TEXT DEFAULT '[]', settings TEXT NOT NULL, uploads TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS images (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
            name TEXT NOT NULL, source TEXT NOT NULL, page INTEGER,
            width INTEGER NOT NULL, height INTEGER NOT NULL, path TEXT NOT NULL,
            representation TEXT NOT NULL, source_key TEXT NOT NULL,
            in_library INTEGER DEFAULT 0, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS images_job ON images(job_id);
        CREATE INDEX IF NOT EXISTS images_library ON images(in_library);
        CREATE TABLE IF NOT EXISTS findings (
            id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id),
            image_a TEXT NOT NULL REFERENCES images(id),
            image_b TEXT NOT NULL REFERENCES images(id), result TEXT NOT NULL,
            review_status TEXT DEFAULT 'pending', review_note TEXT DEFAULT '',
            reviewed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS findings_job ON findings(job_id);
        CREATE TABLE IF NOT EXISTS pair_diagnostics (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES jobs(id),
            image_a TEXT NOT NULL, image_b TEXT NOT NULL, status TEXT NOT NULL,
            diagnostic TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS diagnostics_job ON pair_diagnostics(job_id);
        ''')
        # Additive migration: old images, findings and review decisions are retained.
        columns = {row['name'] for row in con.execute('PRAGMA table_info(images)')}
        for name, definition in [('parent_id', 'TEXT'), ('bbox', 'TEXT'), ('metadata', "TEXT DEFAULT '{}'"), ('annotations', "TEXT DEFAULT '{}'")]:
            if name not in columns:
                con.execute(f'ALTER TABLE images ADD COLUMN {name} {definition}')
        job_columns = {row['name'] for row in con.execute('PRAGMA table_info(jobs)')}
        if 'diagnostics' not in job_columns:
            con.execute("ALTER TABLE jobs ADD COLUMN diagnostics TEXT DEFAULT '{}'")
        con.execute("UPDATE jobs SET status='interrupted', error='服务重启中断了此任务，请重新提交；已生成的部分结果保留。', finished_at=? WHERE status IN ('queued','running','cancelling')", (now(),))


@asynccontextmanager
async def lifespan(app):
    initialize()
    yield
    with db() as con:
        CANCELLED.update(row['id'] for row in con.execute("SELECT id FROM jobs WHERE status IN ('queued','running','cancelling')"))
    # Jobs run in a single process; do not launch multiple uvicorn workers.
    await asyncio.to_thread(POOL.shutdown, wait=True)
    # The launcher stops us only after the admission gate has frozen an idle
    # service.  Finish any terminal worker cleanup before closing SQLite WAL.
    def checkpoint():
        with db() as con:
            con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    await asyncio.to_thread(checkpoint)


app = FastAPI(title='图鉴 · 科研图像核验', version='0.3.6-feature-cache-stable-trial', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', '[::1]', 'testserver'])
app.mount('/static', StaticFiles(directory=ROOT / 'app' / 'static'), name='static')


@app.middleware('http')
async def local_policy(request, call_next):
    # Browser-origin checks prevent unrelated websites posting local files/jobs.
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return Response('不允许跨站请求', status_code=403)
        length = request.headers.get('content-length')
        if length and (not length.isdecimal() or int(length) > MAX_TOTAL_BYTES + 1024 * 1024):
            return Response('请求超过 500 MiB 总上传限制', status_code=413)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'"
    return response


# Added after local_policy so upload admission is outside request-body parsing
# and every other user middleware. It remains held through the full ASGI call.
DAILY_ADMISSION = AdmissionCoordinator()
app.state.daily_admission = DAILY_ADMISSION
app.add_middleware(MutationAdmissionMiddleware, coordinator=DAILY_ADMISSION)


def daily_active_jobs():
    # Terminal completion/cancellation already uses this lock in the baseline.
    # Mutation admission protects job creation before it first reaches SQLite.
    with JOB_STATE_LOCK:
        with db() as con:
            return con.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','cancelling')").fetchone()[0]


def daily_identity():
    return {'session_id': os.environ.get('LABIMAGE_SESSION_ID'),
            'runtime_directory': str(Path(os.environ.get('LABIMAGE_RUNTIME', str(DATA.parent))).resolve()),
            'source_manifest_sha256': os.environ.get('LABIMAGE_SOURCE_MANIFEST_SHA256'),
            'process_id': os.getpid(), 'executable': sys.executable,
            'source_directory': str(ROOT), 'data_directory': str(DATA),
            'mode': 'accelerated' if CACHE_ENABLED else 'compatible',
            'mode_label': '加速模式' if CACHE_ENABLED else '兼容模式',
            'feature_cache_enabled': CACHE_ENABLED,
            'detector_class': Detector.__module__ + '.' + Detector.__name__,
            'original_detector_path': (not CACHE_ENABLED and
                not any(name in Detector.__dict__ for name in ('prepare', 'compare', '_sift'))),
            'application_version': app.version,
            'cache_version': feature_cache_stats()['cache_version']}


def require_daily_control(request: Request):
    token = os.environ.get('LABIMAGE_CONTROL_TOKEN', '')
    if not token:
        raise HTTPException(503, '此进程未由日常入口启动，不能通过日常入口控制。')
    supplied = request.headers.get('X-Labimage-Control-Token', '')
    if not hmac.compare_digest(token, supplied):
        raise HTTPException(403, '会话身份校验失败；未执行关闭操作。')


@app.get('/api/daily/identity')
def daily_control_identity(request: Request):
    require_daily_control(request)
    return daily_identity()


@app.get('/api/daily/status')
async def daily_status():
    identity = daily_identity()
    # Session secrets and implementation/audit hashes are not user steps.
    return {key: identity[key] for key in ('application_version', 'cache_version',
            'mode', 'mode_label', 'data_directory')} | {
        'cache': cache_state(feature_cache_stats(), enabled=CACHE_ENABLED),
        'active_jobs': await asyncio.to_thread(daily_active_jobs),
        **await DAILY_ADMISSION.snapshot(),
        'log_url': '/api/daily/log', 'help_url': '/api/daily/help'}


def portable_mode_control(path, body=None):
    """Private loopback forwarding; no lifecycle credential reaches JavaScript."""
    import urllib.request
    import urllib.error
    runtime = Path(os.environ.get('LABIMAGE_RUNTIME', str(DATA.parent))).resolve()
    record = runtime / 'records' / 'controller.current.json'
    try:
        controller = json.loads(record.read_text(encoding='utf-8-sig'))
        if (controller.get('schema') != 'labimage-portable-controller-v1'
                or controller.get('runtime') != str(runtime)
                or controller.get('release') != str(ROOT.parent)
                or controller.get('manifest_sha256') != os.environ.get('LABIMAGE_SOURCE_MANIFEST_SHA256')):
            raise ValueError('本机控制器与当前包身份不一致。')
        headers = {'X-Labimage-Control-Token': controller['control_token']}
        if body is not None:
            headers['Content-Type'] = 'application/json'
            body = {**body, 'session_id': os.environ.get('LABIMAGE_SESSION_ID'),
                    'backend_token': os.environ.get('LABIMAGE_CONTROL_TOKEN', '')}
        req = urllib.request.Request(f'http://127.0.0.1:{int(controller["port"])}{path}',
            headers=headers, method='POST' if body is not None else 'GET',
            data=json.dumps(body).encode('utf-8') if body is not None else None)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=20) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read())
        except (ValueError, UnicodeError):
            return error.code, {'detail': '本机控制请求被拒绝。'}
    except Exception:
        return 503, {'state': 'unavailable', 'switching': False,
                     'detail': '本机模式控制器暂不可用；当前计算模式未改变。请双击“启动图鉴.bat”恢复控制器，勿强制结束活动任务。'}


def portable_mode_view(control):
    # Current mode is measured from this process's selected class, never from
    # preference/configuration or the controller's requested target.
    identity = daily_identity()
    return {**control, **{key: identity[key] for key in
        ('mode', 'mode_label', 'feature_cache_enabled', 'detector_class', 'process_id')},
        'current_mode': identity['mode'],
        'cache': cache_state(feature_cache_stats(), enabled=CACHE_ENABLED)}


@app.get('/api/daily/mode')
async def portable_mode_status():
    code, control = await asyncio.to_thread(portable_mode_control, '/status')
    return JSONResponse(portable_mode_view(control), status_code=code,
                        headers={'Cache-Control': 'no-store'})


@app.post('/api/daily/mode')
async def portable_mode_switch(request: Request):
    # JSON + a non-simple header prevents cross-site form/no-cors requests even
    # when an attacker omits Origin. local_policy also validates exact Origin.
    if (request.headers.get('X-Labimage-Mode') != 'switch'
            or request.headers.get('Sec-Fetch-Site') == 'cross-site'
            or request.headers.get('Content-Type', '').split(';')[0] != 'application/json'):
        raise HTTPException(403, '仅允许当前图鉴网页切换模式。')
    try:
        body = await request.json()
    except (ValueError, UnicodeError):
        raise HTTPException(400, '请选择开启或关闭加速模式。')
    if not isinstance(body, dict) or body.get('mode') not in ('accelerated', 'compatible'):
        raise HTTPException(400, '请选择开启或关闭加速模式。')
    code, control = await asyncio.to_thread(portable_mode_control, '/switch',
                                          {'mode': body['mode']})
    return JSONResponse(portable_mode_view(control), status_code=code,
                        headers={'Cache-Control': 'no-store'})


@app.post('/api/daily/shutdown')
async def daily_shutdown(request: Request):
    require_daily_control(request)
    callback = getattr(app.state, 'daily_shutdown_callback', None)
    if not callable(callback):
        raise HTTPException(503, '本进程未安装正常关闭回调；未执行关闭。')
    result = await DAILY_ADMISSION.request_shutdown(daily_active_jobs)
    if not result['accepted']:
        return JSONResponse({**result, 'detail': '仍有活动任务或上传／保存请求；请等待完成，或在界面取消任务后重试。未强制停止。'}, status_code=409)
    # The final response is sent first; Uvicorn then drains existing read-only
    # responses and invokes lifespan cleanup. No new write can enter the gate.
    return JSONResponse({**result, 'detail': '已停止接收新任务和修改，正在正常关闭。'},
                        status_code=202, background=BackgroundTask(callback))


@app.get('/api/daily/log')
def daily_log():
    log_root = Path(os.environ.get('LABIMAGE_RUNTIME', str(DATA.parent))).resolve() / 'logs'
    logs = sorted((path for path in log_root.glob('*.log')
                   if path.is_file() and path.resolve().parent == log_root.resolve()),
                  key=lambda path: path.stat().st_mtime, reverse=True)[:3]
    content = []
    for path in logs:
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 65536))
            content.append(path.name + '\n' + stream.read(65536).decode('utf-8', 'replace'))
    return Response('\n\n'.join(content) or '暂时没有日志。日志目录：' + str(log_root),
                    media_type='text/plain; charset=utf-8')


@app.get('/api/daily/help', response_class=HTMLResponse)
def daily_help():
    help_path = ROOT.parent / '使用说明.md'
    text = (help_path.read_text(encoding='utf-8-sig') if help_path.is_file() else
            '双击“启动图鉴.bat”启动，在网页“运行状态”旁开关加速模式。\n关闭时双击“关闭图鉴.bat”；有活动任务或上传／保存请求时不会强停。\n切换会短暂重新连接，保留同一数据目录与页面输入；成功后记住模式。\n备份或整体复制前先正常关闭图鉴。\n已完成任务和复核资料保留；未实现中断续跑、持久队列或旧图库自动迁移。')
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>图鉴 · 使用说明</title><body style="max-width:850px;margin:32px auto;padding:0 20px;font:16px/1.8 sans-serif"><h1>图鉴 · 使用说明</h1><pre style="white-space:pre-wrap;overflow-wrap:anywhere">' + html.escape(text) + '</pre></body></html>'


def job_view(row):
    result = dict(row)
    for key in ('settings', 'warnings', 'diagnostics'):
        result[key] = json.loads(result[key])
    result['sources'] = [{k: v for k, v in source.items() if k != 'path'} for source in json.loads(result.pop('uploads'))]
    return result


def image_view(row):
    record = dict(row)
    return {k: row[k] for k in ('id', 'name', 'source', 'page', 'width', 'height', 'representation')} | {
        'url': f"/api/images/{row['id']}", 'thumbnail_url': f"/api/images/{row['id']}/thumbnail",
        'original_url': f"/api/jobs/{row['job_id']}/sources/{row['source_key']}",
        'parent_id': record.get('parent_id'),
        'parent_url': f"/api/images/{record['parent_id']}" if record.get('parent_id') else None,
        'bbox': json.loads(record['bbox']) if record.get('bbox') else None,
        'metadata': json.loads(record.get('metadata') or '{}'),
        'annotations': json.loads(record.get('annotations') or '{}'),
    }


def update_job(job_id, _connection=None, **values):
    if not values:
        return
    if _connection is not None:
        _connection.execute('UPDATE jobs SET ' + ','.join(f'{key}=?' for key in values) + ' WHERE id=?', (*values.values(), job_id))
        return
    with db() as con:
        con.execute('UPDATE jobs SET ' + ','.join(f'{key}=?' for key in values) + ' WHERE id=?', (*values.values(), job_id))


@app.get('/')
def index():
    return FileResponse(ROOT / 'app' / 'static' / 'index.html')


@app.get('/api/status')
def status():
    with db() as con:
        return {
            'engine_version': ENGINE_VERSION,
            'application_version': '0.3.6-feature-cache-stable-trial',
            'performance': {'feature_cache': feature_cache_stats()},
            'capabilities': {'lightweight_job_progress': True, 'bulk_review': True,
                             'offline_report_bundle': True, 'algorithm_unchanged': True},
            'process_id': os.getpid(),
            'panel_version': PANEL_VERSION,
            'engine_sha256': ENGINE_SHA256,
            'panel_sha256': PANEL_SHA256,
            'annotation_version': ANNOTATION_VERSION,
            'annotation_sha256': ANNOTATION_SHA256,
            'annotation_policy_sha256': ANNOTATION_POLICY_SHA256,
            'verification_policy_sha256': VERIFICATION_POLICY_SHA256,
            'fixed_grid_policy_sha256': FIXED_GRID_POLICY_SHA256,
            'semantic_schema_version': SEMANTIC_SCHEMA_VERSION,
            'semantics_sha256': SEMANTICS_SHA256,
            'validation_status': 'experimental_not_high_accuracy_validated',
            'library_count': con.execute('SELECT count(*) FROM images WHERE in_library=1').fetchone()[0],
            'jobs_count': con.execute('SELECT count(*) FROM jobs').fetchone()[0],
            'active_jobs': con.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running','cancelling')").fetchone()[0],
            'limits': {'max_files': MAX_FILES, 'max_file_mb': MAX_FILE_BYTES // 1024**2, 'max_images': MAX_IMAGES, 'max_pairs': MAX_PAIRS},
            'warnings': ['实验性复核工具，未通过高精度验收；严格模式仍有已知误报与漏报。', '本版为本机使用，不开放局域网访问。', '证据强度分数不是准确率或重复概率；未检出不代表不存在重复。'],
        }


@app.post('/api/jobs', status_code=202)
async def create_job(
    files: list[UploadFile] = File(...), mode: str = Form('batch'),
    sensitivity: str = Form('strict'), include_history: bool = Form(False),
    add_to_library: bool = Form(True), name: str = Form(''),
    auto_panels: str = Form('true'),
):
    # Boolean Form fields use HTML unchecked-checkbox semantics on omission;
    # parse explicitly so API clients also receive the documented default.
    if auto_panels.lower() not in {'true', 'false', '1', '0', 'on', 'off'}:
        raise HTTPException(400, 'auto_panels 应为 true 或 false')
    panels_enabled = auto_panels.lower() in {'true', '1', 'on'}
    if mode not in ('batch', 'documents', 'pair') or sensitivity not in ('strict', 'balanced'):
        raise HTTPException(400, '未知的检测模式或灵敏度')
    if not files or len(files) > MAX_FILES:
        raise HTTPException(400, f'请选择 1–{MAX_FILES} 个文件')
    if mode == 'pair' and len(files) != 2:
        raise HTTPException(400, '双图比对必须上传两张图片')
    with db() as con:
        active = con.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
    if active >= 5:
        raise HTTPException(429, '已有 5 个任务正在处理或排队，请稍后再试')
    job_id = uuid.uuid4().hex
    folder = DATA / 'uploads' / job_id
    folder.mkdir(parents=True)
    uploads = []
    total = 0
    try:
        for item in files:
            display = (item.filename or '未命名').replace('\\', '/').split('/')[-1][:240]
            ext = Path(display).suffix.lower()
            if ext not in ALLOWED or (mode == 'pair' and ext in ('.pdf', '.docx')):
                raise HTTPException(400, f'不支持的文件类型：{display}；旧版 .doc 请另存为 .docx')
            dest = folder / (uuid.uuid4().hex + ext)
            size = 0
            source_hash = hashlib.sha256()
            with dest.open('wb') as stream:
                while chunk := await item.read(1024 * 1024):
                    size += len(chunk)
                    total += len(chunk)
                    if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                        raise HTTPException(413, '上传超出限制：单文件 100 MiB，总计 500 MiB')
                    stream.write(chunk)
                    source_hash.update(chunk)
            if size == 0:
                raise HTTPException(400, f'文件为空：{display}')
            uploads.append({'path': str(dest), 'name': display, 'sha256': source_hash.hexdigest(), 'bytes': size})
    except BaseException:
        # Only files created in this new, server-generated job directory.
        for entry in folder.iterdir():
            if entry.is_file():
                entry.unlink()
        folder.rmdir()
        raise
    finally:
        for item in files:
            await item.close()
    settings = {'sensitivity': sensitivity, 'include_history': include_history and mode != 'pair',
                'add_to_library': add_to_library, 'engine_version': ENGINE_VERSION,
                'engine_sha256': ENGINE_SHA256, 'panel_sha256': PANEL_SHA256,
                'annotation_version': ANNOTATION_VERSION, 'annotation_sha256': ANNOTATION_SHA256,
                'annotation_policy_sha256': ANNOTATION_POLICY_SHA256,
                'verification_policy_sha256': VERIFICATION_POLICY_SHA256,
                'fixed_grid_policy_sha256': FIXED_GRID_POLICY_SHA256,
                'semantic_schema_version': SEMANTIC_SCHEMA_VERSION, 'semantics_sha256': SEMANTICS_SHA256,
                'auto_panels': panels_enabled and mode != 'pair', 'panel_version': PANEL_VERSION,
                'max_regions_per_pair': 4, 'comparison_workers': COMPARISON_WORKERS}
    with db() as con:
        con.execute('INSERT INTO jobs (id,name,mode,status,created_at,settings,uploads) VALUES (?,?,?,?,?,?,?)',
                    (job_id, name.strip()[:120] or f'{uploads[0]["name"]} 等 {len(files)} 个文件', mode, 'queued', now(), json.dumps(settings), json.dumps(uploads)))
    POOL.submit(run_job, job_id)
    return {'job_id': job_id}


def skip_structural_pair(a, b):
    if a.get('parent_id') and a.get('parent_id') == b.get('id'):
        return True
    if b.get('parent_id') and b.get('parent_id') == a.get('id'):
        return True
    return (a['job_id'] == b['job_id'] and a['source_key'] == b['source_key']
            and a['page'] == b['page'] and 'page' in (a['representation'], b['representation']))


def persist_annotations(image_id, path, width, height, parent=None, bbox=None):
    """Single-image masks only. Display overlays never become detector inputs."""
    with Image.open(path) as source:
        source = source.convert('RGB')
        source.thumbnail((1280, 1280), Image.Resampling.LANCZOS)
        rgb = np.array(source)
    analysis = analyze_annotations(rgb)
    folder = DATA / 'annotations' / image_id
    folder.mkdir(parents=True, exist_ok=True)
    for name, mask in [('annotation', analysis.annotation_mask), ('uncertain', analysis.uncertain_mask),
                       ('data', analysis.data_mask), ('labels', analysis.labels)]:
        Image.fromarray(mask.astype(np.uint8) * (1 if name == 'labels' else 255)).save(folder / f'{name}.png')
    verification_layers = verification_mask_layers(rgb, analysis)
    for name, mask in verification_layers.items():
        Image.fromarray(mask.astype(np.uint8) * 255).save(folder / f'{name}.png')
    for name, mask, color in [('annotation', analysis.annotation_mask, [240, 65, 85]),
                              ('uncertain', analysis.uncertain_mask, [245, 180, 40]),
                              ('data', analysis.data_mask, [25, 180, 125]),
                              ('verification_data', verification_layers['verification_data'], [25, 180, 125])]:
        overlay = rgb.copy()
        selected = mask.astype(bool)
        overlay[selected] = (overlay[selected].astype(float) * .55 + np.asarray(color) * .45).astype(np.uint8)
        Image.fromarray(overlay).save(folder / f'{name}_overlay.png')
    metadata = dict(analysis.metadata)
    metadata.update(version=ANNOTATION_VERSION, module_sha256=ANNOTATION_SHA256,
                    verification_engine_sha256=ENGINE_SHA256,
                    policy_sha256=ANNOTATION_POLICY_SHA256,
                    analysis_width=rgb.shape[1], analysis_height=rgb.shape[0],
                    original_width=width, original_height=height,
                    analysis_to_original=[[width / rgb.shape[1], 0, 0], [0, height / rgb.shape[0], 0], [0, 0, 1]],
                    original_to_parent=[[1, 0, bbox[0]], [0, 1, bbox[1]], [0, 0, 1]] if parent else None,
                    coordinate_system='analysis_view_xy_pixels',
                    layers={layer: f'/api/images/{image_id}/annotations/{layer}' for layer in sorted(ANNOTATION_LAYERS)},
                    verification_mask_version=VERIFICATION_MASK_VERSION,
                    verification_areas={name: {'pixels': int(mask.sum()), 'fraction': round(float(mask.mean()), 6)}
                                        for name, mask in verification_layers.items()},
                    legacy_overlay_note='旧矩形规则候选，实际是否排除请看verification_excluded；不能从候选推断实验标尺语义。',
                    display_only_overlays=True, uncertain_retained=True)
    (folder / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    return metadata


def run_job(job_id):
    warnings = []
    summary = {'segmentation': {'sources': 0, 'panels': 0, 'complete': False, 'reports': []},
               'status_counts': {}, 'rejection_counts': {}, 'classification_counts': {}, 'pair_classification_counts': {}, 'structural_pairs_skipped': 0,
               'caveat': '拒绝次数对应候选假设，并非真实阴性；未验证匹配不等于没有重复。'}
    try:
        with db() as con:
            job = dict(con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
            historical = [dict(row) for row in con.execute('SELECT * FROM images WHERE in_library=1 AND job_id!=?', (job_id,))]
        settings = json.loads(job['settings'])
        update_job(job_id, status='running')
        imported = []

        def register(record, source_index, parent=None):
            image_id = uuid.uuid4().hex
            path = Path(record['path']).resolve()
            if not path.is_relative_to(DATA / 'images'):
                raise ValueError('导入路径超出工作数据目录')
            metadata = {k: record[k] for k in ('source_index',) if k in record}
            if parent:
                metadata.update({k: record[k] for k in ('panel_index', 'confidence', 'method')})
                metadata.update(parent_width=parent['width'], parent_height=parent['height'],
                                coordinate_space='normalized_parent_pixels_xywh')
            elif record.get('bbox'):
                metadata.update(source_bbox_pdf_points_xyxy=record['bbox'])
            row = {'id': image_id, 'job_id': job_id, 'name': record['name'], 'source': record['source'],
                   'page': record.get('page'), 'width': record['width'], 'height': record['height'],
                   'path': str(path), 'representation': record.get('representation', 'image'),
                   'source_key': str(source_index), 'in_library': 0, 'created_at': now(),
                   'parent_id': parent['id'] if parent else None,
                   'bbox': json.dumps(record['bbox']) if parent else None,
                   'metadata': json.dumps(metadata, ensure_ascii=False)}
            with Image.open(path) as im:
                im.thumbnail((420, 320))
                im.convert('RGB').save(DATA / 'thumbnails' / f'{image_id}.jpg', quality=85)
            row['annotations'] = json.dumps(persist_annotations(image_id, path, record['width'], record['height'],
                                                                 parent, record.get('bbox')), ensure_ascii=False)
            with db() as con:
                keys = list(row)
                con.execute(f'INSERT INTO images ({",".join(keys)}) VALUES ({",".join("?" for _ in keys)})', tuple(row.values()))
            imported.append(row)
            return row

        for source_index, upload in enumerate(json.loads(job['uploads'])):
            if job_id in CANCELLED:
                break
            remaining = MAX_IMAGES - len(imported)
            if remaining <= 0:
                warnings.append(f'达到 {MAX_IMAGES} 张图像上限，后续文件未导入；本任务未全扫。')
                break
            records, notes = ingest_file(Path(upload['path']), upload['name'], DATA / 'images', max_images=remaining)
            warnings.extend(notes)
            parents = [register(record, source_index) for record in records]
            if settings.get('auto_panels'):
                embedded_pages = {p['page'] for p in parents if p['representation'] == 'embedded'}
                for parent in parents:
                    if job_id in CANCELLED:
                        break
                    if parent['representation'] == 'page' and parent['page'] in embedded_pages:
                        continue
                    remaining = MAX_IMAGES - len(imported)
                    if remaining <= 0:
                        warnings.append(f'达到 {MAX_IMAGES} 张视图上限，部分面板未拆分；本任务未全扫。')
                        break
                    children, panel_report = extract_panels(parent['path'], DATA / 'images' / job_id,
                                                           max_panels=min(64, remaining))
                    summary['segmentation']['sources'] += 1
                    summary['segmentation']['panels'] += len(children)
                    summary['segmentation']['reports'].append({'parent_id': parent['id'], 'name': parent['name'], **panel_report})
                    if panel_report['limit_reached']:
                        warnings.append(f'{parent["name"]}：面板数量达到上限，拆分未完整。')
                    for child in children:
                        child.update(name=f'{parent["name"]} · 面板 {child["panel_index"]}',
                                     source=parent['source'], page=parent['page'], representation='panel')
                        register(child, source_index, parent)
            update_job(job_id, images_count=len(imported), warnings=json.dumps(warnings, ensure_ascii=False),
                       diagnostics=json.dumps(summary, ensure_ascii=False))
        if job_id in CANCELLED:
            update_job(job_id, status='cancelled', finished_at=now(), diagnostics=json.dumps(summary, ensure_ascii=False))
            return
        if job['mode'] == 'pair' and len(imported) != 2:
            raise ValueError('双图模式只接受两张单帧图片；多页 TIFF 请使用批量模式')
        if not imported:
            raise ValueError('没有可检测图像')
        pairs = []
        structural = 0
        for i, a in enumerate(imported):
            for b in imported[i+1:]:
                if skip_structural_pair(a, b):
                    structural += 1
                else:
                    pairs.append((a, b))
        if settings['include_history']:
            # Evaluate the full requested search size BEFORE constructing the cross product.
            prospective = len(pairs) + len(imported) * len(historical)
            if prospective > MAX_PAIRS:
                raise ValueError(f'本次需比较 {prospective:,} 对，超过 {MAX_PAIRS:,} 对上限；请分批或关闭历史图库比对。任务未完成，不能把零结果解释为没有重复。')
            pairs.extend((a, b) for a in imported for b in historical)
        if len(pairs) > MAX_PAIRS:
            raise ValueError(f'本次需比较 {len(pairs):,} 对，超过 {MAX_PAIRS:,} 对上限，请分批上传')
        # A new comparison against a v2 library item needs its own current
        # single-image artifacts. This adds only the new annotations column;
        # historical finding JSON and all review decisions remain untouched.
        historical_analyzed = 0
        if settings['include_history']:
            for item in historical:
                if job_id in CANCELLED:
                    update_job(job_id, status='cancelled', finished_at=now(), diagnostics=json.dumps(summary, ensure_ascii=False))
                    return
                existing = json.loads(item.get('annotations') or '{}')
                if (existing.get('module_sha256') == ANNOTATION_SHA256
                        and existing.get('verification_engine_sha256') == ENGINE_SHA256
                        and (DATA / 'annotations' / item['id'] / 'verification_data.png').is_file()):
                    continue
                bbox = json.loads(item['bbox']) if item.get('bbox') else None
                metadata = persist_annotations(item['id'], item['path'], item['width'], item['height'],
                                               {'id': item['parent_id']} if item.get('parent_id') else None, bbox)
                metadata.update(analysis_requested_by_job=job_id,
                                historical_findings_reclassified=False,
                                artifact_purpose='new_comparison_single_image_analysis_not_retroactive_pair_verification')
                serialized = json.dumps(metadata, ensure_ascii=False)
                (DATA / 'annotations' / item['id'] / 'metadata.json').write_text(serialized, encoding='utf-8')
                with db() as con:
                    con.execute('UPDATE images SET annotations=? WHERE id=?', (serialized, item['id']))
                item['annotations'] = serialized
                historical_analyzed += 1
        summary['historical_images_analyzed_for_new_comparison'] = historical_analyzed
        if structural:
            warnings.append(f'已排除 {structural} 组同页整页与内嵌图/面板、原图与自身面板的结构性重合；同图不同面板仍互查。')
        summary['structural_pairs_skipped'] = structural
        if not pairs:
            warnings.append('没有可比较的图片对：请至少导入两张图，或启用非空历史图库。图像可入库供后续比对。')
        if settings.get('auto_panels'):
            warnings.append('自动拆分仅提出矩形面板，不识别实验标签且不能保证全分；保留整图比较。含内嵌图的 PDF 页面只拆内嵌图，未对其页面矢量区分面板。')
        warnings.append('未包含单张图内部复制粘贴检测（连续照片内部）；已拆出的不同面板可互查。低纹理条带和重复纹理仍可能漏检。每对最多报告 4 个区域，并非穷尽。')
        update_job(job_id, total_pairs=len(pairs), warnings=json.dumps(warnings, ensure_ascii=False),
                   diagnostics=json.dumps(summary, ensure_ascii=False))
        local = threading.local()

        def prepared(record):
            detector, cache = local.detector, local.cache
            key = record['id']
            if key not in cache:
                cache[key] = detector.prepare(record['path'])
            cache.move_to_end(key)
            # Pixel allowance includes lazy channels/features; bounded local cache.
            while len(cache) > 192 or (len(cache) > 2 and sum(p.gray.size * 28 for p in cache.values()) > 256 * 1024**2):
                cache.popitem(last=False)
            return cache[key]

        def compare_pair(pair):
            if job_id in CANCELLED:
                return None
            if not hasattr(local, 'detector'):
                local.detector, local.cache = Detector(), OrderedDict()
            a, b = pair
            return local.detector.compare_detailed(prepared(a), prepared(b), sensitivity=settings['sensitivity'],
                                                   max_regions=settings.get('max_regions_per_pair', 4))

        def compared_pairs():
            # Bounded prefetch; separate detector/cache per worker. Merge in input
            # order so review records are reproducible and cancellation is bounded.
            workers = max(1, min(COMPARISON_WORKERS, settings.get('comparison_workers', COMPARISON_WORKERS)))
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix='pair-check') as pool:
                pending = deque()
                iterator = iter(enumerate(pairs))
                for _ in range(workers * 2):
                    item = next(iterator, None)
                    if item is not None:
                        pending.append((item, pool.submit(compare_pair, item[1])))
                try:
                    while pending:
                        (index, pair), future = pending.popleft()
                        result = future.result()
                        if result is None or job_id in CANCELLED:
                            break
                        yield index, pair, result
                        item = next(iterator, None)
                        if item is not None:
                            pending.append((item, pool.submit(compare_pair, item[1])))
                finally:
                    for _, future in pending:
                        future.cancel()

        count = 0
        statuses, rejections, classes, pair_classes = Counter(), Counter(), Counter(), Counter()
        for index, (a, b), evidence in compared_pairs():
            if job_id in CANCELLED:
                update_job(job_id, status='cancelled', finished_at=now(), diagnostics=json.dumps(summary, ensure_ascii=False))
                return
            diagnostic = evidence['diagnostics']
            statuses[diagnostic['status']] += 1
            rejections.update(diagnostic.get('rejections', {}))
            found_classes = {f.get('evidence_class', 'legacy_unclassified') for f in evidence['findings']}
            pair_classes.update(found_classes or {diagnostic['status']})
            with db() as con:
                con.execute('INSERT INTO pair_diagnostics (job_id,image_a,image_b,status,diagnostic) VALUES (?,?,?,?,?)',
                            (job_id, a['id'], b['id'], diagnostic['status'], json.dumps(diagnostic, ensure_ascii=False)))
                for region_index, result in enumerate(evidence['findings'], 1):
                    classes[result.get('evidence_class', 'legacy_unclassified')] += 1
                    result['region_index'] = region_index
                    for side, row in [('a', a), ('b', b)]:
                        if row.get('parent_id'):
                            x, y, _, _ = json.loads(row['bbox'])
                            result['parent_regions_' + side] = [[rx+x, ry+y, rw, rh] for rx, ry, rw, rh in result['regions_' + side]]
                    con.execute('INSERT INTO findings (id,job_id,image_a,image_b,result) VALUES (?,?,?,?,?)',
                                (uuid.uuid4().hex, job_id, a['id'], b['id'], json.dumps(result, ensure_ascii=False)))
                    count += 1
                pair_summary = dict(summary)
                pair_summary.update(status_counts=dict(statuses), rejection_counts=dict(rejections),
                                    classification_counts=dict(classes), pair_classification_counts=dict(pair_classes))
                values = dict(processed_pairs=index+1, findings_count=count, progress=round((index+1)/len(pairs)*100, 1))
                if index % 20 == 0 or index == len(pairs)-1:
                    values['diagnostics'] = json.dumps(pair_summary, ensure_ascii=False)
                # Results and their progress watermark become visible together.
                update_job(job_id, _connection=con, **values)
            summary = pair_summary
        with JOB_STATE_LOCK:
            if job_id in CANCELLED:
                update_job(job_id, status='cancelled', finished_at=now(), diagnostics=json.dumps(summary, ensure_ascii=False))
                return
            with db() as con:
                if settings['add_to_library']:
                    con.execute('UPDATE images SET in_library=1 WHERE job_id=?', (job_id,))
                con.execute("UPDATE jobs SET status='completed',progress=100,finished_at=?,diagnostics=? WHERE id=?", (now(), json.dumps(summary, ensure_ascii=False), job_id))
    except Exception as exc:
        log.exception('Job %s failed', job_id)
        update_job(job_id, status='failed', error=str(exc)[:2000], finished_at=now(), warnings=json.dumps(warnings, ensure_ascii=False), diagnostics=json.dumps(summary, ensure_ascii=False))
    finally:
        CANCELLED.discard(job_id)


@app.get('/api/jobs')
def jobs():
    with db() as con:
        return {'jobs': [job_view(row) for row in con.execute('SELECT * FROM jobs ORDER BY created_at DESC LIMIT 200')]}


@app.get('/api/jobs/{job_id}')
def job_detail(job_id: str):
    with db() as con:
        # Keep the image map and findings on one read snapshot while a job runs.
        con.execute('BEGIN')
        job = con.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not job:
            raise HTTPException(404, '任务不存在')
        # Fetch current-job images and referenced historical images in one query.
        # The public images list still contains only this task's imported views.
        image_rows = list(con.execute('''SELECT * FROM images WHERE job_id=? OR id IN (
            SELECT image_a FROM findings WHERE job_id=? UNION SELECT image_b FROM findings WHERE job_id=?
        ) ORDER BY created_at''', (job_id, job_id, job_id)))
        image_views = {row['id']: image_view(row) for row in image_rows}
        images = [image_views[row['id']] for row in image_rows if row['job_id'] == job_id]
        findings = []
        for row in con.execute('SELECT * FROM findings WHERE job_id=?', (job_id,)):
            finding = enrich_finding(json.loads(row['result']))
            finding.update({'id': row['id'], 'review_status': row['review_status'], 'review_note': row['review_note'], 'reviewed_at': row['reviewed_at']})
            if finding.get('metrics', {}).get('annotation_verification', {}).get('roi_a'):
                finding['data_evidence_urls'] = {side: f'/api/findings/{row["id"]}/data-evidence/{side}/overlay' for side in ('a', 'b')}
            for key in ('image_a', 'image_b'):
                finding[key] = image_views[row[key]]
            findings.append(finding)
        findings.sort(key=lambda x: x.get('score', 0), reverse=True)
        counts = dict(Counter(f.get('evidence_class', 'legacy_unclassified') for f in findings))
        reviewed_at = max((f['reviewed_at'] for f in findings if f['reviewed_at']), default=None)
        return {'job': job_view(job), 'images': images, 'findings': findings, 'classification_counts': counts,
                'revision': job_revision(job, reviewed_at),
                'semantic_schema_version': SEMANTIC_SCHEMA_VERSION,
                'semantic_counts': semantic_counts(findings),
                'counting_scope': '分类为独立维度，允许交叉；视觉对应不等于已建立实验图像重复，全部记录保留。'}


PROGRESS_FIELDS = ('id', 'name', 'mode', 'status', 'created_at', 'finished_at', 'progress',
                   'total_pairs', 'processed_pairs', 'images_count', 'findings_count', 'error')


def job_revision(row, reviewed_at):
    # A progress-only tick must not force the browser to download all evidence.
    # Structural changes, terminal state and review edits do invalidate detail.
    payload = {key: row[key] for key in PROGRESS_FIELDS if key not in ('progress', 'processed_pairs')}
    payload['last_reviewed_at'] = reviewed_at
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


@app.get('/api/jobs/{job_id}/progress')
def job_progress(job_id: str):
    with db() as con:
        row = con.execute('SELECT ' + ','.join(PROGRESS_FIELDS) + ', '
                          '(SELECT MAX(reviewed_at) FROM findings WHERE job_id=jobs.id) AS last_reviewed_at '
                          'FROM jobs WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise HTTPException(404, '任务不存在')
        return {'job': {key: row[key] for key in PROGRESS_FIELDS},
                'revision': job_revision(row, row['last_reviewed_at'])}


@app.post('/api/jobs/{job_id}/cancel')
def cancel_job(job_id: str):
    with JOB_STATE_LOCK:
        with db() as con:
            cursor = con.execute("UPDATE jobs SET status='cancelling' WHERE id=? AND status IN ('queued','running','cancelling')", (job_id,))
            if cursor.rowcount == 0:
                raise HTTPException(409, '任务不存在或已经结束')
            CANCELLED.add(job_id)
    return {'status': 'cancelling'}


@app.get('/api/jobs/{job_id}/diagnostics')
def pair_diagnostics(job_id: str, limit: int = 100, offset: int = 0):
    if not 1 <= limit <= 500 or offset < 0:
        raise HTTPException(400, 'limit 应在 1–500 内，offset 不得为负')
    with db() as con:
        if not con.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone():
            raise HTTPException(404, '任务不存在')
        total = con.execute('SELECT count(*) FROM pair_diagnostics WHERE job_id=?', (job_id,)).fetchone()[0]
        pairs = []
        for row in con.execute('SELECT * FROM pair_diagnostics WHERE job_id=? ORDER BY id LIMIT ? OFFSET ?', (job_id, limit, offset)):
            pairs.append({'image_a': row['image_a'], 'image_b': row['image_b'], 'status': row['status'],
                          'diagnostics': json.loads(row['diagnostic'])})
    return {'total': total, 'offset': offset, 'limit': limit, 'pairs': pairs}


@app.get('/api/jobs/{job_id}/sources/{source_index}')
def source_file(job_id: str, source_index: int):
    with db() as con:
        row = con.execute('SELECT uploads FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, '任务不存在')
    sources = json.loads(row['uploads'])
    if source_index < 0 or source_index >= len(sources):
        raise HTTPException(404, '来源文件不存在')
    source = sources[source_index]
    return FileResponse(source['path'], media_type='application/octet-stream', filename=source['name'])


class Review(BaseModel):
    status: str
    note: str = Field(default='', max_length=4000)


class BulkReview(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    status: str
    note: str = Field(default='', max_length=4000)


@app.post('/api/jobs/{job_id}/reviews')
def bulk_review(job_id: str, body: BulkReview):
    if body.status not in ('pending', 'confirmed', 'dismissed'):
        raise HTTPException(400, '无效的复核状态')
    if len(set(body.ids)) != len(body.ids):
        raise HTTPException(400, '批量复核不能包含重复的匹配结果 ID')
    placeholders = ','.join('?' for _ in body.ids)
    stamp = now()
    replace_note = 'note' in body.model_fields_set
    with db() as con:
        # Validate and write under one transaction: never partly apply a request
        # containing an unknown finding or a finding from a different task.
        con.execute('BEGIN IMMEDIATE')
        if not con.execute('SELECT 1 FROM jobs WHERE id=?', (job_id,)).fetchone():
            raise HTTPException(404, '任务不存在')
        found = con.execute(f'SELECT id FROM findings WHERE job_id=? AND id IN ({placeholders})',
                            (job_id, *body.ids)).fetchall()
        if len(found) != len(body.ids):
            raise HTTPException(404, '部分匹配结果不存在或不属于本任务；本次未修改任何结果')
        if replace_note:
            con.execute(f'UPDATE findings SET review_status=?,review_note=?,reviewed_at=? WHERE job_id=? AND id IN ({placeholders})',
                        (body.status, body.note, stamp, job_id, *body.ids))
        else:
            con.execute(f'UPDATE findings SET review_status=?,reviewed_at=? WHERE job_id=? AND id IN ({placeholders})',
                        (body.status, stamp, job_id, *body.ids))
    return {'count': len(body.ids), 'status': body.status, 'reviewed_at': stamp,
            'notes_preserved': not replace_note}


@app.post('/api/findings/{finding_id}/review')
def review(finding_id: str, body: Review):
    if body.status not in ('pending', 'confirmed', 'dismissed'):
        raise HTTPException(400, '无效的复核状态')
    with db() as con:
        cursor = con.execute('UPDATE findings SET review_status=?,review_note=?,reviewed_at=? WHERE id=?', (body.status, body.note, now(), finding_id))
        if cursor.rowcount == 0:
            raise HTTPException(404, '匹配结果不存在')
    return {'status': body.status, 'note': body.note}


@app.get('/api/library')
def library():
    with db() as con:
        return {'images': [image_view(row) for row in con.execute('SELECT * FROM images WHERE in_library=1 ORDER BY created_at DESC')]}


@app.get('/api/images/{image_id}')
def original(image_id: str):
    with db() as con:
        row = con.execute('SELECT path FROM images WHERE id=?', (image_id,)).fetchone()
    if not row:
        raise HTTPException(404, '图片不存在')
    return FileResponse(row['path'], media_type='image/png')


@app.get('/api/images/{image_id}/thumbnail')
def thumbnail(image_id: str):
    if not image_id.isalnum():
        raise HTTPException(404, '图片不存在')
    path = DATA / 'thumbnails' / f'{image_id}.jpg'
    if not path.is_file():
        raise HTTPException(404, '图片不存在')
    return FileResponse(path, media_type='image/jpeg')


@app.get('/api/images/{image_id}/annotations/{layer}')
def annotation_layer(image_id: str, layer: str):
    if not image_id.isalnum() or layer not in ANNOTATION_LAYERS:
        raise HTTPException(404, '标注层不存在')
    with db() as con:
        row = con.execute('SELECT annotations FROM images WHERE id=?', (image_id,)).fetchone()
    if not row or not json.loads(row['annotations'] or '{}'):
        raise HTTPException(404, '历史记录没有本轮标注证据；不自动补写')
    path = DATA / 'annotations' / image_id / f'{layer}.png'
    if not path.is_file():
        raise HTTPException(404, '标注层文件缺失')
    return FileResponse(path, media_type='image/png')


def common_data_masks(finding):
    """Reconstruct the exact verifier mask from frozen single-view masks and ROI."""
    verification = finding.get('metrics', {}).get('annotation_verification', {})
    if not verification.get('roi_a') or not verification.get('roi_b'):
        raise HTTPException(404, '历史记录没有可重建的数据区')
    masks, rois = {}, {}
    for side in ('a', 'b'):
        image = finding['image_' + side]
        path = DATA / 'annotations' / image['id'] / 'verification_data.png'
        if not path.is_file():
            raise HTTPException(404, '单图核验遮罩缺失')
        masks[side] = (np.array(Image.open(path).convert('L')) > 0).astype(np.uint8)
        rois[side] = np.zeros_like(masks[side])
        contours = [np.asarray(c, np.int32).reshape(-1, 1, 2) for c in verification['roi_' + side]['contours']]
        if contours:
            cv2.fillPoly(rois[side], contours, 1)
    matrix = np.asarray(verification['analysis_transform_a_to_b'], np.float64)
    common = {}
    for side, other, transform in [('a','b',np.linalg.inv(matrix)), ('b','a',matrix)]:
        height, width = masks[side].shape
        mapped = cv2.warpPerspective(rois[other] & masks[other], transform, (width,height),
                                    flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
        common[side] = rois[side] & masks[side] & mapped
        expected = verification['coverage_' + side]['effective_pixels']
        if int(common[side].sum()) != expected:
            raise HTTPException(409, '核验区重建与冻结覆盖计数不一致；拒绝展示错误叠加')
    return common


def common_data_overlay(image, mask):
    with db() as con:
        path = con.execute('SELECT path FROM images WHERE id=?', (image['id'],)).fetchone()[0]
    with Image.open(path) as source:
        rgb = np.array(source.convert('RGB').resize((mask.shape[1],mask.shape[0]), Image.Resampling.LANCZOS))
    rgb[mask != 0] = (rgb[mask != 0].astype(float)*.55 + np.asarray([25,180,125])*.45).astype(np.uint8)
    return rgb


@app.get('/api/findings/{finding_id}/data-evidence/{side}/{layer}')
def finding_data_evidence(finding_id: str, side: str, layer: str):
    if side not in ('a','b') or layer not in ('mask','overlay'):
        raise HTTPException(404, '证据层不存在')
    with db() as con:
        row = con.execute('SELECT * FROM findings WHERE id=?', (finding_id,)).fetchone()
        if not row:
            raise HTTPException(404, '线索不存在')
        finding = json.loads(row['result'])
        for name in ('a','b'):
            finding['image_'+name] = image_view(con.execute('SELECT * FROM images WHERE id=?', (row['image_'+name],)).fetchone())
    mask = common_data_masks(finding)[side]
    pixels = mask * 255 if layer == 'mask' else common_data_overlay(finding['image_'+side], mask)
    stream = io.BytesIO(); Image.fromarray(pixels).save(stream, 'PNG')
    return Response(stream.getvalue(), media_type='image/png')


@app.get('/api/demo-files')
def demo_files():
    return {'files': [{'name': path.name, 'url': f'/api/demo/{path.name}'} for path in sorted((ROOT / 'demo').glob('*.png'))]}


@app.get('/api/demo/{name}')
def demo_file(name: str):
    if Path(name).name != name or not name.endswith('.png'):
        raise HTTPException(404)
    path = ROOT / 'demo' / name
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type='image/png')


def safe_csv(value):
    value = str(value or '')
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@', '\t', '\r')) else value


@app.get('/api/jobs/{job_id}/report')
def report(job_id: str, format: str = 'html'):
    if format not in ('html', 'json', 'csv', 'zip'):
        raise HTTPException(400, '支持 html、json、csv、zip 格式')
    detail = job_detail(job_id)
    report_data = {'report_version': '3.0', 'exported_at': now(), 'disclaimer': '实验性辅助复核工具，未通过高精度验收。自动匹配不是科研不端判断。分数为证据强度，不是概率。', **detail}
    headers = {'Content-Disposition': f'attachment; filename="labimage-{job_id[:8]}.{format if format in ("html","json","csv","zip") else "txt"}"'}
    if format == 'json':
        return Response(json.dumps(report_data, ensure_ascii=False, indent=2), media_type='application/json', headers=headers)
    if format == 'csv':
        buffer = io.StringIO(newline='')
        writer = csv.writer(buffer)
        writer.writerow(['任务状态', '引擎版本', '图片 A', '来源 A', '页码 A', '图片 B', '来源 B', '页码 B', '类型', '证据强度(非概率)', '复核状态', '备注', '证据说明', '色彩分量 A', '色彩分量 B', '面板框 A(xywh)', '面板框 B(xywh)', '原图证据区域 A', '原图证据区域 B', '范围说明/未全扫警告', 'evidence_class', '数据区验证JSON', '标注版本', '坐标系', '任务错误', 'visual_evidence', 'content_role', 'semantic_basis', 'review_scope', 'semantic_counts'])
        notes = '；'.join(detail['job']['warnings'])
        error = detail['job'].get('error') or ''
        for f in detail['findings']:
            a, b = f['image_a'], f['image_b']
            verification = f.get('metrics', {}).get('annotation_verification', {})
            writer.writerow([safe_csv(v) for v in [detail['job']['status'], detail['job']['settings']['engine_version'], a['name'], a['source'], a['page'], b['name'], b['source'], b['page'], f['kind'], f['score'], f['review_status'], f['review_note'], f['explanation'], f.get('channel_a', 'gray'), f.get('channel_b', 'gray'), json.dumps(a.get('bbox')), json.dumps(b.get('bbox')), json.dumps(f.get('parent_regions_a', f.get('regions_a'))), json.dumps(f.get('parent_regions_b', f.get('regions_b'))), notes, f.get('evidence_class', 'legacy_unclassified'), json.dumps(verification, ensure_ascii=False), verification.get('annotation_version'), verification.get('coordinate_system'), error, json.dumps(f['visual_evidence'], ensure_ascii=False), f['content_role'], json.dumps(f['semantic_basis'], ensure_ascii=False), json.dumps(f['review_scope'], ensure_ascii=False), json.dumps(detail['semantic_counts'], ensure_ascii=False)]])
        if not detail['findings']:
            writer.writerow([safe_csv(v) for v in [detail['job']['status'], detail['job']['settings']['engine_version'], '', '', '', '', '', '', '', '', '', '', '无匹配记录；是否完成请查看任务状态，未检出不等于没有重复。', '', '', '', '', '', '', notes, '', '', detail['job']['settings'].get('annotation_version'), '', error, '', '', '', '', json.dumps(detail['semantic_counts'], ensure_ascii=False)]])
        return Response('\ufeff' + buffer.getvalue(), media_type='text/csv; charset=utf-8', headers=headers)
    from .report_assets import ReportAssets
    from starlette.background import BackgroundTask
    with ReportAssets(packed=format == 'zip') as assets:
        body = render_report_html(detail, report_data, assets)
        if format == 'zip':
            path = assets.finish(body, report_data)
            response = FileResponse(path, media_type='application/zip', headers=headers,
                                    background=BackgroundTask(path.unlink, missing_ok=True))
            assets.delivered = True
            return response
        return HTMLResponse(body, headers=headers)


def render_report_html(detail, report_data, assets):
    # Same evidence and rendering policy for legacy HTML and the deduplicated ZIP.
    from PIL import ImageDraw

    def marked_preview(path, regions, limit, quality):
        with Image.open(path) as picture:
            picture = picture.convert('RGB')
            original_size = picture.size
            picture.thumbnail(limit)
            draw = ImageDraw.Draw(picture)
            sx, sy = picture.width / original_size[0], picture.height / original_size[1]
            for region in regions:
                if isinstance(region, dict):
                    x, y, w, h = region['x'], region['y'], region.get('width', region.get('w')), region.get('height', region.get('h'))
                else:
                    x, y, w, h = region
                draw.rectangle((x*sx, y*sy, (x+w)*sx, (y+h)*sy), outline='#e76834', width=3)
            stream = io.BytesIO()
            picture.save(stream, format='JPEG', quality=quality)
            return stream.getvalue()

    def common_overlay_bytes(im, mask):
        stream = io.BytesIO()
        Image.fromarray(common_data_overlay(im, mask)).save(stream, 'PNG')
        return stream.getvalue()

    image_ids = {f[key]['id'] for f in detail['findings'] for key in ('image_a', 'image_b')}
    image_ids.update(f[key]['parent_id'] for f in detail['findings'] for key in ('image_a', 'image_b') if f[key].get('parent_id'))
    image_paths = {}
    identifiers = sorted(image_ids)
    with db() as con:
        for offset in range(0, len(identifiers), 400):
            chunk = identifiers[offset:offset+400]
            for row in con.execute('SELECT id,path FROM images WHERE id IN (' + ','.join('?' for _ in chunk) + ')', chunk):
                image_paths[row['id']] = row['path']
    esc = lambda value: html.escape(str(value if value is not None else '—'))
    kinds = {'exact_pixels': '查重视图像素一致', 'near_duplicate': '整体近似重复', 'local_reuse': '局部复用线索'}
    reviews = {'pending': '待人工复核', 'confirmed': '人工确认相似', 'dismissed': '人工排除'}
    statuses = {'completed': '检测完成', 'failed': '检测失败', 'cancelled': '已取消（未全扫）', 'interrupted': '已中断（未全扫）', 'queued': '排队中', 'running': '检测中（部分结果）', 'cancelling': '正在取消（部分结果）'}
    cards = []
    for f in detail['findings']:
        figures = []
        common_masks = common_data_masks(f) if f.get('data_evidence_urls') else None
        for key, region_key in (('image_a', 'regions_a'), ('image_b', 'regions_b')):
            im = f[key]
            regions = f.get(region_key, [])
            preview_key = ('preview', im['id'], json.dumps(regions, sort_keys=True))
            image_url = assets.asset(preview_key, 'jpg',
                lambda: marked_preview(image_paths[im['id']], regions, (1000, 1000), 88))
            context = ''
            if im.get('parent_id'):
                context_key = ('context', im['parent_id'], json.dumps(im['bbox']))
                context_url = assets.asset(context_key, 'jpg',
                    lambda: marked_preview(image_paths[im['parent_id']], [im['bbox']], (600, 600), 82))
                context = f'<details><summary>原图上下文 · 面板框 {esc(im["bbox"])}</summary><img src="{context_url}"></details>'
            layers = ''
            for layer, title in [('annotation_overlay', '标注候选（红）'), ('uncertain_overlay', '不确定（黄，仍保留）'), ('data_overlay', '保留数据候选（绿，不代表匹配成功）'), ('annotation', '二值标注遮罩'), ('data', '二值数据候选遮罩'), ('verification_excluded', '实际核验排除遮罩（歧义旧标尺候选保留）'), ('verification_data', '实际单图核验保留区')]:
                layer_path = DATA / 'annotations' / im['id'] / f'{layer}.png'
                if im.get('annotations') and layer_path.is_file():
                    layer_url = assets.asset(('layer', im['id'], layer), 'png', layer_path.read_bytes)
                    layers += f'<details><summary>{title}</summary><img alt="{title}" src="{layer_url}"></details>'
            if common_masks is not None:
                side = 'a' if key == 'image_a' else 'b'
                mask = common_masks[side]
                # Pair-local support may differ even when the source image is the same.
                mask_key = ('common', im['id'], mask.shape, hashlib.sha256(mask.tobytes()).hexdigest())
                pair_url = assets.asset(mask_key, 'png', lambda: common_overlay_bytes(im, mask))
                layers += f'<details><summary>共同可见的有效核验区（绿；是否通过看证据分类）</summary><img alt="共同有效核验区" src="{pair_url}"></details>'
            if im.get('annotations'):
                layers += f'<details><summary>遮罩版本/面积/坐标</summary><pre>{esc(json.dumps(im["annotations"], ensure_ascii=False, indent=2))}</pre></details>'
            else:
                layers = '<p>历史记录：没有本轮单图遮罩，未追填验证结论。</p>'
            figures.append(f'<figure><img alt="原图与匹配区域" src="{image_url}"><figcaption>{esc(im["name"])} · 来源 {esc(im["source"])} · 页 {esc(im["page"])}</figcaption>{context}{layers}</figure>')
        classification = f.get('evidence_class', 'legacy_unclassified')
        verification = f.get('metrics', {}).get('annotation_verification', {})
        grid = verification.get('fixed_grid_evidence')
        if grid and verification.get('photometric_basis') == 'fixed_grid_robust_partial_correspondence':
            effective = sum(cell['effective_pixels'] for cell in grid['cells'])
            figures.append(f'<p>固定局部核验：支持 {grid["supporting_cells"]}/{len(grid["cells"])} 格；反证 {grid["counterevidence_cells"]} 格；无足够信息 {len(grid["cells"])-grid["informative_cells"]} 格。支持像素 {grid["supporting_pixels"]}/{effective}（{grid["supporting_pixels"]/max(1,effective):.1%}，网格 {esc(verification.get("verification_grid"))}）。仅部分对应，不是整框一致；全部反证像素保留。</p>')
        figures.append(f'<div><p>内容角色：{esc(CONTENT_ROLES[f["content_role"]])}；实验属性已核实：{esc(f["semantic_basis"]["experimental_identity_verified"])}</p><details><summary>视觉对应 / 内容角色 / 语义依据 / 结论边界（独立维度）</summary><pre>{esc(json.dumps({k:f[k] for k in ("visual_evidence","content_role","semantic_basis","review_scope")}, ensure_ascii=False, indent=2))}</pre></details></div>')
        cards.append(f'<article><h2>{esc(EVIDENCE_CLASSES.get(classification, classification))} · {esc(kinds.get(f["kind"], f["kind"]))} · 证据强度 {esc(f["score"])}</h2><div class="pair">{"".join(figures)}</div><p>{esc(f["explanation"])}</p><p>复核：{esc(reviews.get(f["review_status"], f["review_status"]))} · {esc(f["review_note"])}</p><details><summary>原始/数据区证据、两侧全图/重叠域覆盖与拒绝原因</summary><pre>{esc(json.dumps(f["metrics"], ensure_ascii=False, indent=2))}</pre></details></article>')
    job = detail['job']
    body = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>科研图像核验报告</title>
    <style>body{{font:15px/1.7 system-ui,sans-serif;max-width:1100px;margin:40px auto;padding:0 24px;color:#173332;background:#f7f9f8}}h1{{font-size:30px}}article{{background:white;border:1px solid #d8e3df;border-radius:12px;padding:20px;margin:22px 0;break-inside:avoid}}.pair{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:20px}}.pair>div,.pair>p{{grid-column:1/-1;min-width:0}}figure{{margin:0;flex:1;min-width:0}}img{{max-width:100%;max-height:500px;object-fit:contain}}figcaption{{overflow-wrap:anywhere;font-size:12px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}.notice{{background:#fff4dc;padding:16px}}@media(max-width:600px){{.pair{{grid-template-columns:minmax(0,1fr)}}body{{padding:0 12px}}}}@media print{{body{{margin:0;background:white}}}}</style>
    <h1>图鉴 · 科研图像核验报告</h1><p>{esc(job['name'])}</p><p>任务状态：<b>{esc(statuses.get(job['status'], job['status']))}</b> · 引擎：{esc(job['settings']['engine_version'])} · 模式：{esc(job['settings']['sensitivity'])} · 导出：{esc(now())}</p>
    <p>图像 {job['images_count']} 张 · 完成比较 {job['processed_pairs']} / {job['total_pairs']} 对 · 匹配记录 {len(detail['findings'])} 组</p>
    <p>报告 v3 · 全部记录均保留：{esc(' · '.join(EVIDENCE_CLASSES.get(k,k)+' '+str(v) for k,v in detail['classification_counts'].items()))}。data_supported 仅为遮罩外视觉对应，不计为已确认实验数据复用；其他记录不代表阴性。保留数据候选不等于已验证实验数据。</p>
    <p>分层计数（维度交叉，不可求和）：{esc(' · '.join(COUNT_LABELS[k]+' '+str(v) for k,v in detail['semantic_counts'].items()))}</p>
    <div class="notice">{esc(report_data['disclaimer'])} 未检出不代表不存在重复。未完成任务只包含部分结果。<br>{esc(job.get('error') or '')}<ul>{''.join('<li>'+esc(w)+'</li>' for w in job['warnings'])}</ul></div>
    {''.join(cards) or '<article>本任务没有匹配记录。请结合任务状态、导入警告及检测范围解读。</article>'}
    <details><summary>检测覆盖与候选拒绝诊断（不是真实阴性统计）</summary><pre>{esc(json.dumps(job.get('diagnostics', {}), ensure_ascii=False, indent=2))}</pre></details>
    <details><summary>原始上传文件 SHA-256（用于归档校验）</summary><pre>{esc(json.dumps(job['sources'], ensure_ascii=False, indent=2))}</pre></details></html>'''
    return body.replace('<img ', '<img loading="lazy" decoding="async" ') if assets.packed else body
