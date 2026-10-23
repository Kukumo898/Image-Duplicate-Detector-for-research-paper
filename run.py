"""Start the single-process, loopback-only local review app."""
import argparse
import threading
import webbrowser
import json
import urllib.error
import urllib.request
import sys
from pathlib import Path

import uvicorn


def main():
    parser = argparse.ArgumentParser(description='图鉴 · 科研图像核验（本机服务）')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--candidate', action='store_true', help='显式运行隔离收尾候选；默认保留已交付v3，直至收尾验收通过')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('端口必须为 1–65535')
    url = f'http://127.0.0.1:{args.port}'
    try:
        with urllib.request.urlopen(url + '/api/status', timeout=1) as response:
            existing = json.load(response)
        if 'engine_version' in existing and 'library_count' in existing:
            print(f'系统已在运行：{url}')
            if not args.no_browser:
                webbrowser.open(url)
            return
    except (OSError, ValueError, urllib.error.URLError):
        pass
    if not args.no_browser:
        timer = threading.Timer(1.5, lambda: webbrowser.open(url))
        timer.daemon = True
        timer.start()
    root = Path(__file__).resolve().parent
    baseline = root / 'benchmarks/frozen_v3_before_closeout'
    gate = root / 'output/v3_closeout/acceptance_gate.json'
    accepted = gate.exists() and json.loads(gate.read_text(encoding='utf-8')).get('status') == 'passed'
    if baseline.is_dir() and not accepted and not args.candidate:
        # A candidate source edit must not silently become the formal engine
        # on the next normal launch. Archived code still uses the production DB.
        import os
        import hashlib
        import shutil
        os.environ.setdefault('LABIMAGE_DATA', str(root / 'data'))
        # Keep the frozen archive read-only. A separate baseline runtime also
        # needs the unchanged built-in demo assets expected by its ROOT paths.
        runtime = root / 'output/v3_closeout/production_runtime'
        if not runtime.exists():
            runtime.mkdir(parents=True)
            shutil.copytree(baseline / 'app', runtime / 'app', ignore=shutil.ignore_patterns('__pycache__'))
            shutil.copytree(root / 'demo', runtime / 'demo', ignore=shutil.ignore_patterns('__pycache__'))
        for archived in (baseline / 'app').rglob('*'):
            if not archived.is_file() or '__pycache__' in archived.parts:
                continue
            copied = runtime / 'app' / archived.relative_to(baseline / 'app')
            if not copied.is_file() or hashlib.sha256(copied.read_bytes()).digest() != hashlib.sha256(archived.read_bytes()).digest():
                raise RuntimeError('归档v3运行时源文件校验失败，未启动或替换正式引擎。')
        sys.path.insert(0, str(runtime))
        print('收尾验收尚未通过：默认启动归档的已交付 v3；候选请使用 --candidate 和隔离 LABIMAGE_DATA。')
    if args.candidate:
        import os
        if Path(os.environ.get('LABIMAGE_DATA', str(root/'data'))).resolve() == (root/'data').resolve():
            parser.error('--candidate 必须设置隔离 LABIMAGE_DATA，不可使用正式 data 目录')
    uvicorn.run('app.main:app', host='127.0.0.1', port=args.port, workers=1, log_level='info')


if __name__ == '__main__':
    main()

