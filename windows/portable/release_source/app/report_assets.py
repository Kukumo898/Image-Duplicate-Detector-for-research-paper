"""Per-export, content-addressed report assets; no detection or shared cache."""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import tempfile
import zipfile


class ReportAssets:
    def __init__(self, packed=False):
        self.packed = packed
        self.memo = {}
        self.entries = {}
        self.references = 0
        self.referenced_bytes = 0
        self.path = None
        self.archive = None
        self.delivered = False

    def __enter__(self):
        if self.packed:
            handle = tempfile.NamedTemporaryFile(prefix='labimage-report-', suffix='.zip', delete=False)
            self.path = Path(handle.name)
            handle.close()
            try:
                self.archive = zipfile.ZipFile(self.path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6)
            except BaseException:
                self.path.unlink(missing_ok=True)
                raise
        return self

    def __exit__(self, *exc):
        try:
            if self.archive:
                self.archive.close()
        except BaseException:
            if self.path:
                self.path.unlink(missing_ok=True)
            raise
        finally:
            if self.path and (not self.delivered or exc[0] is not None):
                self.path.unlink(missing_ok=True)

    def asset(self, key, extension, build):
        """Memoize rendering by full display key; deduplicate files by exact bytes."""
        if key not in self.memo:
            content = build()
            digest = hashlib.sha256(content).hexdigest()
            mime = 'image/jpeg' if extension == 'jpg' else 'image/png'
            name = f'assets/{digest}.{extension}'
            if self.packed:
                if name not in self.entries:
                    # PNG/JPEG are already compressed. JSON/HTML below use deflate.
                    self.archive.writestr(name, content, compress_type=zipfile.ZIP_STORED)
                    self.entries[name] = {'sha256': digest, 'bytes': len(content), 'media_type': mime}
                url = name
            else:
                url = f'data:{mime};base64,' + base64.b64encode(content).decode('ascii')
            self.memo[key] = (url, len(content))
        url, size = self.memo[key]
        self.references += 1
        self.referenced_bytes += size
        return url

    def finish(self, body, report_data):
        if not self.packed:
            raise ValueError('finish is only valid for ZIP reports')
        html_bytes = body.encode('utf-8')
        data_bytes = (json.dumps(report_data, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        manifest = {
            'format': 'labimage-offline-report-1',
            'job_id': report_data['job']['id'],
            'finding_count': len(report_data['findings']),
            'asset_references': self.references,
            'unique_assets': len(self.entries),
            'referenced_asset_bytes': self.referenced_bytes,
            'unique_asset_bytes': sum(a['bytes'] for a in self.entries.values()),
            'index_sha256': hashlib.sha256(html_bytes).hexdigest(),
            'results_sha256': hashlib.sha256(data_bytes).hexdigest(),
            'assets': self.entries,
            'scope': '全部记录；预览沿用单文件报告显示尺度；遮罩原字节保留。未包含原始上传文件。',
        }
        self.archive.writestr('index.html', html_bytes)
        self.archive.writestr('results.json', data_bytes)
        self.archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        self.archive.writestr('README.txt', (
            '图鉴 · 离线报告包\n\n'
            '请先完整解压，再打开 index.html。无需联网或启动图鉴服务。\n'
            '不要将 index.html 与 assets 文件夹分开；图片和无损遮罩已按内容去重。\n'
            'results.json 保留全部机器结果；manifest.json 提供文件校验及去重统计。\n'
            'JSON 中原 API URL 是来源记录，不是离线链接；离线图证请从 index.html 打开。\n'
            '预览图不是原始实验数据。本包未包含原始上传文件，请另外保存原图、PDF/DOCX。\n'
            '局部证据只支持显示范围，不代表整个图像或实验身份相同。\n'
        ).encode('utf-8'))
        self.archive.close()
        return self.path
