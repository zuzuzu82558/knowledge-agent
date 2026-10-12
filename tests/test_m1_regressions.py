"""R1–R5 regressions, preserving the original independent review assertions."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.parsing import parse, chunk
from app.context import context_cost, model_evidence, select_context
from app.models import CloudModels
from app.storage import Store
from app.service import uid
from types import SimpleNamespace
import hashlib
import sqlite3


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, app_mode='offline', data_dir=tmp_path)
    with TestClient(create_app(settings)) as value:
        yield value


def upload(client, text, name='source.txt', document_id=None):
    response = client.post('/api/documents', files={'file': (name, text.encode())},
                          data={'document_id': document_id} if document_id else {})
    assert response.status_code == 202
    item = response.json()
    for _ in range(300):
        job = client.get('/api/jobs/' + item['job_id']).json()
        if job['status'] in ('completed', 'failed', 'interrupted'):
            assert job['status'] == 'completed', job
            return item
        time.sleep(.05)
    pytest.fail('job timeout')


def ask(client, document_id, question='研发投入'):
    response = client.post('/api/query', json={'question': question, 'document_ids': [document_id]})
    assert response.status_code == 200
    return response.json()


def test_long_chinese_retrieval_keeps_evidence(client):
    content = ('研发投入持续增长。' * 300)[:1800]
    doc = upload(client, content)
    result = ask(client, doc['document_id'])
    assert result['retrieval']['fused'], 'Expected matching retrieval candidates'
    costs = [len(json.dumps(client.get('/api/evidence/' + h['id']).json(),
                           ensure_ascii=False).encode()) for h in result['retrieval']['fused']]
    print('LONG_CHINESE', json.dumps({'status': result['status'], 'candidate_costs': costs,
                                    'budget': result['policy']['context_tokens'],
                                    'evidence_count': len(result['evidence'])}))
    assert result['evidence'], 'All matching 900-character Chinese chunks were dropped by default budget'


def test_long_csv_chunk_does_not_repeat_entire_row(tmp_path):
    path = tmp_path / 'wide.csv'
    path.write_text('公司,说明\n星河,' + '详细信息' * 550 + '\n', encoding='utf-8')
    parsed = parse(path, '.csv')
    chunks = chunk(parsed)
    original = parsed['blocks'][0]['text']
    print('LONG_CSV', json.dumps({'row_length': len(original), 'chunk_lengths': [len(c['text']) for c in chunks]}))
    assert all(original not in c['text'] for c in chunks[1:]), 'Continuation chunk prepends the entire original CSV record'


def test_pending_deletion_hides_stored_answer(client, monkeypatch):
    doc = upload(client, '研发投入为120亿元。')
    result = ask(client, doc['document_id'])
    original = client.app.state.service.index.client.delete

    def fail(*args, **kwargs):
        raise RuntimeError('offline_simulated_failure')

    monkeypatch.setattr(client.app.state.service.index.client, 'delete', fail)
    removed = client.delete('/api/documents/' + doc['document_id']).json()
    assert removed['status'] == 'cleanup_pending'
    response = client.get('/api/runs/' + result['run_id'])
    evidence_response = client.get('/api/evidence/' + result['evidence'][0]['id'])
    monkeypatch.setattr(client.app.state.service.index.client, 'delete', original)
    print('DELETE_PENDING', json.dumps({'run_status': response.status_code,
                                       'evidence_status': evidence_response.status_code,
                                       'run_still_contains_source': '120' in response.text}))
    assert response.status_code in (404, 410), 'Stored query exposes deleted-document excerpts while cleanup is pending'


def test_format_change_of_same_bytes_creates_new_generation(client):
    content = '# 财务\n\n研发投入120亿元。'
    first = upload(client, content, 'source.txt')
    second = upload(client, content, 'source.md', first['document_id'])
    print('FORMAT_CHANGE', json.dumps({'same_job': first['job_id'] == second['job_id'],
                                      'same_generation': first['generation_id'] == second['generation_id'],
                                      'idempotent': second['idempotent']}))
    assert first['generation_id'] != second['generation_id'], 'Changing parser format silently reuses TXT generation'


def test_skipped_heading_levels_keep_siblings(tmp_path):
    path = tmp_path / 'headings.md'
    path.write_text('# 总览\n\n### 第一节\n\n第一节正文\n\n### 第二节\n\n第二节正文\n', encoding='utf-8')
    parsed = parse(path, '.md')
    second = next(b for b in parsed['blocks'] if b['text'] == '第二节正文')
    print('HEADING_PATH', json.dumps(second['headings'], ensure_ascii=False))
    assert second['headings'] == ['总览', '第二节'], 'Sibling heading incorrectly becomes child when levels skip'


@pytest.mark.parametrize('budget', [500, 1000, 3000])
def test_r1_budget_matches_real_generation_payload_and_source(tmp_path, monkeypatch, budget):
    import litellm
    observed = []

    async def embed(self, texts):
        return [[1., 0., 0.] for _ in texts]

    async def complete(**kwargs):
        payload = json.loads(kwargs['messages'][1]['content'])['evidence']
        observed.append(payload)
        assert len(json.dumps(payload, ensure_ascii=False).encode()) <= budget
        assert any('120' in e['text'] for e in payload)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(
            {'refused': False, 'claims': [{'text': '关键收入为120亿元。', 'citations': [payload[0]['id']]}]})))], usage=None)

    monkeypatch.setattr(CloudModels, 'embed', embed)
    monkeypatch.setattr(litellm, 'acompletion', complete)
    settings = Settings(_env_file=None, app_mode='local', model_api_key='dummy-test-only',
                        data_dir=tmp_path, embedding_dimension=3, context_tokens=budget)
    with TestClient(create_app(settings)) as value:
        content = '背景资料。' * 210 + '关键收入为120亿元。' + '背景说明。' * 210
        doc = upload(value, content)
        result = ask(value, doc['document_id'], '关键收入')
        assert result['status'] == 'answered', result['warnings']
        assert observed == [model_evidence(result['evidence'])]
        assert context_cost(result['evidence']) == result['context']['used'] <= budget
        assert result['context']['candidate_count'] > 0
        for e in result['evidence']:
            original = value.get('/api/evidence/' + e['id']).json()
            if 'excerpt' in e:
                span = e['excerpt']
                assert e['text'] == original['text'][span['start']:span['end']]
                assert e['locations'] == original['locations']
            else:
                assert e['text'] == original['text']


def test_r1_no_recall_differs_from_unfit_metadata(tmp_path):
    settings = Settings(_env_file=None, app_mode='offline', data_dir=tmp_path, context_tokens=500)
    with TestClient(create_app(settings)) as value:
        doc = upload(value, '# ' + '超长标题' * 200 + '\n\n研发投入为120亿元。', 'long-heading.md')
        unfit = ask(value, doc['document_id'])
        assert unfit['retrieval']['fused'] and not unfit['evidence']
        assert unfit['status'] == 'context_budget_exceeded'
        assert unfit['context']['status'] == 'budget_exceeded'
        assert all(h['selection'] == 'context_budget' for h in unfit['retrieval']['fused'])
        missing = ask(value, doc['document_id'], 'zzzzabsentterm')
        assert missing['status'] == 'refused'
        assert missing['context']['status'] == 'no_candidates'
        assert missing['warnings'] != unfit['warnings']


@pytest.mark.parametrize('suffix', ['.csv', '.md', '.pdf'])
def test_r2_long_table_cells_are_bounded_and_lossless(tmp_path, monkeypatch, suffix):
    path = tmp_path / ('wide' + suffix)
    description = '详细研发说明' * 400
    headers = ['公司', '年度', '说明', '单位']
    values = ['星河', '2025', description, '万元']
    if suffix == '.csv':
        path.write_text(','.join(headers) + '\n' + ','.join(values) + '\n', encoding='utf-8')
    elif suffix == '.md':
        path.write_text('|公司|年度|说明|单位|\n|---|---|---|---|\n|' + '|'.join(values) + '|\n', encoding='utf-8')
    else:
        page = SimpleNamespace(extract_text=lambda: 'This PDF contains a long structured table.',
                               extract_tables=lambda: [ [headers, values] ])
        class PDF:
            pages = [page]
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
        monkeypatch.setattr('pdfplumber.open', lambda _: PDF())
    parsed = parse(path, suffix)
    parts = chunk(parsed)
    assert all(len(p['text']) <= 900 for p in parts)
    cell_parts = [p for p in parts if p.get('table_column') == '3:说明']
    assert len(cell_parts) > 1
    reconstructed = ''.join(p['text'][p['text_prefix_length']:] for p in cell_parts)
    assert reconstructed == description
    for p in cell_parts:
        assert '星河' in p['text'] and '2025' in p['text'] and '万元' in p['text']
        assert p['table_schema'] == ['1:公司', '2:年度', '3:说明', '4:单位']
        assert p['text'][p['text_prefix_length']:] == description[p['cell_span']['start']:p['cell_span']['end']]


def test_r2_pathological_schema_and_legacy_blocks_stay_bounded():
    cells = [{'column': '超长列名' * 500, 'value': '长数据' * 1000}]
    parsed = {'blocks': [{'kind': 'table', 'text': '单行数据' * 1000,
                         'table_cells': cells, 'headings': [], 'location': {'row': 2}}]}
    parts = chunk(parsed)
    assert all(len(p['text']) <= 900 for p in parts)
    assert ''.join(p['text'][p['text_prefix_length']:] for p in parts) == cells[0]['value']
    del parsed['blocks'][0]['table_cells']
    assert all(len(p['text']) <= 900 for p in chunk(parsed))


def test_r3_mixed_run_hidden_after_failed_delete_and_restart(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, app_mode='offline', data_dir=tmp_path)
    with TestClient(create_app(settings)) as value:
        a = upload(value, '甲研发投入120亿元。')
        b = upload(value, '乙研发投入150亿元。')
        mixed = value.post('/api/query', json={'question': '研发投入', 'document_ids': [a['document_id'], b['document_id']]}).json()
        safe = ask(value, b['document_id'])
        service = value.app.state.service
        monkeypatch.setattr(service.index.client, 'delete', lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('simulated')))
        assert value.delete('/api/documents/' + a['document_id']).json()['status'] == 'cleanup_pending'
        denied = value.get('/api/runs/' + mixed['run_id'])
        assert denied.status_code == 410 and '120' not in denied.text and '150' not in denied.text
        assert value.get('/api/runs/' + safe['run_id']).status_code == 200
        # Retained record proves protection is on read, not only an eventual purge.
        assert service.store.one('SELECT id FROM runs WHERE id=?', (mixed['run_id'],))
    with TestClient(create_app(settings)) as value:
        assert value.get('/api/runs/' + mixed['run_id']).status_code == 410
        assert value.get('/api/runs/' + safe['run_id']).status_code == 200
        assert value.delete('/api/documents/' + a['document_id']).json()['status'] == 'deleted'
        assert value.get('/api/runs/' + mixed['run_id']).status_code == 404
        assert value.get('/api/runs/' + safe['run_id']).status_code == 200


def test_r4_formats_remain_independently_idempotent(client):
    content = '# 财务\n\n研发投入120亿元。'
    txt = upload(client, content, 'source.txt')
    md = upload(client, content, 'source.md', txt['document_id'])
    assert txt['version_id'] != md['version_id']
    again = upload(client, content, 'renamed.md', txt['document_id'])
    assert again['idempotent'] and again['job_id'] == md['job_id']
    txt_again = upload(client, content, 'again.txt', txt['document_id'])
    assert txt_again['idempotent'] and txt_again['job_id'] == txt['job_id']
    txt_body = ask(client, txt['document_id'])['evidence']
    assert all(e['headings'] == [] for e in txt_body)
    upload(client, content, 'third.md', txt['document_id'])
    assert any(e['headings'] == ['财务'] for e in ask(client, txt['document_id'])['evidence'])
    rows = client.app.state.service.store.all('SELECT * FROM versions WHERE document=?', (txt['document_id'],))
    assert len(rows) == 2 and {r['suffix'] for r in rows} == {'.txt', '.md'}
    assert all(Path(r['path']).exists() for r in rows)


def legacy_database(directory):
    directory.mkdir(exist_ok=True)
    # Seed the actual previous schema, IDs, file and references without touching business data.
    st = Store(directory)
    with st.db:
        st.db.execute('DROP TABLE versions')
        st.db.execute('''CREATE TABLE versions(id TEXT PRIMARY KEY, document TEXT, sha TEXT, suffix TEXT, path TEXT,
                      UNIQUE(document,sha))''')
        st.db.execute('PRAGMA user_version=0')
    content = '# 总览\n\n### 第一节\n\n研发投入120亿元。\n\n### 第二节\n\n研发投入150亿元。'
    sha = hashlib.sha256(content.encode()).hexdigest()
    version = uid('legacy-document', sha)
    path = directory / 'files' / (version + '.md')
    path.parent.mkdir(exist_ok=True)
    path.write_text(content, encoding='utf-8')
    old_contract = {'parser': 'm1-v1', 'chunker': 'section-900-v1',
                    'sparse': 'jieba-0.42.1-bm25-k1=1.5-b=0-qdrant-idf-v1',
                    'mode': 'offline', 'embedding': 'openai/text-embedding-3-small',
                    'revision': 'provider-default', 'dimension':1536, 'distance':'Cosine',
                    'normalization':'qdrant-cosine', 'endpoint':None}
    old_fingerprint = hashlib.sha256(json.dumps(old_contract, sort_keys=True).encode()).hexdigest()[:16]
    generation = uid(version, old_fingerprint)
    st.execute('INSERT INTO documents(id,name,active) VALUES (?,?,?)', ('legacy-document', 'old.md', generation))
    st.execute('INSERT INTO versions VALUES (?,?,?,?,?)', (version, 'legacy-document', sha, '.md', str(path.resolve())))
    st.execute('INSERT INTO generations(id,version,config,contract,parse_status,vector_status) VALUES (?,?,?,?,?,?)',
               (generation, version, old_fingerprint, json.dumps(old_contract), 'ready', 'ready'))
    st.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', ('legacy-job', generation, 'completed', 'ready', None))
    old_chunk = {'id': 'legacy-chunk', 'text': '研发投入150亿元。', 'headings': ['总览', '第一节', '第二节'],
                 'locations': [{'line_start':11}], 'generation':generation, 'document':'legacy-document', 'version':version}
    st.execute('INSERT INTO chunks VALUES (?,?,?)', ('legacy-chunk', generation, json.dumps(old_chunk)))
    st.execute('INSERT INTO runs VALUES (?,?)', ('legacy-run', json.dumps({'snapshot':{'documents':['legacy-document']}, 'evidence':[old_chunk]})))
    st.db.close()
    return content, version, generation, old_fingerprint


def test_r4_legacy_migration_preserves_references_and_is_idempotent(tmp_path):
    content, version, generation, _ = legacy_database(tmp_path)
    st = Store(tmp_path)
    assert st.one('SELECT * FROM versions')['id'] == version
    assert st.one('SELECT * FROM generations')['version'] == version
    assert st.one('SELECT * FROM jobs')['generation'] == generation
    assert st.one('SELECT * FROM chunks')['id'] == 'legacy-chunk'
    assert st.one('SELECT * FROM runs')['id'] == 'legacy-run'
    assert st.one('PRAGMA user_version')['user_version'] == 2
    backup_path = tmp_path / 'state.pre-format-v2.sqlite3'
    assert backup_path.exists()
    with sqlite3.connect(backup_path) as backup:
        assert backup.execute('SELECT id FROM versions').fetchone()[0] == version
    backup_content = backup_path.read_bytes()
    sha = hashlib.sha256(content.encode()).hexdigest()
    st.execute('INSERT INTO versions VALUES (?,?,?,?,?)', ('new-txt', 'legacy-document', sha, '.txt', 'synthetic-path'))
    with pytest.raises(sqlite3.IntegrityError):
        st.execute('INSERT INTO versions VALUES (?,?,?,?,?)', ('duplicate-md', 'legacy-document', sha, '.md', 'not-used'))
    st.db.rollback()
    st.db.close()
    reopened = Store(tmp_path)
    assert len(reopened.all('SELECT * FROM versions')) == 2
    assert backup_path.read_bytes() == backup_content
    reopened.db.close()


def test_r4_r5_rebuild_legacy_generation_without_changing_old_citations(tmp_path):
    _, version, generation, old_fingerprint = legacy_database(tmp_path)
    settings = Settings(_env_file=None, app_mode='offline', data_dir=tmp_path)
    assert settings.fingerprint != old_fingerprint
    with TestClient(create_app(settings)) as value:
        assert value.get('/api/evidence/legacy-chunk').status_code == 200
        assert value.get('/api/runs/legacy-run').status_code == 200
        assert value.post('/api/query', json={'question':'研发', 'document_ids':['legacy-document']}).status_code == 409
        response = value.post('/api/documents/legacy-document/rebuild')
        assert response.status_code == 202
        for _ in range(300):
            job = value.get('/api/jobs/' + response.json()['job_id']).json()
            if job['status'] in ('completed', 'failed'):
                break
            time.sleep(.05)
        assert job['status'] == 'completed', job
        result = ask(value, 'legacy-document')
        assert result['snapshot']['versions'] == [version]
        assert result['snapshot']['generations'] != [generation]
        assert any(e['headings'] == ['总览', '第二节'] for e in result['evidence'] if '150' in e['text'])
        assert value.get('/api/evidence/legacy-chunk').json()['headings'] == ['总览', '第一节', '第二节']
        service = value.app.state.service
        assert service.store.one('SELECT id FROM generations WHERE id=?', (generation,))
        assert service.store.one('SELECT id FROM jobs WHERE id=?', ('legacy-job',))
        assert len(service.store.all('SELECT * FROM versions')) == 1


def test_r5_heading_stack_for_skips_siblings_and_return(tmp_path):
    path = tmp_path / 'levels.md'
    cases = [(1,'总览',['总览']), (3,'甲',['总览','甲']), (3,'乙',['总览','乙']),
             (4,'子节',['总览','乙','子节']), (2,'上层',['总览','上层']),
             (1,'新总览',['新总览']), (6,'深节',['新总览','深节']), (2,'回退',['新总览','回退'])]
    path.write_text('\n\n'.join('#'*level+' '+title+'\n\n正文'+title for level,title,_ in cases), encoding='utf-8')
    parsed = parse(path, '.md')
    for _, title, expected in cases:
        assert next(b for b in parsed['blocks'] if b['text'] == '正文'+title)['headings'] == expected


def test_r1_metadata_plus_one_character_is_not_useful_evidence():
    candidate = {'id': 'source', 'text': '背景文字' * 100 + '关键收入120亿元。', 'headings': ['H' * 500]}
    budget = context_cost([{**candidate, 'text': '关'}])
    selected, context = select_context([candidate], '关键收入', budget)
    assert not selected and context['status'] == 'budget_exceeded'


def test_r4_migration_rolls_back_all_ddl_on_failure(tmp_path, monkeypatch):
    _, version, _, _ = legacy_database(tmp_path)
    connect = sqlite3.connect
    def fail_drop(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.set_authorizer(lambda action, name, *rest:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DROP_TABLE and name == 'versions' else sqlite3.SQLITE_OK)
        return connection
    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, 'connect', fail_drop)
        with pytest.raises(sqlite3.DatabaseError):
            Store(tmp_path)
    with connect(tmp_path / 'state.sqlite3') as db:
        assert db.execute('SELECT id FROM versions').fetchone()[0] == version
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='versions_format_v2'").fetchall()
    migrated = Store(tmp_path)
    assert migrated.one('PRAGMA user_version')['user_version'] == 2
    migrated.db.close()


def test_r4_legacy_same_format_upload_keeps_version_id(tmp_path):
    content, version, old_generation, _ = legacy_database(tmp_path)
    settings = Settings(_env_file=None, app_mode='offline', data_dir=tmp_path)
    with TestClient(create_app(settings)) as value:
        rebuilt = upload(value, content, 'again.md', 'legacy-document')
        assert rebuilt['version_id'] == version
        assert rebuilt['generation_id'] != old_generation
        duplicate = upload(value, content, 'renamed.md', 'legacy-document')
        assert duplicate['idempotent'] and duplicate['job_id'] == rebuilt['job_id']
        changed = upload(value, content, 'now.txt', 'legacy-document')
        assert changed['version_id'] != version
        assert changed['generation_id'] != rebuilt['generation_id']
        assert len(value.app.state.service.store.all('SELECT * FROM versions')) == 2


def test_r4_legacy_failed_rebuild_keeps_original_active_and_citations(tmp_path, monkeypatch):
    _, _, generation, _ = legacy_database(tmp_path)
    settings = Settings(_env_file=None, app_mode='offline', data_dir=tmp_path)
    with TestClient(create_app(settings)) as value:
        async def fail(*args):
            raise ValueError('synthetic_rebuild_failure')
        monkeypatch.setattr(value.app.state.service, 'parse', fail)
        response = value.post('/api/documents/legacy-document/rebuild')
        assert response.status_code == 202
        for _ in range(100):
            job = value.get('/api/jobs/' + response.json()['job_id']).json()
            if job['status'] == 'failed':
                break
            time.sleep(.05)
        assert job['status'] == 'failed'
        st = value.app.state.service.store
        assert st.one("SELECT active FROM documents WHERE id='legacy-document'")['active'] == generation
        assert value.get('/api/evidence/legacy-chunk').status_code == 200
        assert value.get('/api/runs/legacy-run').status_code == 200
