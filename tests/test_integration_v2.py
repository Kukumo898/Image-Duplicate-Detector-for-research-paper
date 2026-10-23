"""Check actual panel ingestion, sibling search, provenance and diagnostic APIs."""
import io
import json
import sqlite3
import time

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image
import cv2

from app import main


def wait(client, identifier):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        detail = client.get('/api/jobs/' + identifier).json()
        if detail['job']['status'] not in {'queued', 'running', 'cancelling'}:
            return detail
        time.sleep(.03)
    raise AssertionError('job timed out')


def test_real_montage_siblings_search_and_source_trace(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path / 'data')
    rng = np.random.default_rng(513)
    panel = Image.fromarray(rng.integers(15, 220, (180, 240, 3), dtype=np.uint8))
    montage = Image.new('RGB', (560, 230), 'white')
    montage.paste(panel, (20, 25))
    montage.paste(panel, (300, 25))
    content = io.BytesIO()
    montage.save(content, 'PNG')
    with TestClient(main.app) as client:
        response = client.post('/api/jobs', files=[('files', ('montage.png', content.getvalue()))],
                               data={'mode': 'batch', 'add_to_library': 'false'})
        detail = wait(client, response.json()['job_id'])
        job = detail['job']
        assert job['status'] == 'completed', job
        assert job['images_count'] == 3
        assert job['processed_pairs'] == job['total_pairs'] == 1
        assert job['diagnostics']['structural_pairs_skipped'] == 2
        assert job['diagnostics']['segmentation']['complete'] is False
        assert job['diagnostics']['segmentation']['panels'] == 2
        assert len(detail['findings']) == 1
        finding = detail['findings'][0]
        assert finding['kind'] == 'exact_pixels'
        parent = next(im for im in detail['images'] if not im['parent_id'])
        for side in ('a', 'b'):
            im = finding['image_' + side]
            assert im['parent_id'] == parent['id']
            assert im['bbox'] in [[20, 25, 240, 180], [300, 25, 240, 180]]
            assert finding['parent_regions_' + side] == [im['bbox']]
            assert im['metadata']['parent_width'] == 560
            assert client.get(im['parent_url']).status_code == 200
            assert client.get(im['original_url']).content == content.getvalue()
        diagnostics = client.get(f'/api/jobs/{job["id"]}/diagnostics?limit=1').json()
        assert diagnostics['total'] == 1
        assert diagnostics['pairs'][0]['status'] == 'verified_findings'
        assert client.get(f'/api/jobs/{job["id"]}/diagnostics?limit=0').status_code == 400
        report = client.get(f'/api/jobs/{job["id"]}/report?format=json').json()
        assert report['findings'][0]['parent_regions_a'] == finding['parent_regions_a']


