import asyncio
import json

from app.config import Settings
from app.models import CloudModels
from app.service import Service


def test_model_contract_graph_summary_cache_and_delete(tmp_path,monkeypatch):
    s=Settings(_env_file=None,app_mode='local',model_api_key='test-only',embedding_dimension=2)
    s=s.model_copy(update={'data_dir':tmp_path})
    async def embed(self,texts): return [[1.,.2] for _ in texts]
    async def answer(self,question,evidence):
        return {'status':'answered','claims':[{'text':'测试断言','citations':[evidence[0]['id']]}]}
    async def structured(self,instruction,data,model=None):
        if 'facts 数组' in instruction:
            text=data['text']
            if '星河生产芯片' not in text: return {'facts':[]}
            return {'facts':[{'subject':{'name':'星河','kind':'company'},'object':{'name':'芯片','kind':'product'},
                'relation':'produces','quote':'星河生产芯片','assertion':'historical'}]}
        if 'query 字符串' in instruction: return {'query':'芯片'}
        if 'core_viewpoints/risk_points/key_data' in instruction:
            return {'core_viewpoints':[{'text':'测试聚合','sources':['0']}],'risk_points':[],'key_data':[]}
        return {'ids':[c['id'] for c in reversed(data['candidates'])]}
    monkeypatch.setattr(CloudModels,'embed',embed);monkeypatch.setattr(CloudModels,'answer',answer)
    monkeypatch.setattr(CloudModels,'structured',structured)
    async def run():
        service=Service(s)
        try:
            upload=await service.upload('a.md','.md','# 产品\n\n星河生产芯片。\n\n## 财务\n\n星河收入120亿元。'.encode())
            await asyncio.gather(*service.tasks)
            doc=upload['document_id']
            build=await service.build_graph(doc)
            assert build['status']=='ready' and build['facts']==1
            result=await service.query('星河关系',[doc],route='relation',strategy='graph')
            assert result['status']=='answered' and result['graph_paths']
            assert all(f['generation'] in result['snapshot']['generations'] for p in result['graph_paths'] for f in p)
            original_search=service.graph.search
            hits,paths=original_search('星河',result['snapshot']['generations'])
            paths=json.loads(json.dumps(paths));paths[0][0]['generation']='foreign-generation'
            service.graph.search=lambda *args:(hits,paths)
            guarded=await service.query('星河污染关系',[doc],route='relation',strategy='graph',bypass_cache=True)
            assert guarded['status']=='answered' and 'graph_paths' not in guarded
            assert 'graph' not in guarded['retrieval']['branches']
            assert any(w.startswith('graph_failed_hybrid_fallback') for w in guarded['warnings'])
            service.graph.search=original_search
            first=await service.query('星河收入',[doc],route='qa')
            second=await service.query('星河收入',[doc],route='qa')
            assert second['cache']['status']=='exact' and second['run_id']!=first['run_id']
            assert second['usage']==[] and second['cost']['status']=='no_cloud_calls'
            bypass=await service.query('星河收入',[doc],route='qa',bypass_cache=True)
            assert bypass['cache']['status']=='bypass'
            multi=await service.query('星河产品',[doc],route='qa',strategy='multi')
            assert multi['supplemental_queries']==['芯片']
            summary=await service.query('全文洞察',[doc],route='summary')
            assert summary['status']=='summarized' and summary['coverage']['complete_sections']==2
            assert summary['insights']['status']=='ready'
            assert all(i in {e['id'] for e in summary['evidence']} for c in summary['insights']['core_viewpoints'] for i in c['citations'])
            async def invalid_aggregate(self,instruction,data,model=None):
                return {'core_viewpoints':[{'text':'无效引用','sources':['not-in-input']}],'risk_points':[],'key_data':[]}
            monkeypatch.setattr(CloudModels,'structured',invalid_aggregate)
            partial=await service.query('全文洞察',[doc],route='summary')
            assert partial['status']=='partial_summary'
            assert partial['insights']['status']=='failed_section_union_retained'
            updated=await service.upload('a.md','.md','# 财务\n\n星河收入150亿元。'.encode(),doc)
            await asyncio.gather(*service.tasks)
            new=await service.query('星河收入',[doc],route='qa')
            assert new['cache']['status']=='miss' and new['snapshot']['generations']!=first['snapshot']['generations']
            await service.delete(doc)
            assert not service.store.all('SELECT * FROM facts')
            assert not service.store.all('SELECT * FROM summaries')
        finally: await service.close()
    asyncio.run(run())


def test_request_deadline_cancels_model_and_releases_lock(tmp_path,monkeypatch):
    import time
    import pytest
    from fastapi import HTTPException
    cancelled=[]
    async def embed(self,texts): return [[1.,.2] for _ in texts]
    async def slow(self,question,evidence):
        try: await asyncio.sleep(5)
        except asyncio.CancelledError: cancelled.append(True);raise
    monkeypatch.setattr(CloudModels,'embed',embed);monkeypatch.setattr(CloudModels,'answer',slow)
    async def run():
        service=Service(Settings(_env_file=None,data_dir=tmp_path,app_mode='local',model_api_key='fixture',embedding_dimension=2,request_timeout=1))
        try:
            upload=await service.upload('a.txt','.txt','星河收入120亿元'.encode());await asyncio.gather(*service.tasks)
            started=time.monotonic();result=await service.query('星河收入',[upload['document_id']])
            assert result['status']=='request_timeout' and cancelled
            assert time.monotonic()-started<3 and not service.lock.locked()
            await service.lock.acquire()
            try:
                with pytest.raises(HTTPException) as exc: await service.query('星河收入',[upload['document_id']])
                assert exc.value.status_code==504
            finally: service.lock.release()
        finally: await service.close()
    asyncio.run(run())
