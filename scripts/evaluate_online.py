"""Compare documents / fixed online / model-selected Agent on frozen page snapshots."""
import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.config import Settings
from app.service import Service
from app.web_replay import SnapshotReplay, CaptureSearch, CaptureReader
from app.evaluation import citation_metrics


async def run(args):
    if args.live_network and not args.allow_model:
        raise ValueError('capture_requires_configured_local_mode_and_explicit_model_budget')
    if args.allow_model and (not args.model_call_budget or args.model_call_budget<1):
        raise ValueError('explicit_model_call_budget_required')
    if args.live_network and (not args.search_call_budget or args.search_call_budget<1):
        raise ValueError('explicit_search_call_budget_required')
    base=Settings() if args.allow_model else Settings(_env_file=None,app_mode='offline')
    if args.allow_model and base.app_mode!='local':
        raise ValueError('online_evaluation_uses_local_storage_mode_with_configured_models')
    source=Path(args.snapshots)
    bundle=json.loads(source.read_text(encoding='utf-8'))
    if bundle.get('schema')!='web-eval-snapshot-v1':raise ValueError('snapshot_schema')
    report={'schema':'online-evaluation-v1','snapshot_file_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'snapshot_provenance':bundle.get('provenance'),'mode':base.app_mode,'rows':[],
        'human_review':'pending','semantic_support':'not_judged','real_quality_validated':False,
        'notes':['Replay fails for unrecorded queries; it never fabricates results or falls back to live network.',
                 'Offline agent is not run. Fixture replay validates plumbing, not real web search quality.']}
    digest=hashlib.sha256()
    for source_file in sorted((ROOT/'app').rglob('*.py')):
        digest.update(str(source_file.relative_to(ROOT)).encode());digest.update(source_file.read_bytes())
    report['application_source_sha256']=digest.hexdigest()
    captured={'schema':'web-eval-snapshot-v1','provenance':'live_api_capture_unreviewed','cases':[]}
    search_meter={'calls':0,'limit':args.search_call_budget or 0}
    (ROOT/'.tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='online-eval-',dir=ROOT/'.tmp') as directory:
        import uuid
        settings=base.model_copy(update={'data_dir':Path(directory),'collection_prefix':'online_'+uuid.uuid4().hex[:10],
                                         'cache_enabled':False,'graph_enabled':False})
        service=Service(settings)
        if args.allow_model: service.model_meter={'calls':0,'limit':args.model_call_budget}
        live_search,live_reader=service.search,service.reader
        try:
            documents={}
            for case in bundle['cases']:
                name=case['document']
                if name not in documents:
                    path=(ROOT/'examples'/name).resolve()
                    if path.parent!=ROOT/'examples':raise ValueError('example_document_scope')
                    uploaded=await service.upload(name,path.suffix,path.read_bytes())
                    await asyncio.gather(*service.tasks)
                    if service.store.one('SELECT status FROM jobs WHERE id=?',(uploaded['job_id'],))['status']!='completed':
                        raise ValueError('evaluation_ingest_failed')
                    documents[name]=uploaded['document_id']
                frozen=case
                if args.live_network:
                    # Capture discovery/reading once, then all compared flows use it.
                    frozen={**case,'searches':[],'pages':[]}
                    service.search=CaptureSearch(live_search,frozen,search_meter)
                    service.reader=CaptureReader(live_reader,frozen)
                    capture=await service.query(case['question'],[],scope='web',route='qa',bypass_cache=True)
                    frozen['capture_status']=capture['status'];frozen['capture_tools']=capture.get('tool_trace',[])
                    captured['cases'].append(frozen)
                for scope,workflow in [('documents','fixed'),('combined','fixed'),('combined','agent')]:
                    row={'id':case['id'],'scope':scope,'workflow':workflow,'reference':case.get('reference'),
                         'task_completion':None,'human_citation_support':None,'semantic_support':'not_judged'}
                    if workflow=='agent' and not args.allow_model:
                        row.update(status='not_run_offline',reason='autonomous_tool_choice_requires_real_model')
                    else:
                        replay=SnapshotReplay(frozen);service.search=replay;service.reader=replay
                        result=await service.query(case['question'],[documents[name]],scope=scope,workflow=workflow,
                                                   route='qa',strategy=args.strategy,bypass_cache=True,rerank=args.rerank)
                        row.update(status=result['status'],run=result,citation_metrics=citation_metrics(result),
                            latency_ms=result['elapsed_ms'],usage=result['usage'],cost=result['cost'],
                            failure_cases=[t for t in result.get('tool_trace',[]) if t['status']!='ok'],
                            replay_searches=replay.search_calls,replay_reads=replay.read_calls)
                    report['rows'].append(row)
            report['status']='completed_with_pending_semantic_and_human_review'
        except Exception as exc:
            report.update(status='failed',error=type(exc).__name__)
            raise
        finally:
            report['model_meter']=service.model_meter;report['search_meter']=search_meter
            report['contract']=settings.contract
            await service.close()
            target=ROOT/args.report;target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            if args.live_network:
                destination=ROOT/args.capture_output;destination.parent.mkdir(parents=True,exist_ok=True)
                destination.write_text(json.dumps(captured,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':report['status'],'runs':len(report['rows']),'real_quality_validated':False}))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--snapshots',default='eval/web-snapshots.fixture.json')
    parser.add_argument('--report',default='artifacts/online-evaluation-offline.json')
    parser.add_argument('--allow-model',action='store_true')
    parser.add_argument('--model-call-budget',type=int)
    parser.add_argument('--live-network',action='store_true')
    parser.add_argument('--search-call-budget',type=int)
    parser.add_argument('--capture-output',default='artifacts/web-snapshots.live.json')
    parser.add_argument('--strategy',choices=['sparse','dense','hybrid'],default='hybrid')
    parser.add_argument('--rerank',action='store_true')
    asyncio.run(run(parser.parse_args()))
