import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.graph import Graph, validate_facts
from app.main import create_app
from app.storage import Store


def fact(a='星河', b='Aurora-X', relation='produces'):
    return {'subject':{'name':a,'kind':'company','aliases':[]}, 'object':{'name':b,'kind':'product','aliases':[]},
            'relation':relation,'quote':a+b,'assertion':'historical'}


def test_graph_scope_quote_and_bounds(tmp_path):
    st=Store(tmp_path); graph=Graph(Settings(_env_file=None,data_dir=tmp_path),st)
    c={'text':'星河Aurora-X','id':'c1','generation':'g1'}
    graph.replace('g1',validate_facts([fact()],c))
    graph.replace('g2',validate_facts([fact('Aurora-X','秘密')],{'text':'Aurora-X秘密','id':'c2','generation':'g2'}))
    hits,paths=graph.search('星河关系',['g1'])
    assert {h['id'] for h in hits}=={'c1'}
    assert all(f['generation']=='g1' for p in paths for f in p)
    with pytest.raises(ValueError): validate_facts([fact()],{**c,'text':'无证据'})
    with pytest.raises(ValueError): validate_facts([fact(relation='has_metric')],c)
    graph.delete(['g1']); assert graph.search('星河',['g1'])==([],[])
    graph.close();st.db.close()


def test_summary_visits_all_sections_and_relation_fallback(tmp_path):
    with TestClient(create_app(Settings(_env_file=None,data_dir=tmp_path))) as client:
        r=client.post('/api/documents',files={'file':('a.md','# 总览\n\n简介\n\n## 财务\n\n收入120亿元\n\n## 风险\n\n供应中断风险'.encode())}).json()
        import time
        for _ in range(100):
            job=client.get('/api/jobs/'+r['job_id']).json()
            if job['status'] in ('completed','failed'): break
            time.sleep(.05)
        assert job['status']=='completed'
        q={'document_ids':[r['document_id']],'question':'全文洞察','route':'summary'}
        summary=client.post('/api/query',json=q).json()
        assert summary['status']=='offline_extracts'
        assert summary['coverage']['total_sections']==3
        assert summary['coverage']['visited_chunks']==summary['coverage']['total_chunks']
        assert summary['claims']==[]
        relation=client.post('/api/query',json={**q,'route':'relation','question':'供应关系'}).json()
        assert 'graph_not_complete_hybrid_fallback' in relation['warnings']
        assert client.post('/api/documents/'+r['document_id']+'/graph').status_code==409


def test_empty_optional_configuration():
    s=Settings(_env_file=None,llm_api_base='',embedding_api_base=' ',neo4j_uri='',redis_url='',fallback_model='')
    assert s.llm_api_base is None and s.embedding_api_base is None
    assert s.neo4j_uri is None and s.redis_url is None and s.fallback_model is None


def test_neo4j_managed_transaction_contract(tmp_path,monkeypatch):
    st=Store(tmp_path)
    s=Settings(_env_file=None,app_mode='local',model_api_key='fixture',neo4j_uri='neo4j+s://fixture.invalid',neo4j_password='fixture')
    graph=Graph(s,st);calls=[]
    class Result:
        def consume(self): pass
    class Transaction:
        def run(self,query,**params):
            assert isinstance(query,str), 'Neo4j managed transactions reject Query objects'
            calls.append((query,params));return Result()
    class Session:
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute_write(self,writer):
            assert writer.timeout==10
            writer(Transaction())
    class Driver:
        def session(self,**kwargs): return Session()
        def close(self): pass
    monkeypatch.setattr(graph,'remote',lambda:Driver())
    graph.replace('g1',validate_facts([fact()],{'text':'星河Aurora-X','id':'c1','generation':'g1'}))
    assert len(calls)==2
    assert calls[0][1]['g']=='g1' and '$g' in calls[0][0]
    assert calls[1][1]['rows'][0]['generation']=='g1'
    assert 'SUBJECT' in calls[1][0] and 'OBJECT' in calls[1][0]
    graph.close();st.db.close()
