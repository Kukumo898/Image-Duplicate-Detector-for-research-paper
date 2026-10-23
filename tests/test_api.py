"""End-to-end checks: real ingestion, detection, persistence and review exports."""
import io
import time
import threading
import csv
import hashlib

from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
import pytest

from app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path / 'data')
    with TestClient(main.app) as test_client:
        yield test_client


def image_bytes():
    im = Image.new('RGB', (240, 180), '#163f47')
    draw = ImageDraw.Draw(im)
    for index in range(25):
        x, y = (index * 47) % 210, (index * 29) % 150
        draw.ellipse((x, y, x+20, y+20), fill=(40+index*7, 160, 100))
    stream = io.BytesIO()
    im.save(stream, format='PNG')
    return stream.getvalue()


def wait_job(client, job_id):
    for _ in range(200):
        response = client.get(f'/api/jobs/{job_id}')
        assert response.status_code == 200
        detail = response.json()
        if detail['job']['status'] not in ('queued', 'running', 'cancelling'):
            return detail
        time.sleep(0.05)
    pytest.fail('Job did not finish')


def submit(client, names, **settings):
    content = image_bytes()
    response = client.post('/api/jobs', files=[('files', (name, content, 'image/png')) for name in names], data=settings)
    assert response.status_code == 202, response.text
    return wait_job(client, response.json()['job_id'])


def test_pair_review_and_portable_exports(client):
    detail = submit(client, ['<script>alert(1)</script>.png', '=formula.png'], mode='pair', add_to_library='false')
    job = detail['job']
    assert job['status'] == 'completed', job
    assert job['processed_pairs'] == 1
    assert job['sources'][0]['sha256'] == hashlib.sha256(image_bytes()).hexdigest()
    assert len(detail['findings']) == 1
    finding = detail['findings'][0]
    assert finding['kind'] == 'exact_pixels'
    for im in detail['images']:
        assert client.get(im['url']).status_code == 200
        assert client.get(im['thumbnail_url']).status_code == 200
        assert client.get(im['original_url']).content == image_bytes()
    reviewed = client.post(f'/api/findings/{finding["id"]}/review', json={'status': 'confirmed', 'note': '验证图像复用 <b>备注</b>'})
    assert reviewed.status_code == 200
    refreshed = client.get(f'/api/jobs/{job["id"]}').json()
    assert refreshed['findings'][0]['review_status'] == 'confirmed'
    report = client.get(f'/api/jobs/{job["id"]}/report?format=html')
    assert 'data:image/jpeg;base64,' in report.text
    assert '<b>备注</b>' not in report.text
    assert '&lt;b&gt;备注&lt;/b&gt;' in report.text
    csv = client.get(f'/api/jobs/{job["id"]}/report?format=csv').text
    assert "'=formula.png" in csv
    assert '未包含单张图内部复制粘贴检测' in csv
    assert client.get(f'/api/jobs/{job["id"]}/report?format=json').json()['job']['status'] == 'completed'
    assert client.get('/api/library').json()['images'] == []


def test_library_comparison_is_not_self_comparison(client):
    first = submit(client, ['seed.png'], mode='batch', add_to_library='true')
    assert first['job']['total_pairs'] == 0
    assert client.get('/api/status').json()['library_count'] == 1
    second = submit(client, ['candidate.png'], mode='batch', include_history='true', add_to_library='true')
    assert second['job']['total_pairs'] == 1
    assert len(second['findings']) == 1
    assert second['findings'][0]['image_a']['id'] != second['findings'][0]['image_b']['id']
    assert client.get('/api/status').json()['library_count'] == 2


def test_validation_and_local_request_policy(client):
    content = image_bytes()
    assert client.post('/api/jobs', files=[('files', ('a.png', content))], data={'mode': 'pair'}).status_code == 400
    assert client.post('/api/jobs', files=[('files', ('a.exe', content))]).status_code == 400
    assert client.post('/api/jobs', files=[('files', ('a.png', content))], headers={'Origin': 'https://unrelated.invalid'}).status_code == 403
    assert client.get('/api/status', headers={'Host': 'unrelated.invalid'}).status_code == 400
    assert client.get('/api/jobs/not-found').status_code == 404
    assert client.post('/api/findings/nope/review', json={'status': 'bogus'}).status_code == 400


def test_corrupt_file_never_reports_a_completed_clean_scan(client):
    response = client.post('/api/jobs', files=[('files', ('broken.png', b'not an image'))])
    detail = wait_job(client, response.json()['job_id'])
    assert detail['job']['status'] == 'failed'
    assert detail['job']['error']
    assert detail['job']['progress'] != 100
    exported = list(csv.reader(io.StringIO(client.get(f'/api/jobs/{detail["job"]["id"]}/report?format=csv').text.lstrip('\ufeff'))))
    # Additive semantic columns follow the original CSV columns. Preserve the
    # actual error by its stable header, rather than assuming it is last.
    assert exported[1][exported[0].index('任务错误')] == detail['job']['error']


def test_limit_exceeded_is_explicit(client, monkeypatch):
    monkeypatch.setattr(main, 'MAX_PAIRS', 0)
    detail = submit(client, ['a.png', 'b.png'], mode='batch')
    assert detail['job']['status'] == 'failed'
    assert '上限' in detail['job']['error']
    assert client.get('/api/status').json()['library_count'] == 0


def test_structural_pdf_overlap_rule():
    a = {'job_id': 'j', 'source_key': 's', 'page': 1, 'representation': 'page'}
    b = {**a, 'representation': 'embedded'}
    assert main.skip_structural_pair(a, b)
    assert not main.skip_structural_pair(a, {**b, 'page': 2})
    assert not main.skip_structural_pair(b, b)


def test_cancel_does_not_turn_finished_job_into_permanent_cancelling(client, monkeypatch):
    started, release = threading.Event(), threading.Event()
    original_compare = main.Detector.compare_detailed

    def delayed_compare(self, a, b, **kwargs):
        started.set()
        assert release.wait(10)
        return original_compare(self, a, b, **kwargs)

    monkeypatch.setattr(main.Detector, 'compare_detailed', delayed_compare)
    data = image_bytes()
    response = client.post('/api/jobs', files=[('files', ('a.png', data)), ('files', ('b.png', data))])
    job_id = response.json()['job_id']
    try:
        assert started.wait(10)
        assert client.post(f'/api/jobs/{job_id}/cancel').status_code == 200
    finally:
        release.set()
    detail = wait_job(client, job_id)
    assert detail['job']['status'] == 'cancelled'
    assert client.post(f'/api/jobs/{job_id}/cancel').status_code == 409
    assert client.get(f'/api/jobs/{job_id}').json()['job']['status'] == 'cancelled'
    assert client.get('/api/status').json()['library_count'] == 0
