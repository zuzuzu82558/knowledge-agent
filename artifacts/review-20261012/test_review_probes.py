"""Independent defect reproductions. Passing means the described defect exists.

Synthetic providers, isolated storage, and no external network.
"""
import asyncio
import json
import socket
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from app.config import Settings
from app.context import select_context
from app.models import CloudModels
from app.service import Service
from app.web import html_snapshot


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    connect, resolve = socket.socket.connect, socket.getaddrinfo
    def guarded_connect(sock, address):
        assert not isinstance(address, tuple) or address[0] in {'localhost', '127.0.0.1', '::1'}
        return connect(sock, address)
    def guarded_resolve(host, *args, **kwargs):
        assert host in {'localhost', '127.0.0.1', '::1', None}
        return resolve(host, *args, **kwargs)
    monkeypatch.setattr(socket.socket, 'connect', guarded_connect)
    monkeypatch.setattr(socket, 'getaddrinfo', guarded_resolve)


def service(tmp_path, **kwargs):
    result = Service(Settings(_env_file=None, data_dir=tmp_path, app_mode='offline', **kwargs))
    async def parse(record):
        text = Path(record['path']).read_text(encoding='utf-8')
        return {'chunks': [{'text': t, 'headings': [], 'locations': []} for t in text.split('\n\n')]}, 'synthetic-parser'
    result.parse = parse
    return result


async def upload(s, text, doc=None):
    rec = await s.upload('review.txt', '.txt', text.encode(), doc)
    await asyncio.gather(*s.tasks)
    return rec


def test_retry_obsolete_failed_version_replaces_newer_active_version(tmp_path, monkeypatch):
    async def run():
        s = service(tmp_path)
        try:
            old = await upload(s, '收入为100万元。')
            original = s.index.upsert
            def fail(*args): raise ConnectionError('synthetic outage')
            monkeypatch.setattr(s.index, 'upsert', fail)
            failed = await upload(s, '收入为200万元。', old['document_id'])
            assert s.store.one('SELECT status FROM jobs WHERE id=?', (failed['job_id'],))['status'] == 'failed'
            monkeypatch.setattr(s.index, 'upsert', original)
            latest = await upload(s, '收入为300万元。', old['document_id'])
            assert s.store.one('SELECT active FROM documents WHERE id=?', (old['document_id'],))['active'] == latest['generation_id']
            await s.retry(failed['job_id'])
            await asyncio.gather(*s.tasks)
            actual = s.store.one('SELECT active FROM documents WHERE id=?', (old['document_id'],))['active']
            assert actual == failed['generation_id']  # Defect: an obsolete retry rolls back the current version.
            answer = await s.query('收入', [old['document_id']], strategy='sparse', bypass_cache=True)
            assert '200万元' in answer['evidence'][0]['text']
        finally:
            await s.close()
    asyncio.run(run())


def test_new_upload_of_historical_content_silently_rolls_back_existing_document(tmp_path):
    async def run():
        s = service(tmp_path)
        try:
            old = await upload(s, '收入为100万元。')
            latest = await upload(s, '收入为300万元。', old['document_id'])
            # No document_id: the UI's "new document" operation.
            duplicate = await upload(s, '收入为100万元。')
            assert duplicate['idempotent'] is True
            assert duplicate['document_id'] == old['document_id']
            active = s.store.one('SELECT active FROM documents WHERE id=?', (old['document_id'],))['active']
            assert active == old['generation_id'] and active != latest['generation_id']
        finally:
            await s.close()
    asyncio.run(run())


def test_default_combined_budget_excludes_all_five_read_web_pages():
    docs = [dict(id=str(uuid.uuid4()), text='示例公司2025年收入增长百分之十。'*50,
                 headings=[], document_name='2025年度报告.txt', version=str(uuid.uuid4())) for _ in range(20)]
    pages = [html_snapshot(('<html><meta charset="utf-8"><title>示例公司2025年年度报告财务数据与收入分析</title><main>'
                           + '示例公司2025年收入增长百分之十。'*150 + '</main></html>').encode(),
                           f'https://example.org/financial-report/2025/article-{i}',
                           f'https://example.org/financial-report/2025/article-{i}', {}, 30000) for i in range(5)]
    candidates = []
    for i, doc in enumerate(docs):
        candidates.append(doc)
        if i < len(pages): candidates.append(pages[i])
    selected, audit = select_context(candidates[:24], '示例公司2025年收入', 3000, 8, balanced=True)
    assert len(selected) == 8
    assert not any(e.get('source_type') == 'web' for e in selected)
    rejected = {d['id'] for d in audit['decisions'] if d['selection'] == 'context_budget'}
    assert all(p['id'] in rejected for p in pages)


def test_summary_timeout_discards_already_completed_section_evidence(tmp_path, monkeypatch):
    async def run():
        s = service(tmp_path, background_timeout=1)
        try:
            doc = await upload(s, '第一节收入100万元。\n\n第二节成本50万元。')
            # Keep the existing offline index contract, while exercising model summary behavior.
            model_settings = s.s.model_copy(update={'app_mode': 'local'})
            calls = 0
            async def answer(self, question, evidence):
                nonlocal calls
                calls += 1
                if calls == 2: await asyncio.sleep(5)
                return {'status': 'answered', 'claims': [{'text': evidence[0]['text'], 'citations': [evidence[0]['id']]}]}
            monkeypatch.setattr(CloudModels, 'answer', answer)
            from app import service as service_module
            original_insights = service_module.insights
            async def local_insights(svc, generations, model):
                from types import SimpleNamespace
                return await original_insights(SimpleNamespace(s=model_settings, store=svc.store), generations, model)
            monkeypatch.setattr(service_module, 'insights', local_insights)
            result = await s.query('全文洞察', [doc['document_id']], route='summary')
            assert s.store.one('SELECT COUNT(*) AS n FROM summaries')['n'] == 1
            assert result['status'] == 'request_timeout'
            assert result['evidence'] == [] and result['claims'] == [] and 'coverage' not in result
        finally:
            await s.close()
    asyncio.run(run())
