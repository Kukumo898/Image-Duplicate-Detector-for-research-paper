"""Persistence/export contracts, independent of algorithm accuracy claims."""
import csv
import io
import json
import sqlite3
import time

import numpy as np
from PIL import Image
from fastapi.testclient import TestClient

from app import main


def completed(client, identifier):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        detail = client.get('/api/jobs/' + identifier).json()
        if detail['job']['status'] not in {'queued', 'running', 'cancelling'}:
            assert detail['job']['status'] == 'completed', detail['job']
            return detail
        time.sleep(.03)
    raise AssertionError('job timed out')


def test_masks_lossless_coordinate_trace_and_source_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path / 'isolated')
    rgb = np.random.default_rng(7401).integers(20, 220, (260, 1600, 3), dtype=np.uint8)
    stream = io.BytesIO(); Image.fromarray(rgb).save(stream, 'PNG'); content = stream.getvalue()
    with TestClient(main.app) as client:
        identifier = client.post('/api/jobs', data={'mode': 'pair', 'add_to_library': 'false'},
            files=[('files', ('first.png', content)), ('files', ('second.png', content))]).json()['job_id']
        detail = completed(client, identifier)
        assert detail['job']['processed_pairs'] == 1
        assert detail['classification_counts']['data_supported'] == 1
        finding = detail['findings'][0]
        for side in ('a', 'b'):
            layer = client.get(f'/api/findings/{finding["id"]}/data-evidence/{side}/mask')
            assert layer.status_code == 200, layer.text
            common = np.array(Image.open(io.BytesIO(layer.content)))
            assert int((common > 0).sum()) == finding['metrics']['annotation_verification']['coverage_'+side]['effective_pixels']
            assert client.get(finding['data_evidence_urls'][side]).status_code == 200
        assert client.get('/api/status').json()['library_count'] == 0
        for image in detail['images']:
            metadata = image['annotations']
            assert metadata['analysis_width'] == 1280
            assert metadata['analysis_to_original'][0][0] == 1.25
            assert metadata['uncertain_retained']
            assert client.get(image['original_url']).content == content
            original = np.array(Image.open(io.BytesIO(client.get(image['url']).content)))
            assert np.array_equal(rgb, original)
            masks = {}
            for layer in ('annotation', 'data', 'uncertain'):
                response = client.get(metadata['layers'][layer]); assert response.status_code == 200
                masks[layer] = np.array(Image.open(io.BytesIO(response.content)))
                assert set(np.unique(masks[layer])) <= {0, 255}
                assert masks[layer].shape == (metadata['analysis_height'], 1280)
            assert np.all(masks['data'][masks['uncertain'] > 0] == 255)
            assert client.get(f'/api/images/{image["id"]}/annotations/not-a-layer').status_code == 404
        report = client.get(f'/api/jobs/{identifier}/report?format=html').text
        assert 'data:image/png;base64,' in report
        assert '数据支持线索' in report
        assert '二值标注遮罩' in report
        assert '/api/images/' not in report.split('<style>')[1].split('</style>')[0]


def test_all_three_classes_persist_and_export_without_hiding(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path / 'isolated')
    # Explicit interface fixture, not evidence of scientific performance.
    def compare(self, a, b, **kwargs):
        findings = [dict(kind='exact_pixels', score=100-i, method='api_contract_fixture',
            explanation='Synthetic API fixture, not an algorithm outcome',
            evidence_class=classification, regions_a=[[0,0,a.width,a.height]],
            regions_b=[[0,0,b.width,b.height]], matches=[], transform=np.eye(3).tolist(),
            metrics={'annotation_verification': {'annotation_version': main.ANNOTATION_VERSION,
                'coordinate_system': 'analysis_view_xy_pixels', 'reasons': ['fixture']}})
            for i, classification in enumerate(('data_supported', 'annotation_only', 'inconclusive'))]
        return {'findings': findings, 'diagnostics': {'status': 'verified_findings', 'rejections': {}}}
    monkeypatch.setattr(main.Detector, 'compare_detailed', compare)
    stream = io.BytesIO(); Image.new('RGB', (80, 70), '#426363').save(stream, 'PNG')
    with TestClient(main.app) as client:
        identifier = client.post('/api/jobs', data={'mode':'pair','add_to_library':'false'},
            files=[('files', (f'{i}.png', stream.getvalue())) for i in range(2)]).json()['job_id']
        detail = completed(client, identifier)
        expected = {'data_supported':1, 'annotation_only':1, 'inconclusive':1}
        assert detail['classification_counts'] == expected
        assert detail['job']['diagnostics']['classification_counts'] == expected
        assert detail['job']['findings_count'] == len(detail['findings']) == 3
        assert client.get(f'/api/jobs/{identifier}/diagnostics').json()['total'] == 1
        exported = client.get(f'/api/jobs/{identifier}/report?format=json').json()
        assert exported['report_version'] == '3.0'
        assert exported['classification_counts'] == expected
        rows = list(csv.DictReader(io.StringIO(client.get(f'/api/jobs/{identifier}/report?format=csv').text.lstrip('\ufeff'))))
        assert {r['evidence_class'] for r in rows} == set(expected)
        assert all(json.loads(r['数据区验证JSON'])['reasons'] == ['fixture'] for r in rows)


