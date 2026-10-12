"""Reproducible isolated experiments. Default offline; live calls require opt-in."""
import argparse
import asyncio
import json
import hashlib
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
os.environ['RAGAS_DO_NOT_TRACK']='true'
from app.config import Settings
from app.service import Service,uid
from app.evaluation import evidence_metrics,citation_metrics,ragas_scores


async def run(args):
    s=Settings()
    if not args.allow_cloud:
        s=Settings(_env_file=None,app_mode='offline')
    elif s.app_mode=='offline':
        raise ValueError('allow_cloud_requires_configured_local_or_cloud_mode')
    if args.allow_cloud and (not args.model_call_budget or args.model_call_budget<1):
        raise ValueError('live_evaluation_requires_explicit_model_call_budget')
    if args.allow_cloud and args.judge and (not args.judge_call_budget or args.judge_call_budget<1):
        raise ValueError('explicit_judge_call_budget_required')
    judge_meter={'calls':0,'limit':args.judge_call_budget or 0}
    strategies=args.strategies.split(',')
    if not strategies or any(v not in {'sparse','dense','hybrid','multi','graph'} for v in strategies):
        raise ValueError('invalid_experiment_strategy')
    if args.context_budget is not None:
        if not 500<=args.context_budget<=12000: raise ValueError('context_budget_range_500_12000')
        s=s.model_copy(update={'evidence_budget_bytes':args.context_budget})
    if args.chunk_strategy:
        s=Settings(**{**s.model_dump(),'chunk_strategy':args.chunk_strategy,'chunk_overlap':args.chunk_overlap,
                      'chunk_size':args.chunk_size})
    # Never use business DATA_DIR/collection, including a configured cloud prefix.
    with tempfile.TemporaryDirectory(prefix='knowledge-eval-') as directory:
        import uuid
        s=s.model_copy(update={'data_dir':Path(directory),'collection_prefix':'eval_'+uuid.uuid4().hex[:12],
                               'cache_enabled':False,'graph_enabled':False})
        service=Service(s)
        if args.allow_cloud:service.model_meter={'calls':0,'limit':args.model_call_budget}
        report={'mode':s.app_mode,'contract':s.contract,'human_review':'pending','status':'running',
                'cloud_validated':False,'rows':[],'notes':['Synthetic authored corpus; no quality/gain claims.','All strategies share one frozen index; cache bypassed.']}
        digest=hashlib.sha256()
        for source in sorted(Path('app').rglob('*.py')):
            digest.update(str(source).encode());digest.update(source.read_bytes())
        report['application_source_sha256']=digest.hexdigest()
        try:
            dataset=[json.loads(l) for l in Path(args.dataset).read_text(encoding='utf-8').splitlines() if l.strip()]
            documents={}
            for name in sorted({q['document'] for q in dataset}):
                p=Path('examples')/name
                document=uid('evaluation-source-v1',name)
                service.store.execute('INSERT OR IGNORE INTO documents(id,name) VALUES (?,?)',(document,name))
                uploaded=await service.upload(name,p.suffix,p.read_bytes(),document=document)
                await asyncio.gather(*service.tasks)
                job=service.store.one('SELECT * FROM jobs WHERE id=?',(uploaded['job_id'],))
                if job['status']!='completed': raise RuntimeError('evaluation_ingest_failed:'+str(job['error']))
                documents[name]=uploaded['document_id']
                if args.allow_cloud and args.graph:
                    await service.build_graph(uploaded['document_id'])
            for q in dataset:
                for strategy,rerank in [(st,rr) for st in strategies for rr in ([False,True] if args.rerank_comparison else [args.rerank])]:
                    result=await service.query(q['question'],[documents[q['document']]],route='qa',strategy=strategy,
                                               bypass_cache=True,rerank=rerank)
                    candidate_ids={h['id'] for hits in result.get('retrieval',{}).get('branches',{}).values() for h in hits}
                    candidates=[service.evidence(i) for i in candidate_ids]
                    row={'id':q['id'],'split':q['split'],'category':q['category'],'strategy':strategy,'rerank_requested':rerank,
                         'rerank_applied':bool(result.get('rerank')),'run':result,
                         'reference':q.get('answer'),'evidence_metrics':evidence_metrics(q.get('evidence_groups',[]),candidates,result['evidence']),
                         'citation_metrics':citation_metrics(result),'refusal_correct':result['status']=='refused' if q.get('expected')=='refused' and s.app_mode!='offline' else None}
                    scoring={'question':q['question'],'reference':q.get('answer'),'response':'\n'.join(c['text'] for c in result['claims']),
                             'contexts':[e['text'] for e in result['evidence']]}
                    row['ragas']=await ragas_scores(s,scoring,judge_meter) if args.judge else {n:{'value':None,'status':'not_requested'} for n in ['faithfulness','answer_relevancy','context_recall']}
                    report['rows'].append(row)
            if args.summary:
                report['summary_runs']=[]
                for name,doc in documents.items():
                    summary=await service.query('全文洞察',[doc],route='summary',bypass_cache=True)
                    report['summary_runs'].append({'document':name,'run':summary,'human_quality_review':'pending',
                        'chunk_visit_coverage':summary['coverage']['visited_chunks']/summary['coverage']['total_chunks'] if summary['coverage']['total_chunks'] else None})
            report['summary']={'questions':len(dataset),'runs':len(report['rows']),
                'statuses':{status:sum(r['run']['status']==status for r in report['rows']) for status in {r['run']['status'] for r in report['rows']}},
                'graph_gain':None,'ragas_quality_conclusion':None}
            report['experiment_model_meter']=service.model_meter
            report['judge_model_meter']=judge_meter
            report['status']='completed_with_pending_human_and_cloud_acceptance'
        except Exception as exc:
            report['status']='failed';report['error']=type(exc).__name__
            raise
        finally:
            if s.app_mode=='cloud':
                try: service.index.client.delete_collection(service.index.name)
                except Exception as exc: report['cloud_cleanup_error']=type(exc).__name__
            if args.allow_cloud and args.graph:
                try: service.graph.delete([d['active'] for d in service.store.all('SELECT active FROM documents')])
                except Exception as exc: report['graph_cleanup_error']=type(exc).__name__
            await service.close()
            target=Path(args.report);target.parent.mkdir(parents=True,exist_ok=True)
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report.get('summary',{}),ensure_ascii=False))
        if report.get('cloud_cleanup_error') or report.get('graph_cleanup_error'): raise RuntimeError('evaluation_cleanup_failed')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--dataset',default='eval/golden.jsonl')
    parser.add_argument('--report',default='artifacts/evaluation-offline.json')
    parser.add_argument('--strategies',default='dense,hybrid,multi,graph')
    parser.add_argument('--allow-cloud',action='store_true')
    parser.add_argument('--model-call-budget',type=int,help='Total application model attempts, including ingestion/retries; judge has a separate per-sample budget')
    parser.add_argument('--judge-call-budget',type=int,help='Total separate RAGAS judge/embedding attempts')
    parser.add_argument('--graph',action='store_true')
    parser.add_argument('--judge',action='store_true')
    parser.add_argument('--rerank',action='store_true')
    parser.add_argument('--rerank-comparison',action='store_true',help='Paired runs on one frozen index; report whether rerank actually ran')
    parser.add_argument('--chunk-strategy',choices=['legacy','boundary'])
    parser.add_argument('--chunk-size',type=int,default=900)
    parser.add_argument('--chunk-overlap',type=int,default=0)
    parser.add_argument('--context-budget',type=int)
    parser.add_argument('--summary',action='store_true',help='Evaluate full-document coverage separately from QA')
    asyncio.run(run(parser.parse_args()))
