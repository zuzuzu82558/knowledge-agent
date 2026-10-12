import sqlite3
import time

from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.storage import Store


def test_generation_migration_preserves_references_and_backup(tmp_path):
    with sqlite3.connect(tmp_path / 'state.sqlite3') as db:
        db.executescript('''
        CREATE TABLE generations(id TEXT PRIMARY KEY, version TEXT, config TEXT, contract TEXT,
          parse_status TEXT, vector_status TEXT, graph_status TEXT DEFAULT 'not_in_m1',
          summary_status TEXT DEFAULT 'not_in_m1', parsed TEXT, UNIQUE(version,config));
        INSERT INTO generations VALUES ('old','v','cfg','contract','ready','ready','ready','ready','original');
        CREATE TABLE documents(id TEXT PRIMARY KEY,name TEXT,deleted INTEGER DEFAULT 0,active TEXT);
        INSERT INTO documents VALUES ('doc','name',0,'old');
        ''')
    store = Store(tmp_path)
    original = store.one('SELECT * FROM generations WHERE id=?', ('old',))
    assert original['parsed'] == 'original'
    assert store.one('SELECT active FROM documents')['active'] == 'old'
    store.execute("INSERT INTO generations(id,version,config) VALUES ('new','v','cfg')")
    store.db.close()
    with sqlite3.connect(tmp_path / 'state.pre-rebuild-v3.sqlite3') as backup:
        assert backup.execute('SELECT id FROM generations').fetchall() == [('old',)]
    store = Store(tmp_path)
    assert store.one('SELECT * FROM generations WHERE id=?', ('old',)) == original
    assert len(store.all('SELECT * FROM generations')) == 2
    store.db.close()


def test_history_original_download_and_failed_job_contract(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, app_mode='offline')
    with TestClient(create_app(settings)) as client:
        def upload(name, content):
            r = client.post('/api/documents', files={'file': (name, content)}).json()
            for _ in range(200):
                if client.get('/api/jobs/' + r['job_id']).json()['status'] in ('completed', 'failed'):
                    return r
                time.sleep(.05)
            raise AssertionError('job timeout')
        doc = upload('original.html', '<p>Unique999 revenue</p>')
        run = client.post('/api/query', json={'question': 'Unique999', 'document_ids': [doc['document_id']]}).json()
        assert client.get('/api/runs').json()[0]['run_id'] == run['run_id']
        from datetime import datetime
        created = datetime.fromisoformat(run['created_at'])
        assert created.tzinfo is not None
        cached = client.post('/api/query', json={'question': 'Unique999', 'document_ids': [doc['document_id']]}).json()
        assert cached['cache']['status'] == 'exact'
        assert datetime.fromisoformat(cached['created_at']) > created
        assert client.get('/api/runs').json()[0]['created_at'] == cached['created_at']
        # Older stored runs have no timestamp; keep them readable without inventing one.
        import json
        legacy = dict(run)
        legacy.pop('created_at')
        client.app.state.service.store.execute('UPDATE runs SET body=? WHERE id=?', (json.dumps(legacy), run['run_id']))
        old = next(r for r in client.get('/api/runs').json() if r['run_id'] == run['run_id'])
        assert old['created_at'] is None
        file_url = '/api/versions/' + doc['version_id'] + '/file'
        response = client.get(file_url)
        assert response.content == b'<p>Unique999 revenue</p>'
        assert response.headers['content-disposition'].startswith('attachment')
        assert response.headers['content-type'] == 'application/octet-stream'
        bad = upload('broken.pdf', 'broken')
        rows = client.get('/api/documents').json()
        failed = next(r for r in rows if r['id'] == bad['document_id'])
        assert failed['job_status'] == 'failed' and failed['job_error']
        assert client.get('/api/jobs').json()[0]['document_name'] == 'broken.pdf'
        client.delete('/api/documents/' + doc['document_id'])
        assert client.get(file_url).status_code == 410
        assert client.get('/api/runs/' + run['run_id']).status_code == 404
        assert client.get('/api/runs').json() == []
