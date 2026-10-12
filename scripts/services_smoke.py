"""Explicit account contract checks; isolation and cleanup, no model calls."""
import argparse
import asyncio
import json
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.storage import Store
from app.graph import Graph,validate_facts
from app.cache import AnswerCache


async def main(args):
    if not args.allow_cloud: raise ValueError('requires_explicit_allow_cloud')
    # This standalone --allow-cloud script only tests explicitly configured
    # services, and does not require generation/embedding credentials.
    s=Settings(app_mode='offline').model_copy(update={'app_mode':'local'})
    namespace='smoke_'+uuid.uuid4().hex[:12]
    s=s.model_copy(update={'collection_prefix':namespace})
    results={};cleanup=[]
    with tempfile.TemporaryDirectory(prefix='services-smoke-') as directory:
        st=Store(Path(directory));graph=Graph(s,st)
        try:
            if s.neo4j_uri:
                c={'id':'smoke-chunk','generation':namespace,'text':'样例公司生产样例产品'}
                f=validate_facts([{'subject':{'name':'样例公司','kind':'company'},'object':{'name':'样例产品','kind':'product'},
                    'relation':'produces','quote':c['text'],'assertion':'historical'}],c,origin='explicit_smoke_fixture')
                try:
                    graph.replace(namespace,f);hits,_=graph.search('样例公司',[namespace]);assert len(hits)==1
                    assert graph.search('样例公司',['other-generation'])==([],[])
                    results['neo4j']='write_read_scope_passed'
                except Exception as exc: results['neo4j']='failed:'+type(exc).__name__
            else: results['neo4j']='not_configured'
            if s.redis_url:
                cache=AnswerCache(s)
                try:
                    await cache.put(namespace,'scope test',[1,0],{'run_id':'smoke'})
                    assert (await cache.lookup(namespace,'scope test'))[0]
                    assert (await cache.lookup(namespace+'other','scope test'))[0] is None
                    results['redis']='set_get_scope_passed'
                except Exception as exc: results['redis']='failed:'+type(exc).__name__
                finally:
                    try: await cache.command(['DEL','ka:'+namespace])
                    except Exception as exc: cleanup.append('redis:'+type(exc).__name__)
            else: results['redis']='not_configured'
            if s.langfuse_public_key:
                from langfuse import Langfuse
                client=Langfuse(public_key=s.langfuse_public_key,secret_key=s.langfuse_secret_key.get_secret_value(),
                    base_url=s.langfuse_base_url,timeout=5)
                try:
                    assert client.auth_check()
                    with client.start_as_current_observation(name=namespace,metadata={'synthetic_service_contract_test':True}): pass
                    client.flush();results['langfuse']='auth_and_export_attempted_ui_visibility_pending'
                except Exception as exc: results['langfuse']='failed:'+type(exc).__name__
                finally: client.shutdown()
            else: results['langfuse']='not_configured'
        finally:
            if s.neo4j_uri:
                try: graph.delete([namespace])
                except Exception as exc: cleanup.append('neo4j:'+type(exc).__name__)
            graph.close();st.db.close()
    report={'namespace':namespace,'results':results,'cleanup_errors':cleanup,'model_calls':0,
            'notes':'Synthetic fixture only proves storage contracts, not extraction/model quality. Langfuse visibility needs UI verification.'}
    Path(args.report).parent.mkdir(parents=True,exist_ok=True);Path(args.report).write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))
    if cleanup or any(v.startswith('failed:') for v in results.values()): raise RuntimeError('service_smoke_failed')


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--allow-cloud',action='store_true');p.add_argument('--report',default='artifacts/services-smoke.json')
    asyncio.run(main(p.parse_args()))
