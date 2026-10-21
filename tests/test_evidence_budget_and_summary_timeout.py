"""R3/R4 回归测试（docs/PROJECT_REVIEW_20261012.md）。

R3：默认混合证据预算不能因网页元数据较大而排除全部已读取网页。
R4：全文洞察超时时，必须返回截止前已完成的章节、证据与不完整的覆盖率。
合成数据、隔离存储、离线运行，不访问网络。
"""
import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace

from app.config import Settings
from app.context import context_cost, select_context
from app.models import CloudModels
from app.service import Service
from app.web import html_snapshot

QUESTION = '示例公司2025年收入'


def mixed_candidates(doc_count=20, page_count=5):
    docs = [dict(id=str(uuid.uuid4()), text='示例公司2025年收入增长百分之十。' * 50, headings=[],
                 document_name='2025年度报告.txt', version=str(uuid.uuid4())) for _ in range(doc_count)]
    pages = [html_snapshot(('<html><meta charset="utf-8"><title>示例公司2025年年度报告财务数据与收入分析</title><main>'
                            + '示例公司2025年收入增长百分之十。' * 150 + '</main></html>').encode(),
                           f'https://example.org/financial-report/2025/article-{i}',
                           f'https://example.org/financial-report/2025/article-{i}', {}, 30000) for i in range(page_count)]
    candidates = []
    for i, doc in enumerate(docs):
        candidates.append(doc)
        if i < len(pages):
            candidates.append(pages[i])
    return candidates[:24], docs, pages


def test_default_combined_budget_keeps_read_web_pages():
    candidates, _, _ = mixed_candidates()
    selected, audit = select_context(candidates, QUESTION, 3000, 8, balanced=True)
    sources = [e.get('source_type', 'document') for e in selected]
    assert len(selected) == 8
    assert sources.count('web') >= 2 and sources.count('document') >= 2
    assert audit['used'] <= 3000 and context_cost(selected) == audit['used']
    assert audit['allocation'] == 'source_reserved_equal_shares_v2'


def test_single_source_selection_is_unchanged():
    candidates, docs, _ = mixed_candidates(page_count=0)
    selected, audit = select_context(candidates, QUESTION, 3000, 8, balanced=True)
    assert len(selected) == 8
    assert audit['allocation'] == 'remaining_candidates_equal_shares_v1'


def test_reservation_never_exceeds_budget_when_tight():
    candidates, _, _ = mixed_candidates()
    for budget in (500, 900, 1500):
        selected, audit = select_context(candidates, QUESTION, budget, 8, balanced=True)
        assert audit['used'] <= budget


def test_summary_timeout_returns_completed_sections(tmp_path, monkeypatch):
    async def run():
        s = Service(Settings(_env_file=None, data_dir=tmp_path, app_mode='offline', background_timeout=1))
        async def parse(record):
            text = Path(record['path']).read_text(encoding='utf-8')
            return {'chunks': [{'text': t, 'headings': [], 'locations': []} for t in text.split('\n\n')]}, 'synthetic-parser'
        s.parse = parse
        try:
            doc = await s.upload('review.txt', '.txt', '第一节收入100万元。\n\n第二节成本50万元。'.encode(), None)
            await asyncio.gather(*s.tasks)
            model_settings = s.s.model_copy(update={'app_mode': 'local'})
            calls = 0
            async def answer(self, question, evidence):
                nonlocal calls
                calls += 1
                if calls == 2:
                    await asyncio.sleep(5)
                return {'status': 'answered', 'claims': [{'text': evidence[0]['text'], 'citations': [evidence[0]['id']]}]}
            monkeypatch.setattr(CloudModels, 'answer', answer)
            from app import service as service_module
            original = service_module.insights
            async def local_insights(svc, generations, model, **kwargs):
                return await original(SimpleNamespace(s=model_settings, store=svc.store), generations, model, **kwargs)
            monkeypatch.setattr(service_module, 'insights', local_insights)
            result = await s.query('全文洞察', [doc['document_id']], route='summary')
            assert result['status'] == 'request_timeout'
            assert len(result['evidence']) == 1 and '第一节收入100万元' in result['evidence'][0]['text']
            assert result['claims'] and result['claims'][0]['citations'] == [result['evidence'][0]['id']]
            coverage = result['coverage']
            assert coverage['interrupted'] is True
            assert coverage['visited_chunks'] == 1 and coverage['total_chunks'] == 2
            assert coverage['complete_sections'] == 0
            assert any(w.startswith('summary_interrupted') for w in result['warnings'])
            history = s.store.one('SELECT body FROM runs ORDER BY rowid DESC LIMIT 1')['body']
            assert '第一节收入100万元' in history
        finally:
            await s.close()
    asyncio.run(run())
