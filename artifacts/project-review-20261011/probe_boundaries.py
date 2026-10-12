"""Offline review probes. Synthetic model responses; no quality claims or network."""
import asyncio
import json
import socket
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.context import select_context
from app.models import CloudModels
from app.reliability import CallBudget


def no_network(*args, **kwargs):
    raise AssertionError('Network forbidden in review probes')


original_connect = socket.socket.connect


def guarded_connect(sock, address):
    if isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1'}:
        return original_connect(sock, address)
    return no_network()


socket.create_connection = no_network
socket.socket.connect = guarded_connect
socket.getaddrinfo = no_network


async def synthetic_embedding(model, input, **kwargs):
    return SimpleNamespace(data=[{'index': i, 'embedding': [1.0, 0.0]}
                                 for i in range(len(input))], usage=None)


sys.modules['litellm'] = SimpleNamespace(aembedding=synthetic_embedding, telemetry=False)


async def run():
    settings = Settings(_env_file=None, embedding_dimension=2)
    report = {'kind': 'offline_synthetic_boundary_probes', 'network_calls': 0,
              'model_quality_validated': False, 'embedding': []}
    for count in (1280, 1281):
        model = CloudModels(settings)
        try:
            vectors = await model.embed(['synthetic'] * count)
            outcome = {'status': 'completed', 'vectors': len(vectors)}
        except Exception as exc:
            outcome = {'status': 'failed', 'error': type(exc).__name__, 'reason': str(exc)}
        report['embedding'].append({'chunks': count, 'calls': model.budget.calls, **outcome})

    async def successful_call(**kwargs):
        return True

    budget = CallBudget(settings)
    for _ in range(40):
        await budget.run(successful_call, 'synthetic-summary')
    try:
        await budget.run(successful_call, 'synthetic-aggregate')
        aggregate = 'completed'
    except TimeoutError as exc:
        aggregate = str(exc)
    report['summary_budget'] = {'successful_chunk_calls': 40, 'next_aggregate_call': aggregate,
                                'scope': 'CallBudget boundary, not end-to-end summary quality'}
    candidates = [{'id': str(i), 'text': '收入增长。' * 180, 'headings': []} for i in range(8)]
    evidence, context = select_context(candidates, '收入', settings.context_tokens)
    report['context'] = {'candidate_count': 8, 'characters_per_candidate': 900,
                         'selected_count': len(evidence),
                         'full_chunks': sum('excerpt' not in e for e in evidence),
                         'excerpt_chunks': sum('excerpt' in e for e in evidence), **context}
    path = Path(__file__).with_name('boundary-probes.json')
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    asyncio.run(run())
