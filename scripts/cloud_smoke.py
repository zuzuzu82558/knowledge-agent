"""Explicitly paid cloud validation. Never targets the application's collections."""
import argparse
import asyncio
import json
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.service import Service


async def run(settings, report_path):
    report = {'kind': 'real_cloud_smoke', 'mode': settings.app_mode, 'status': 'failed',
              'checks': [], 'cleanup': 'not_started'}
    if settings.app_mode == 'offline':
        raise SystemExit('Set APP_MODE=local or cloud; offline tests are not cloud acceptance.')
    settings.collection_prefix = 'm1_smoke_' + uuid.uuid4().hex[:16]
    settings.graph_enabled = False
    settings.cache_enabled = False
    Path('.tmp').mkdir(exist_ok=True)
    service = None
    try:
        with tempfile.TemporaryDirectory(prefix='cloud-smoke-', dir='.tmp') as tmp:
            settings.data_dir = Path(tmp)
            service = Service(settings)
            try:
                a = await service.upload('smoke-a.txt', '.txt', '星河公司2025年收入为120亿元人民币，全年合并口径。'.encode())
                b = await service.upload('smoke-b.txt', '.txt', '北辰公司2025年收入为999亿元人民币。'.encode())
                await asyncio.gather(*service.tasks)
                for item in (a, b):
                    job = service.store.one('SELECT * FROM jobs WHERE id=?', (item['job_id'],))
                    if job['status'] != 'completed':
                        raise RuntimeError('cloud_ingestion_failed:' + str(job['error']))
                report['checks'].append('real_embedding_write')
                # Explicit multi-input embedding contract beyond ingestion's one-chunk batches.
                from app.models import CloudModels
                vectors = await CloudModels(settings).embed(['中文批量输入甲', '中文批量输入乙'])
                assert len(vectors) == 2 and all(len(v) == settings.embedding_dimension for v in vectors)
                report['checks'].append('real_embedding_batch_dimension')
                result = await service.query('星河公司2025年收入是多少？', [a['document_id']])
                assert result['status'] == 'answered', result['status']
                assert set(result['retrieval']['branches']) == {'dense', 'sparse'}
                assert result['evidence'] and all(e['document'] == a['document_id'] for e in result['evidence'])
                assert any('120' in c['text'] for c in result['claims'])
                report['checks'].extend(['dense_sparse_filter_rrf', 'real_generation_citations'])
                report['answer'] = result['claims']
                report['usage'] = result['usage']
                refusal = await service.query('星河公司2030年的客户数量是多少？', [a['document_id']])
                assert refusal['status'] == 'refused', 'unsupported_question_not_refused'
                report['checks'].append('real_insufficient_evidence_refusal')
                duplicate = await service.upload('duplicate.txt', '.txt', '星河公司2025年收入为120亿元人民币，全年合并口径。'.encode())
                assert duplicate['idempotent']
                assert (await service.delete(a['document_id']))['status'] == 'deleted'
                count = await asyncio.to_thread(service.index.client.count, service.index.name,
                    count_filter=service.index.scope([a['generation_id']]), exact=True)
                assert count.count == 0
                report['checks'].extend(['idempotent_upload', 'remote_delete'])
                report['status'] = 'passed'
            finally:
                if service.tasks:
                    await asyncio.gather(*service.tasks, return_exceptions=True)
                try:
                    assert service.index.name.startswith(settings.collection_prefix + '_')
                    await asyncio.to_thread(service.index.client.delete_collection, service.index.name)
                    report['cleanup'] = 'completed'
                except Exception as exc:
                    report['cleanup'] = 'failed:' + type(exc).__name__
                    report['cleanup_collection'] = service.index.name
                    report['status'] = 'failed'
                await service.close()
    except Exception as exc:
        report['error'] = type(exc).__name__
    finally:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'status':report['status'], 'cleanup':report['cleanup'], 'report':str(report_path)}))
    return report['status'] == 'passed'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-cloud', action='store_true', help='Allow paid model calls and isolated Qdrant writes')
    parser.add_argument('--report', type=Path, default=Path('data/cloud-smoke-report.json'))
    args = parser.parse_args()
    if not args.allow_cloud:
        parser.error('Explicit --allow-cloud is required; this test may incur provider charges.')
    sys.exit(0 if asyncio.run(run(Settings(), args.report)) else 1)
