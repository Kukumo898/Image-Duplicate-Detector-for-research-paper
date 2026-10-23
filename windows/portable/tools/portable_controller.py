"""Loopback-only mode handover coordinator, separate from the backend process.

No detector code is loaded here. Every handover uses the existing authenticated
atomic admission gate, lifetime lock, and full process/session identity checks.
This is an explicit-action controller, not a background restart watchdog.
"""
from __future__ import annotations

import argparse
import ctypes
import hmac
import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from daily_common import (RELEASE, command_line, file_lock, listeners,
                          process_identity, read_json, service_locked,
                          verify_manifest, write_json)

MODES = ('accelerated', 'compatible')
SETTINGS_SCHEMA = 'labimage-portable-settings-v1'
CONTROLLER_SCHEMA = 'labimage-portable-controller-v1'


class ControlError(RuntimeError):
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload if isinstance(payload, dict) else {'detail': str(payload)}
        super().__init__(self.payload.get('detail', str(payload)))


def preferred_mode(runtime):
    path = Path(runtime) / 'settings.json'
    if not path.exists():
        return 'accelerated'
    obj = read_json(path)
    if obj.get('schema') != SETTINGS_SCHEMA or obj.get('preferred_mode') not in MODES:
        raise RuntimeError('加速偏好文件无效；请保留文件并查看使用说明，不会静默改用其他模式。')
    return obj['preferred_mode']


def remember_mode(runtime, mode):
    if mode not in MODES:
        raise ValueError('未知模式')
    write_json(Path(runtime) / 'settings.json',
               {'schema': SETTINGS_SCHEMA, 'preferred_mode': mode})


def _call(port, path, token, *, data=None, timeout=15):
    headers = {'X-Labimage-Control-Token': token}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(
        f'http://127.0.0.1:{int(port)}{path}',
        headers=headers, method='POST' if data is not None else 'GET',
        data=json.dumps(data).encode('utf-8') if data is not None else None)
    # Never use machine/user proxy configuration for local lifecycle requests.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read())
        except (ValueError, UnicodeError):
            payload = {'detail': '本机控制请求被拒绝。'}
        raise ControlError(error.code, payload) from error


def current_controller(runtime):
    runtime = Path(runtime)
    path = runtime / 'records' / 'controller.current.json'
    if not path.exists():
        return None
    rec = read_json(path)
    if (rec.get('schema') != CONTROLLER_SCHEMA or rec.get('runtime') != str(runtime)
            or rec.get('release') != str(RELEASE)):
        raise RuntimeError('控制器记录不属于当前包；未接管其他位置实例。')
    ident = rec['service']
    live = process_identity(ident['process_id'])
    if live is None or live['creation_filetime'] != ident['creation_filetime']:
        return None
    for key in ('executable_path', 'executable_sha256'):
        if live[key] != ident[key]:
            raise RuntimeError('控制器进程身份不符；未接管。')
    if (command_line(ident['process_id']) != ident['command_line']
            or ident['command_line'] != subprocess.list2cmdline(rec['arguments'])
            or listeners(rec['port']) != [{'pid': ident['process_id'], 'address': '127.0.0.1'}]):
        raise RuntimeError('控制器命令或端口归属不符；未接管。')
    found = _call(rec['port'], '/identity', rec['control_token'])
    for key in ('controller_id', 'runtime', 'release', 'manifest_sha256'):
        if found.get(key) != rec[key]:
            raise RuntimeError('控制器会话身份不符；未接管。')
    return rec


