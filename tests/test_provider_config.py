"""Provider options use test doubles; no external requests or actual credentials."""
import asyncio
import os
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.models import CloudModels
from app.cache import namespace


def test_generation_options_separated_from_embeddings(monkeypatch):
    import litellm
    calls = []

    async def generate(**kw):
        calls.append(kw)
        return SimpleNamespace(model=kw['model'], usage=None, choices=[
            SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))])

    async def embed(**kw):
        calls.append(kw)
        return SimpleNamespace(model=kw['model'], usage=None,
            data=[{'index':0, 'embedding':[1., .2]}])

    monkeypatch.setattr(litellm, 'acompletion', generate)
    monkeypatch.setattr(litellm, 'aembedding', embed)
    s = Settings(_env_file=None, embedding_dimension=2,
        llm_api_key='test-generation', embedding_api_key='test-embedding',
        llm_api_base='https://generation.example/v1',
        embedding_api_base='https://embedding.example/v1',
        llm_extra_body={'thinking':{'type':'disabled'}})
    m = CloudModels(s)
    assert asyncio.run(m.structured('return JSON', {})) == {'ok':True}
    assert asyncio.run(m.embed_query(['query'])) == [[1., .2]]
    assert calls[0]['extra_body'] == s.llm_extra_body
    assert calls[0]['api_key'] == 'test-generation'
    assert calls[1]['api_key'] == 'test-embedding'
    assert calls[0]['api_base'] != calls[1]['api_base']
    assert 'extra_body' not in calls[1]
    assert calls[0]['num_retries'] == calls[1]['num_retries'] == 0


def test_generation_options_change_cache_without_changing_embedding_contract():
    a = Settings(_env_file=None, llm_extra_body={})
    b = Settings(_env_file=None, llm_extra_body={'thinking':{'type':'disabled'}})
    assert a.fingerprint == b.fingerprint
    assert namespace([], {'generation_options':a.generation_options_fingerprint}) != namespace(
        [], {'generation_options':b.generation_options_fingerprint})


def test_litellm_does_not_load_project_dotenv():
    assert os.environ['LITELLM_MODE'] != 'DEV'
    # Explicit defaults remain independent of credentials/config in .env.
    s = Settings(_env_file=None)
    assert s.embedding_batch_size == 32
    assert s.llm_extra_body == {}


@pytest.mark.parametrize('key', ['model', 'messages', 'max_tokens', 'api_key', 'stream'])
def test_provider_options_cannot_bypass_request_controls(key):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, llm_extra_body={key:'override'})


@pytest.mark.parametrize('content,finish,code', [
    ('not-json', 'stop', 'answer_invalid_json'),
    ('[]', 'stop', 'answer_contract'),
    ('{"refused":true,"claims":[{"text":"unsupported"}]}', 'stop', 'answer_contract'),
    ('{"refused":false,"claims":[{"text":"fact","citations":["outside"]}]}', 'stop', 'invalid_citation'),
    ('{"refused":true,"claims":[]}', 'length', 'answer_truncated'),
])
def test_answer_validation_preserves_safe_failure_code(monkeypatch, content, finish, code):
    import litellm
    async def generate(**kw):
        return SimpleNamespace(model='fixture', usage=None, choices=[
            SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))])
    monkeypatch.setattr(litellm, 'acompletion', generate)
    model = CloudModels(Settings(_env_file=None))
    with pytest.raises(ValueError, match=code):
        asyncio.run(model.answer('question', [{'id':'allowed', 'text':'evidence'}]))
    assert model.usage[-1]['validation'] == 'failed'
    assert model.usage[-1]['error_code'] == code
    assert content not in str(model.usage)


def test_generation_failure_run_keeps_error_code_and_evidence(tmp_path, monkeypatch):
    from app.models import AnswerValidationError
    from app.service import Service
    async def embed(self, texts): return [[1., .2] for _ in texts]
    async def answer(self, question, evidence): raise AnswerValidationError('invalid_citation')
    monkeypatch.setattr(CloudModels, 'embed', embed)
    monkeypatch.setattr(CloudModels, 'answer', answer)
    service = Service(Settings(_env_file=None, app_mode='local', data_dir=tmp_path,
        model_api_key='test-only', embedding_dimension=2))
    async def run():
        try:
            upload = await service.upload('fixture.txt', '.txt', '接口测试资料。'.encode())
            await asyncio.gather(*service.tasks)
            r = await service.query('接口测试资料', [upload['document_id']], route='qa', strategy='sparse')
            assert r['status'] == 'generation_failed' and r['evidence'] and not r['claims']
            assert r['generation_error']['code'] == 'invalid_citation'
            assert service.run(r['run_id'])['generation_error'] == r['generation_error']
        finally:
            await service.close()
    asyncio.run(run())


def test_online_budget_preserves_later_sources_and_exact_excerpt_offsets():
    from app.context import select_context, context_cost
    pages = [{'id':'web:'+str(i), 'source_type':'web', 'text':'Relevant evidence. '*1000,
              'title':'fixture', 'url':'https://fixture.example/'+str(i)} for i in range(3)]
    original = [dict(p) for p in pages]
    greedy, _ = select_context(pages, 'Relevant evidence', 3000)
    balanced, audit = select_context(pages, 'Relevant evidence', 3000, balanced=True)
    assert len(greedy) == 1 and len(balanced) == 3
    assert context_cost(balanced) <= 3000
    assert audit['allocation'] == 'remaining_candidates_equal_shares_v1'
    assert pages == original
    for source, excerpt in zip(pages, balanced):
        bounds = excerpt['excerpt']
        assert source['text'][bounds['start']:bounds['end']] == excerpt['text']


def test_balanced_budget_rolls_unused_share_forward_and_respects_top_k():
    from app.context import select_context, context_cost
    pages = [{'id':'a','text':'short evidence'}, {'id':'b','text':'long evidence. '*1000},
             {'id':'c','text':'excluded evidence'}]
    selected, audit = select_context(pages, 'evidence', 1200, top_k=2, balanced=True)
    assert [p['id'] for p in selected] == ['a','b']
    assert context_cost(selected) <= 1200
    assert audit['decisions'][1]['allocation_bytes'] > 600
    assert audit['decisions'][2]['reason'] == 'final_evidence_limit'
