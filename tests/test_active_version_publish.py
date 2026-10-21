"""Regression tests for R1/R2 in docs/PROJECT_REVIEW_20261012.md.

R1: a plain duplicate upload of historical content must not roll back the active version.
R2: retrying an obsolete failed job must not replace a newer published version.
Synthetic parser, isolated storage, offline mode, no network.
"""
import asyncio
from pathlib import Path

from app.config import Settings
from app.service import Service
from app.storage import Store


def service(tmp_path):
    result = Service(Settings(_env_file=None, data_dir=tmp_path, app_mode='offline'))
    async def parse(record):
        text = Path(record['path']).read_text(encoding='utf-8')
        return {'chunks': [{'text': t, 'headings': [], 'locations': []} for t in text.split('\n\n')]}, 'synthetic-parser'
    result.parse = parse
    return result


async def upload(s, text, doc=None):
    rec = await s.upload('review.txt', '.txt', text.encode(), doc)
    await asyncio.gather(*s.tasks)
    return rec


def active(s, doc):
    return s.store.one('SELECT active FROM documents WHERE id=?', (doc,))['active']


def test_plain_duplicate_upload_does_not_roll_back_active_version(tmp_path):
    async def run():
        s = service(tmp_path)
        try:
            a = await upload(s, '收入为100万元。')
            b = await upload(s, '收入为300万元。', a['document_id'])
            assert active(s, a['document_id']) == b['generation_id']
            dup = await upload(s, '收入为100万元。')
            assert dup['idempotent'] is True and dup['document_id'] == a['document_id']
            assert dup['active'] is False
            assert active(s, a['document_id']) == b['generation_id']
            answer = await s.query('收入', [a['document_id']], strategy='sparse', bypass_cache=True)
            assert '300万元' in answer['evidence'][0]['text']
        finally:
            await s.close()
    asyncio.run(run())


def test_explicit_update_back_to_historical_content_still_switches(tmp_path):
    async def run():
        s = service(tmp_path)
        try:
            a = await upload(s, '收入为100万元。')
            await upload(s, '收入为300万元。', a['document_id'])
            again = await upload(s, '收入为100万元。', a['document_id'])
            assert again['idempotent'] is True and again['active'] is True
            assert active(s, a['document_id']) == a['generation_id']
        finally:
            await s.close()
    asyncio.run(run())


def test_plain_duplicate_of_current_content_stays_idempotent(tmp_path):
    async def run():
        s = service(tmp_path)
        try:
            a = await upload(s, '收入为100万元。')
            dup = await upload(s, '收入为100万元。')
            assert dup['document_id'] == a['document_id'] and dup['active'] is True
            assert active(s, a['document_id']) == a['generation_id']
        finally:
            await s.close()
    asyncio.run(run())


def test_retry_of_obsolete_failed_job_does_not_replace_newer_version(tmp_path, monkeypatch):
    async def run():
        s = service(tmp_path)
        try:
            a = await upload(s, '收入为100万元。')
            original = s.index.upsert
            def fail(*args): raise ConnectionError('synthetic outage')
            monkeypatch.setattr(s.index, 'upsert', fail)
            b = await upload(s, '收入为200万元。', a['document_id'])
            assert s.store.one('SELECT status FROM jobs WHERE id=?', (b['job_id'],))['status'] == 'failed'
            monkeypatch.setattr(s.index, 'upsert', original)
            c = await upload(s, '收入为300万元。', a['document_id'])
            assert active(s, a['document_id']) == c['generation_id']
            await s.retry(b['job_id'])
            await asyncio.gather(*s.tasks)
            job = s.store.one('SELECT status,stage FROM jobs WHERE id=?', (b['job_id'],))
            assert job['status'] == 'completed' and job['stage'] == 'superseded'
            assert active(s, a['document_id']) == c['generation_id']
            answer = await s.query('收入', [a['document_id']], strategy='sparse', bypass_cache=True)
            assert '300万元' in answer['evidence'][0]['text']
        finally:
            await s.close()
    asyncio.run(run())


def test_retry_of_latest_failed_job_still_publishes(tmp_path, monkeypatch):
    async def run():
        s = service(tmp_path)
        try:
            a = await upload(s, '收入为100万元。')
            original = s.index.upsert
            def fail(*args): raise ConnectionError('synthetic outage')
            monkeypatch.setattr(s.index, 'upsert', fail)
            b = await upload(s, '收入为200万元。', a['document_id'])
            assert active(s, a['document_id']) == a['generation_id']
            monkeypatch.setattr(s.index, 'upsert', original)
            await s.retry(b['job_id'])
            await asyncio.gather(*s.tasks)
            assert active(s, a['document_id']) == b['generation_id']
        finally:
            await s.close()
    asyncio.run(run())


def test_target_column_migrates_existing_database(tmp_path):
    import sqlite3
    con = sqlite3.connect(tmp_path / 'state.sqlite3')
    con.execute('CREATE TABLE documents(id TEXT PRIMARY KEY, name TEXT, deleted INTEGER DEFAULT 0, active TEXT)')
    con.execute("INSERT INTO documents(id,name,active) VALUES ('d','legacy','g')")
    con.commit(); con.close()
    store = Store(tmp_path)
    try:
        assert store.one("SELECT active,target FROM documents WHERE id='d'") == {'active': 'g', 'target': None}
    finally:
        store.db.close()