def test_existing_database_additive_migration_preserves_review(tmp_path, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir()
    monkeypatch.setattr(main, 'DATA', data)
    # Create genuine old tables without v2 columns, not a v2 database fixture.
    with sqlite3.connect(data / 'labimage.sqlite3') as con:
        con.executescript('''
        CREATE TABLE jobs (id TEXT PRIMARY KEY, name TEXT NOT NULL, mode TEXT NOT NULL,
        status TEXT NOT NULL, created_at TEXT NOT NULL, finished_at TEXT, progress REAL DEFAULT 0,
        total_pairs INTEGER DEFAULT 0, processed_pairs INTEGER DEFAULT 0, images_count INTEGER DEFAULT 0,
        findings_count INTEGER DEFAULT 0, error TEXT, warnings TEXT DEFAULT '[]', settings TEXT NOT NULL, uploads TEXT NOT NULL);
        CREATE TABLE images (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, name TEXT NOT NULL,
        source TEXT NOT NULL, page INTEGER, width INTEGER NOT NULL, height INTEGER NOT NULL,
        path TEXT NOT NULL, representation TEXT NOT NULL, source_key TEXT NOT NULL,
        in_library INTEGER DEFAULT 0, created_at TEXT NOT NULL);
        CREATE TABLE findings (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, image_a TEXT NOT NULL,
        image_b TEXT NOT NULL, result TEXT NOT NULL, review_status TEXT DEFAULT 'pending',
        review_note TEXT DEFAULT '', reviewed_at TEXT);
        INSERT INTO jobs (id,name,mode,status,created_at,settings,uploads)
        VALUES ('old','legacy','batch','completed','2020','{}','[]');
        INSERT INTO findings (id,job_id,image_a,image_b,result,review_status,review_note)
        VALUES ('evidence','old','a','b','{}','dismissed','retain my review');
        ''')
    main.initialize()
    with main.db() as con:
        assert con.execute('SELECT review_note FROM findings').fetchone()[0] == 'retain my review'
        assert con.execute('SELECT review_status FROM findings').fetchone()[0] == 'dismissed'
        job = con.execute('SELECT * FROM jobs').fetchone()
        assert job['status'] == 'completed'
        assert json.loads(job['diagnostics']) == {}
        assert {'parent_id', 'bbox', 'metadata'} <= {r['name'] for r in con.execute('PRAGMA table_info(images)')}


def test_siblings_are_not_structural_duplicates():
    base = {'job_id': 'j', 'source_key': '0', 'page': 1}
    parent = {**base, 'id': 'p', 'representation': 'embedded', 'parent_id': None}
    first = {**base, 'id': 'a', 'representation': 'panel', 'parent_id': 'p'}
    second = {**first, 'id': 'b'}
    assert main.skip_structural_pair(parent, first)
    assert main.skip_structural_pair(first, parent)
    assert not main.skip_structural_pair(first, second)


def test_api_keeps_multiple_independent_regions_and_parallel_pair_outcomes(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path / 'data')
    # Two reused patches with different mappings in otherwise independent fields.
    def texture(seed):
        rng = np.random.default_rng(seed)
        arr = cv2.GaussianBlur(rng.normal(128, 60, (480, 640)).astype(np.float32), (0, 0), .85)
        for _ in range(85):
            x, y = int(rng.integers(15, 625)), int(rng.integers(15, 465))
            cv2.circle(arr, (x, y), int(rng.integers(3, 16)), float(rng.integers(20, 225)), 2)
        return np.clip(arr, 0, 255).astype(np.uint8)
    a, b, c = texture(100), texture(101), texture(1129)
    b[25:195, 330:525] = a[25:195, 25:220]
    b[270:440, 35:265] = a[30:200, 300:530]
    uploads = []
    for name, arr in [('a.png', a), ('b.png', b), ('c.png', c)]:
        stream = io.BytesIO()
        Image.fromarray(arr).save(stream, 'PNG')
        uploads.append(('files', (name, stream.getvalue())))
    with TestClient(main.app) as client:
        identifier = client.post('/api/jobs', files=uploads,
                                 data={'auto_panels': 'false', 'add_to_library': 'false'}).json()['job_id']
        detail = wait(client, identifier)
        assert detail['job']['status'] == 'completed', detail['job']
        assert detail['job']['total_pairs'] == detail['job']['processed_pairs'] == 3
        assert len(detail['findings']) == 2
        assert {f['region_index'] for f in detail['findings']} == {1, 2}
        assert {f['image_a']['name'] for f in detail['findings']} == {'a.png'}
        assert {f['image_b']['name'] for f in detail['findings']} == {'b.png'}
        diagnostics = client.get(f'/api/jobs/{identifier}/diagnostics').json()
        assert diagnostics['total'] == 3
        assert sum(p['status'] == 'verified_findings' for p in diagnostics['pairs']) == 1
        assert sum(detail['job']['diagnostics']['status_counts'].values()) == 3


def test_cancel_flushes_diagnostics_between_periodic_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path / 'data')
    original_update = main.update_job

    def cancel_after_five(job_id, **values):
        original_update(job_id, **values)
        if values.get('processed_pairs') == 5:
            main.CANCELLED.add(job_id)

    monkeypatch.setattr(main, 'update_job', cancel_after_five)
    stream = io.BytesIO()
    Image.fromarray(np.random.default_rng(614).integers(15, 225, (90, 120, 3), dtype=np.uint8)).save(stream, 'PNG')
    with TestClient(main.app) as client:
        identifier = client.post('/api/jobs', data={'auto_panels': 'false', 'add_to_library': 'true'},
                                 files=[('files', (f'{i}.png', stream.getvalue())) for i in range(4)]).json()['job_id']
        detail = wait(client, identifier)
        assert detail['job']['status'] == 'cancelled'
        assert detail['job']['processed_pairs'] == 5
        assert sum(detail['job']['diagnostics']['status_counts'].values()) == 5
        assert client.get(f'/api/jobs/{identifier}/diagnostics').json()['total'] == 5
        assert client.get('/api/status').json()['library_count'] == 0