def test_v2_database_migration_does_not_relabel_historical_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path / 'isolated')
    main.initialize()
    with main.db() as connection:
        connection.execute("INSERT INTO jobs(id,name,mode,status,created_at,settings,uploads) VALUES('old','old','pair','completed','2020','{}','[]')")
        for identifier in ('a', 'b'):
            connection.execute("INSERT INTO images(id,job_id,name,source,width,height,path,representation,source_key,created_at) VALUES(?,'old',?,'old',80,70,'not-needed','image','0','2020')", (identifier, identifier))
        connection.execute("INSERT INTO findings(id,job_id,image_a,image_b,result,review_status,review_note) VALUES('f','old','a','b',?,'dismissed','keep review')", (json.dumps({'kind':'exact_pixels','score':100,'metrics':{}}),))
        connection.execute('ALTER TABLE images DROP COLUMN annotations')
        before = dict(connection.execute("SELECT * FROM findings WHERE id='f'").fetchone())
    main.initialize()
    with TestClient(main.app) as client:
        historical = client.get('/api/jobs/old').json()
        assert historical['classification_counts'] == {'legacy_unclassified':1}
        assert 'evidence_class' not in historical['findings'][0]
        assert all(not image['annotations'] for image in historical['images'])
        assert client.get('/api/images/a/annotations/data').status_code == 404
    with main.db() as connection:
        assert dict(connection.execute("SELECT * FROM findings WHERE id='f'").fetchone()) == before


def test_new_comparison_against_real_legacy_library_can_export_all_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'DATA', tmp_path / 'isolated')
    rgb = np.random.default_rng(7701).integers(20, 220, (160, 220, 3), dtype=np.uint8)
    stream = io.BytesIO(); Image.fromarray(rgb).save(stream,'PNG'); content=stream.getvalue()
    with TestClient(main.app) as client:
        old_id = client.post('/api/jobs',data={'mode':'pair','add_to_library':'true'},
            files=[('files',(f'{i}.png',content)) for i in range(2)]).json()['job_id']
        old = completed(client, old_id)
        # Recreate genuine v2 absence of masks/result classification in the
        # isolated DB, preserving files and all old task/review fields.
        with main.db() as con:
            old_finding = dict(con.execute('SELECT * FROM findings WHERE job_id=?',(old_id,)).fetchone())
            historical_result = json.loads(old_finding['result'])
            historical_result.pop('evidence_class')
            historical_result['metrics'].pop('annotation_verification')
            con.execute('UPDATE findings SET result=?,review_status=?,review_note=? WHERE id=?',
                (json.dumps(historical_result), 'dismissed', 'retain legacy review', old_finding['id']))
            con.execute("UPDATE images SET annotations='{}' WHERE job_id=?",(old_id,))
            before = dict(con.execute('SELECT * FROM findings WHERE job_id=?',(old_id,)).fetchone())
        for image in old['images']:
            folder = main.DATA / 'annotations' / image['id']
            folder.rename(folder.with_name(image['id']+'_old_fixture'))
        new_id=client.post('/api/jobs',data={'mode':'batch','auto_panels':'false','include_history':'true','add_to_library':'false'},
            files=[('files',('new.png',content))]).json()['job_id']
        detail=completed(client,new_id)
        assert len(detail['findings'])==2
        for finding in detail['findings']:
            assert finding['evidence_class']=='data_supported'
            for side in ('a','b'):
                assert client.get(finding['data_evidence_urls'][side]).status_code==200
        assert client.get(f'/api/jobs/{new_id}/report?format=html').status_code==200
        assert client.get(f'/api/jobs/{new_id}/report?format=json').status_code==200
        assert client.get(f'/api/jobs/{old_id}').json()['classification_counts']=={'legacy_unclassified':1}
        with main.db() as con:
            assert dict(con.execute('SELECT * FROM findings WHERE job_id=?',(old_id,)).fetchone())==before
            metadata=json.loads(con.execute('SELECT annotations FROM images WHERE id=?',(old['images'][0]['id'],)).fetchone()[0])
            assert metadata['analysis_requested_by_job']==new_id
            assert metadata['historical_findings_reclassified'] is False