def ensure_controller(runtime, backend_port, digest):
    existing = current_controller(runtime)
    if existing:
        if existing['manifest_sha256'] != digest or existing['backend_port'] != backend_port:
            raise RuntimeError('控制器版本或配置不同，请先正常关闭图鉴。')
        return existing
    runtime = Path(runtime)
    for folder in ('records', 'logs'):
        (runtime / folder).mkdir(exist_ok=True)
    cid = uuid.uuid4().hex
    session = runtime / 'records' / (cid + '.controller-session.json')
    args = [sys.executable, '-B', '-X', 'utf8',
            str(RELEASE / 'tools' / 'portable_controller.py'), '--session-file', str(session)]
    rec = {'schema': CONTROLLER_SCHEMA, 'controller_id': cid,
           'runtime': str(runtime), 'release': str(RELEASE), 'backend_port': backend_port,
           'control_token': secrets.token_urlsafe(40), 'manifest_sha256': digest,
           'arguments': args, 'requested_at': time.time()}
    write_json(session, rec)
    env = {key: value for key, value in os.environ.items() if not key.startswith('LABIMAGE_')}
    env.update(PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1')
    with (runtime / 'logs' / 'controller.stdout.log').open('ab') as out, \
            (runtime / 'logs' / 'controller.stderr.log').open('ab') as err:
        child = subprocess.Popen(args, cwd=RELEASE, env=env, stdout=out, stderr=err,
                                 creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)
    deadline = time.monotonic() + 25
    last = ''
    while time.monotonic() < deadline:
        try:
            found = current_controller(runtime)
            if found and found['controller_id'] == cid:
                return found
        except Exception as error:
            last = str(error)
        if child.poll() is not None:
            break
        time.sleep(.2)
    raise RuntimeError('本机模式控制器未启动；请查看 user_data/logs/controller.stderr.log。' + last)


def stop_controller(runtime):
    rec = current_controller(runtime)
    if not rec:
        return {'already_stopped': True}
    result = _call(rec['port'], '/stop', rec['control_token'], data={}, timeout=90)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        live = process_identity(rec['service']['process_id'])
        if live is None or live['creation_filetime'] != rec['service']['creation_filetime']:
            return result
        time.sleep(.1)
    raise RuntimeError('控制器仍在正常退出中；未强行终止。请稍后再次关闭。')


class Controller:
    def __init__(self, rec):
        self.rec = rec
        self.runtime = Path(rec['runtime'])
        self.transition = threading.Lock()
        self.state_lock = threading.Lock()
        self.server = None
        self.state = {'state': 'ready', 'switching': False, 'current_mode': None,
                      'requested_mode': None, 'detail': '已就绪。'}
        self.publish(**self.state)

    def publish(self, **values):
        with self.state_lock:
            self.state.update(values)
            self.state['updated_at'] = time.time()
            write_json(self.runtime / 'records' / 'mode_status.json', self.state)

    def snapshot(self):
        with self.state_lock:
            return dict(self.state)

    def acquire_operation(self):
        if not self.transition.acquire(blocking=False):
            raise ControlError(409, {**self.snapshot(), 'detail': '模式切换或关闭正在进行，请等待完成。'})
        operation = file_lock(self.runtime / 'operation.lock')
        try:
            operation.__enter__()
            return operation
        except Exception as error:
            self.transition.release()
            raise ControlError(409, {'detail': str(error)}) from error

    def release_operation(self, operation):
        operation.__exit__(None, None, None)
        self.transition.release()

    def begin_switch(self, body):
        mode = body.get('mode')
        if mode not in MODES:
            raise ControlError(400, {'detail': '请选择开启或关闭加速模式。'})
        state = self.snapshot()
        if state['switching']:
            if state['requested_mode'] == mode:
                return 202, state, None
            raise ControlError(409, {**state, 'detail': '另一个模式切换正在进行；此请求未排队，请完成后重试。'})
        operation = self.acquire_operation()
        try:
            from daily_launcher import current
            old = current(self.runtime)
            if not old:
                raise ControlError(503, {'detail': '后端当前未运行；请通过“启动图鉴.bat”恢复。'})
            if (body.get('session_id') != old['session_id']
                    or not hmac.compare_digest(body.get('backend_token', ''), old['control_token'])):
                raise ControlError(409, {'detail': '网页对应的后端会话已改变；请等待重新连接后重试。'})
            if old['mode'] == mode:
                remember_mode(self.runtime, mode)
                self.publish(state='ready', switching=False, current_mode=mode,
                             requested_mode=mode, detail='当前模式已经生效。', last_error=None,
                             rollback_restored=False)
                self.release_operation(operation)
                return 200, self.snapshot(), None
            # This authenticated call atomically checks BOTH jobs and every
            # accepted unsafe HTTP request, then closes admission under one lock.
            # /api/daily/mode itself is excluded from mutation accounting.
            accepted = _call(old['port'], '/api/daily/shutdown', old['control_token'], data={})
            if not accepted.get('accepted'):
                raise ControlError(409, accepted)
            self.publish(state='switching', switching=True, current_mode=old['mode'],
                         requested_mode=mode, operation_id=uuid.uuid4().hex,
                         detail='正在切换计算模式，保留当前资料与页面输入，请稍候重新连接。',
                         last_error=None, rollback_restored=False)
            # The handler sends its 202 before starting this worker. The backend
            # drains the forwarding request before exiting; this controller lives on.
            return 202, self.snapshot(), lambda: self.finish_switch(old, mode, operation)
        except Exception:
            self.release_operation(operation)
            raise

    def wait_backend_exit(self, old):
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            live = process_identity(old['service']['process_id'])
            if live is None or live['creation_filetime'] != old['service']['creation_filetime']:
                if not service_locked(self.runtime) and not listeners(old['port']):
                    return
            time.sleep(.15)
        raise RuntimeError('旧后端尚未完成正常退出；未启动第二份后端，也未强制停止。')

    def finish_switch(self, old, mode, operation):
        restored = False
        try:
            self.wait_backend_exit(old)
            from daily_launcher import start
            new = start(self.runtime, old['port'], mode, self.rec['manifest_sha256'], self.rec)
            remember_mode(self.runtime, new['mode'])
            self.publish(state='ready', switching=False, current_mode=new['mode'],
                         detail='加速模式已开启。' if mode == 'accelerated' else '加速模式已关闭，已使用原计算路径。',
                         process_id=new['service']['process_id'], last_error=None,
                         rollback_restored=False)
        except Exception as error:
            failure = str(error)
            rollback_error = None
            actual = None
            try:
                from daily_launcher import current, start, stop
                live = current(self.runtime)
                if live:
                    if live['mode'] == old['mode']:
                        actual = old['mode']
                        restored = True
                    else:
                        stop(self.runtime)
                if not restored:
                    if service_locked(self.runtime):
                        raise RuntimeError('仍有未退出的后端占用数据目录，未冒险启动第二份。')
                    recovered = start(self.runtime, old['port'], old['mode'],
                                      self.rec['manifest_sha256'], self.rec)
                    actual = recovered['mode']
                    restored = True
            except Exception as recovery:
                rollback_error = str(recovery)
            self.publish(state='failed', switching=False, current_mode=actual,
                         last_error=failure, rollback_error=rollback_error,
                         rollback_restored=restored,
                         detail=('切换未完成，已安全恢复原模式；资料与原偏好保留，可稍后重试。'
                                 if restored else '切换未完成，暂时无法确认后端可用；未强制停止。请查看日志，稍后双击“启动图鉴.bat”恢复。'))
        finally:
            self.release_operation(operation)

    def stop(self):
        operation = self.acquire_operation()
        try:
            from daily_launcher import stop
            result = stop(self.runtime)  # Busy refusal keeps the controller alive.
            return result
        finally:
            self.release_operation(operation)


def serve(rec):
    runtime = Path(rec['runtime'])
    if rec['release'] != str(RELEASE) or verify_manifest() != rec['manifest_sha256']:
        raise RuntimeError('控制器不属于当前完整交付。')
    with file_lock(runtime / 'controller.lock'):
        controller = Controller(rec)

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                # No headers, body, tokens, or session credentials enter access logs.
                pass

            def respond(self, status, payload):
                data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                self.wfile.write(data)
                self.wfile.flush()

            def authorized(self):
                if (self.client_address[0] != '127.0.0.1'
                        or self.headers.get('Origin') is not None
                        or self.headers.get('Host') != f'127.0.0.1:{rec["port"]}'
                        or not hmac.compare_digest(self.headers.get('X-Labimage-Control-Token', ''), rec['control_token'])):
                    raise ControlError(403, {'detail': '仅允许当前包的本机受控请求。'})

            def handle_action(self):
                try:
                    self.authorized()
                    if self.command == 'GET' and self.path == '/identity':
                        return self.respond(200, {key: rec[key] for key in
                            ('controller_id', 'runtime', 'release', 'manifest_sha256')})
                    if self.command == 'GET' and self.path == '/status':
                        return self.respond(200, controller.snapshot())
                    if self.command != 'POST' or self.path not in ('/switch', '/stop'):
                        raise ControlError(404, {'detail': '没有此控制操作。'})
                    length = self.headers.get('Content-Length', '')
                    if (not length.isdecimal() or int(length) > 4096
                            or self.headers.get('Content-Type', '').split(';')[0] != 'application/json'):
                        raise ControlError(400, {'detail': '控制请求格式无效。'})
                    body = json.loads(self.rfile.read(int(length)))
                    if not isinstance(body, dict):
                        raise ControlError(400, {'detail': '控制请求格式无效。'})
                    if self.path == '/switch':
                        status, result, work = controller.begin_switch(body)
                        try:
                            self.respond(status, result)
                        finally:
                            if work:
                                threading.Thread(target=work, name='mode-handover', daemon=False).start()
                    else:
                        result = controller.stop()
                        self.respond(200, result)
                        threading.Thread(target=self.server.shutdown, daemon=True).start()
                except ControlError as error:
                    self.respond(error.status, error.payload)
                except (ValueError, UnicodeError):
                    self.respond(400, {'detail': '控制请求格式无效。'})
                except Exception as error:
                    self.respond(503, {'detail': str(error)})

            do_GET = handle_action
            do_POST = handle_action

        class Server(ThreadingHTTPServer):
            daemon_threads = False
            allow_reuse_address = False

        with Server(('127.0.0.1', 0), Handler) as server:
            rec['port'] = server.server_address[1]
            ident = process_identity(os.getpid())
            ctypes.windll.kernel32.GetCommandLineW.restype = ctypes.c_wchar_p
            ident['command_line'] = ctypes.windll.kernel32.GetCommandLineW()
            rec['service'] = ident
            rec['started_at'] = time.time()
            write_json(runtime / 'records' / 'controller.current.json', rec)
            server.serve_forever(poll_interval=.1)
        write_json(runtime / 'records' / (rec['controller_id'] + '.controller-exit.json'),
                   {'ended_at': time.time(), 'process_id': os.getpid(), 'normal_exit': True})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--session-file', required=True)
    serve(read_json(parser.parse_args().session_file))
