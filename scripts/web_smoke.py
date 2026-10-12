"""Separate live search/read availability probe; never invokes embeddings or generation."""
import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.web import TavilySearch, PageReader, WebError


async def run(args):
    if not args.allow_live or args.search_call_budget!=1:
        raise ValueError('explicit_allow_live_and_one_search_call_budget_required')
    settings=Settings()
    if settings.app_mode=='offline': raise ValueError('offline_never_calls_network')
    result={'status':'running','live_api_check':True,'model_calls':0,'search_attempts':1,'pages':[],'semantic_support':'not_judged'}
    try:
        found=await TavilySearch(settings).search(args.question)
        result['search']=found
        reader=PageReader(settings)
        for page in found['results'][:args.pages]:
            try:
                snapshot=await reader.read(page)
                result['pages'].append(snapshot)
            except Exception as exc:
                result['pages'].append({'url':page['url'],'status':'failed','error':str(exc) if isinstance(exc,WebError) else type(exc).__name__})
        result['status']='completed' if any(p.get('read_status')=='body_read' for p in result['pages']) else 'no_readable_body'
    except Exception as exc:
        result.update(status='failed',error=str(exc) if isinstance(exc,WebError) else type(exc).__name__)
    target=Path(args.report);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':result['status'],'model_calls':0,'search_attempts':1}))
    if result['status']!='completed':raise RuntimeError('live_web_smoke_failed_see_report')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--allow-live',action='store_true')
    parser.add_argument('--search-call-budget',type=int)
    parser.add_argument('--question',default='HTTP semantics RFC 9110')
    parser.add_argument('--pages',type=int,choices=[1,2,3],default=1)
    parser.add_argument('--report',default='artifacts/web-smoke.live.json')
    asyncio.run(run(parser.parse_args()))
