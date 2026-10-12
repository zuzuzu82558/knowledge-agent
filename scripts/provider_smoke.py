"""Explicit, bounded paid-service probe, separate from offline pytest.

Uses a fresh isolated corpus. Hard caps: one search, two generation requests,
two embedding requests, no retries or fallback. Reports actual usage; currency
charges must be checked against provider billing, not inferred from success.
"""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.service import Service


async def run(args):
    if not args.allow_live or args.budget_yuan != 1:
        raise ValueError('explicit_live_consent_and_one_yuan_budget_required')
    destination = Path(args.data_dir).resolve()
    root = Path(__file__).resolve().parents[1]
    if not destination.is_relative_to(root / '.tmp') or destination.exists():
        raise ValueError('fresh_isolated_tmp_data_directory_required')
    s = Settings(app_mode='local', data_dir=destination, retry_attempts=0,
        fallback_model=None, graph_enabled=False, cache_enabled=False,
        redis_url=None, redis_token='', neo4j_uri=None, neo4j_password='',
        langfuse_public_key=None, langfuse_secret_key='',
        search_max_calls=1, search_max_results=3, web_max_pages=3,
        generation_max_tokens=400, evidence_budget_bytes=6000,
        max_model_calls=2, background_max_model_calls=1, request_timeout=120)
    import litellm
    litellm.telemetry = False
    counts = {'search':0, 'generation':0, 'embedding':0}
    originals = {'generation':litellm.acompletion, 'embedding':litellm.aembedding}

    async def call(kind, **kw):
        if counts[kind] >= 2:
            raise RuntimeError('experiment_' + kind + '_limit')
        counts[kind] += 1
        return await originals[kind](**kw)

    async def generation(**kw): return await call('generation', **kw)
    async def embedding(**kw): return await call('embedding', **kw)
    litellm.acompletion, litellm.aembedding = generation, embedding
    service = Service(s)
    search = service.search.search

    async def bounded_search(query):
        if counts['search'] >= 1: raise RuntimeError('experiment_search_limit')
        counts['search'] += 1
        return await search(query)

    service.search.search = bounded_search
    report = {'budget_yuan':1, 'retry_attempts':0, 'counts':counts,
              'models':{'generation':s.llm_model, 'embedding':s.embedding_model},
              'quality_evaluation':'not_performed', 'actual_currency_charge':'check_provider_billing',
              'citation_validation':'snapshot_existence_and_scope_not_semantic_support',
              'data_dir':str(destination), 'checks':[]}
    start = time.monotonic()
    try:
        uploaded = await service.upload('联网验收测试.txt', '.txt',
            '这是虚构的接口验收资料。测试项目代号为青松，计划于2026年11月上线。'.encode())
        await asyncio.gather(*service.tasks)
        job = service.store.one('SELECT status FROM jobs WHERE id=?', (uploaded['job_id'],))
        report['checks'].append({'name':'document_embedding', 'status':job['status']})
        if job['status'] == 'completed':
            result = await service.query('测试项目代号是什么？', [uploaded['document_id']],
                route='qa', strategy='hybrid', bypass_cache=True)
            report['checks'].append({'name':'document_rag', 'status':result['status'],
                'run_id':result['run_id'], 'claims':result['claims'], 'usage':result['usage'],
                'warnings':result['warnings'], 'retrieval':result['retrieval']})
        result = await service.query('RFC 9110 定义了什么？请用中文简短回答。', [],
            scope='web', workflow='fixed', route='qa', bypass_cache=True)
        ids = [c for claim in result['claims'] for c in claim['citations']]
        snapshots = [service.evidence(i) for i in ids]
        report['checks'].append({'name':'fixed_web_rag', 'status':result['status'],
            'run_id':result['run_id'], 'claims':result['claims'], 'usage':result['usage'],
            'warnings':result['warnings'], 'online':result.get('online'),
            'tool_trace':result.get('tool_trace'),
            'snapshot_checks':[{'id':p['id'], 'read_status':p.get('read_status'),
                'url':p.get('url'), 'fetched_at':p.get('fetched_at'),
                'hash_present':bool(p.get('body_sha256'))} for p in snapshots]})
    except Exception as exc:
        report['error_type'] = type(exc).__name__  # Never persist provider exception text.
    finally:
        await service.close()
        litellm.acompletion, litellm.aembedding = originals['generation'], originals['embedding']
        report['elapsed_ms'] = round((time.monotonic()-start)*1000)
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    passed = len(report['checks']) == 3 and all(
        c['status'] == ('completed' if c['name']=='document_embedding' else 'answered')
        for c in report['checks'])
    print(json.dumps({'passed':passed, 'counts':counts, 'report':args.report}))
    return 0 if passed else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-live', action='store_true')
    parser.add_argument('--budget-yuan', type=int, choices=[1])
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--report', default='artifacts/provider-smoke.live.json')
    sys.exit(asyncio.run(run(parser.parse_args())))
