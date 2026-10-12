"""Synthetic contracts and failure fixtures. No quality claims, no external IO."""
import asyncio
import json
import socket
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.config import Settings
from app.models import CloudModels
from app.reliability import BudgetExhausted, CallBudget
from app.parsing import chunk
from app.service import Service
from app.checkpoints import Checkpoints
from app.web import (WebError, canonical_url, public_addresses, PinnedConnection,
                     read_http, PageReader, TavilySearch, html_snapshot)
from app.main import create_app
from app.cache import namespace


def local(tmp_path, **kw):
    return Settings(_env_file=None,data_dir=tmp_path,app_mode='local',model_api_key='synthetic-only',
                    embedding_dimension=2,**kw)


def fake_models(monkeypatch):
    async def embed(self,texts): return [[1.,.2] for _ in texts]
    async def answer(self,question,evidence):
        return {'status':'answered','claims':[{'text':'synthetic response','citations':[e['id'] for e in evidence]}]}
    monkeypatch.setattr(CloudModels,'embed',embed)
    monkeypatch.setattr(CloudModels,'answer',answer)


async def ingest(service, text='唯一文档证据收入120亿元。'):
    record=await service.upload('fixture.txt','.txt',text.encode())
    await asyncio.gather(*service.tasks)
    assert service.store.one('SELECT status FROM jobs WHERE id=?',(record['job_id'],))['status']=='completed'
    return record


class SearchFixture:
    def __init__(self, fail=False): self.calls=[];self.fail=fail
    async def search(self, query):
        self.calls.append(query)
        if self.fail: raise WebError('http_429')
        return {'results':[{'url':'https://public.example/evidence','title':'fixture page',
                            'snippet':'UNVERIFIED_SNIPPET_999','published_at':None}],
                'provider':'explicit_fixture','usage':{'credits':None}}


class ReaderFixture:
    def __init__(self, fail=False): self.calls=[];self.fail=fail;self.body='核对网页证据收入100亿元。'
    async def read(self, row):
        self.calls.append(row['url'])
        if self.fail: raise WebError('body_unavailable')
        return html_snapshot(('<main><h1>原文</h1><p>'+self.body+'</p></main>').encode(),row['url'],row['url'],row,30000)


def test_embedding_1280_1281_and_background_budget(monkeypatch):
    import litellm
    calls=[]
    async def response(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=[{'index':i,'embedding':[1.,.2]} for i in range(len(kwargs['input']))],usage=None)
    monkeypatch.setattr(litellm,'aembedding',response)
    settings=Settings(_env_file=None,embedding_dimension=2,retry_attempts=0)
    assert len(asyncio.run(CloudModels(settings).embed(['text']*1280)))==1280
    with pytest.raises(BudgetExhausted): asyncio.run(CloudModels(settings).embed(['text']*1281))
    model=CloudModels(settings,purpose='background')
    assert len(asyncio.run(model.embed(['text']*1281)))==1281
    assert model.budget.calls==41 and len(model.usage)==41


def test_embedding_resume_reuses_only_valid_successful_batches(tmp_path,monkeypatch):
    import litellm
    service=Service(local(tmp_path,retry_attempts=0,background_max_model_calls=1))
    calls=[]
    async def response(**kwargs):
        calls.append(kwargs['input'])
        return SimpleNamespace(data=[{'index':i,'embedding':[1.,.2]} for i in range(len(kwargs['input']))],usage=None)
    monkeypatch.setattr(litellm,'aembedding',response)
    async def run():
        checkpoint=Checkpoints(service.store,'gen','embedding')
        first=CloudModels(service.s,purpose='background');first.embedding_cache=checkpoint
        with pytest.raises(BudgetExhausted): await first.embed([str(i) for i in range(33)])
        second=CloudModels(service.s,purpose='background');second.embedding_cache=checkpoint
        assert len(await second.embed([str(i) for i in range(33)]))==33
        assert len(calls)==2 and len(calls[-1])==1
        bad=CloudModels(service.s,purpose='background');bad.embedding_cache=checkpoint
        rows=service.store.all('SELECT * FROM processing_checkpoints')
        checkpoint.put(rows[0]['key'],[[float('nan'),1]]*32)
        with pytest.raises(ValueError): await bad.embed([str(i) for i in range(33)])
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('profile,document,query',[('cohere','search_document','search_query'),('voyage','document','query')])
def test_provider_input_role_contract(profile,document,query,monkeypatch):
    import litellm
    calls=[]
    async def response(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=[{'index':0,'embedding':[1.,.2]}],usage=None)
    monkeypatch.setattr(litellm,'aembedding',response)
    model=CloudModels(Settings(_env_file=None,embedding_model=profile+'/fixture',embedding_dimension=2))
    asyncio.run(model.embed(['document']));asyncio.run(model.embed_query(['question']))
    assert [c['input_type'] for c in calls]==[document,query]
    assert all(c['model']==profile+'/fixture' for c in calls)


def test_embedding_response_model_mismatch(monkeypatch):
    import litellm
    async def response(**kwargs): return SimpleNamespace(model='wrong-model',data=[],usage=None)
    monkeypatch.setattr(litellm,'aembedding',response)
    with pytest.raises(ValueError,match='model_mismatch'):
        asyncio.run(CloudModels(Settings(_env_file=None)).embed(['text']))


@pytest.mark.parametrize('change',[{'chunk_size':800},{'chunk_strategy':'boundary'},
    {'embedding_revision':'v2'},{'embedding_input_profile':'prefix','embedding_query_prefix':'query: '}])
def test_index_contract_requires_explicit_rebuild(change):
    settings=Settings(_env_file=None)
    assert settings.fingerprint!=settings.model_copy(update=change).fingerprint
    assert settings.fingerprint==settings.model_copy(update={'retrieval_top_k':3,'evidence_budget_bytes':500}).fingerprint


def test_boundary_chunking_offsets_overlap_and_legacy():
    text=('每段包含完整句子，测试段落边界。第二句话包含更多内容。\n\n')*40
    parsed={'blocks':[{'text':text,'location':{'line_start':1},'headings':[]}]}
    old=chunk(parsed,size=100)
    assert [c['text'] for c in old]==[text[i:i+100] for i in range(0,len(text),100)]
    new=chunk(parsed,size=100,strategy='boundary',overlap=30)
    covered=set()
    for c in new:
        span=c['source_span'];assert c['text']==text[span['start']:span['end']] and len(c['text'])<=100
        covered.update(range(span['start'],span['end']))
    assert covered==set(range(len(text)))
    assert any(b['offset']<a['source_span']['end'] for a,b in zip(new,new[1:]))


def test_full_request_budget_is_separate_from_evidence(monkeypatch):
    import litellm
    async def forbidden(**kwargs): raise AssertionError('rejected request must not call model')
    monkeypatch.setattr(litellm,'acompletion',forbidden)
    model=CloudModels(Settings(_env_file=None,model_request_budget_bytes=4000))
    with pytest.raises(ValueError,match='model_request_bytes'):
        asyncio.run(model.answer('question',[{'id':'e','text':'tiny','headings':[]}]))
    assert model.budget.calls==0 and model.request_audit[0]['status']=='rejected'


def test_parse_does_not_spend_processing_model_deadline(tmp_path,monkeypatch):
    fake_models(monkeypatch)
    service=Service(local(tmp_path,request_timeout=1,background_timeout=5))
    seen=[]
    async def parse(rec):
        await asyncio.sleep(1.05)
        return {'chunks':[{'text':'fixture','headings':[],'locations':[]}]},'fixture-path'
    async def embed(self,texts):
        seen.append((self.budget.purpose,self.budget.deadline-time.monotonic()))
        return [[1.,.2]]
    monkeypatch.setattr(service,'parse',parse);monkeypatch.setattr(CloudModels,'embed',embed)
    async def run():
        await ingest(service)
        assert seen[0][0]=='background' and seen[0][1]>4
        await service.close()
    asyncio.run(run())


def test_sparse_dense_hybrid_fair_pool_and_rerank(tmp_path,monkeypatch):
    fake_models(monkeypatch)
    service=Service(local(tmp_path,retrieval_branch_limit=20,retrieval_candidate_pool=16,retrieval_top_k=3,
                          rerank_model='fixture',evidence_budget_bytes=20000))
    ranked=[]
    async def structured(self,instruction,data,model=None):
        ranked.append([c['id'] for c in data['candidates']])
        return {'ids':list(reversed(ranked[-1]))}
    monkeypatch.setattr(CloudModels,'structured',structured)
    async def run():
        doc=await ingest(service,'\n\n'.join('唯一文档证据编号'+str(i)+'收入120亿元。' for i in range(15)))
        results=[]
        for strategy in ['sparse','dense','hybrid']:
            r=await service.query('唯一文档证据',[doc['document_id']],route='qa',strategy=strategy,bypass_cache=True,rerank=True)
            assert set(r['retrieval']['branches'])==({'sparse','dense'} if strategy=='hybrid' else {strategy})
            assert len(ranked[-1])>8
            assert [e['id'] for e in r['evidence']]==list(reversed(ranked[-1]))[:3]
            assert any(d['selection']=='final_top_k' for d in r['context']['decisions'])
            results.append(r)
        assert len({r['snapshot']['collection'] for r in results})==1
        assert len({namespace(r['snapshot'],r['policy']) for r in results})==3
        await service.close()
    asyncio.run(run())


@pytest.mark.parametrize('url',['file:///etc/passwd','http://localhost/','http://a.local/',
    'http://user:secret@example.com/','http://example.com:8000/','http://example.com\\@127.0.0.1/',
    'http://example.com/\nheader','http://[fe80::1%25eth0]/'])
def test_url_syntax_denied(url):
    with pytest.raises(WebError):canonical_url(url)


@pytest.mark.parametrize('address',['127.0.0.1','10.0.0.1','172.16.0.1','192.168.1.1','169.254.169.254',
                                  '100.100.100.200','0.0.0.0','::1','fc00::1','fe80::1','224.0.0.1','::ffff:127.0.0.1'])
def test_dns_private_addresses_denied(address):
    def resolver(*a,**k):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',(address,443)),
                                (socket.AF_INET,socket.SOCK_STREAM,6,'',('93.184.216.34',443))]
    with pytest.raises(WebError,match='non_public'):public_addresses('https://public.example',resolver)


def test_pinned_socket_does_not_reresolve_and_preserves_tls_hostname(monkeypatch):
    connections=[];tls=[]
    class Sock:
        def settimeout(self,v): pass
        def connect(self,address):connections.append(address)
        def close(self):pass
        def do_handshake(self):pass
    class Context:
        def wrap_socket(self,sock,server_hostname,**kwargs):tls.append(server_hostname);return sock
    monkeypatch.setattr('app.web.socket.socket',lambda *a,**k:Sock())
    monkeypatch.setattr('app.web.ssl.create_default_context',lambda:Context())
    connection=PinnedConnection('public.example',443,'93.184.216.34',True,1)
    connection.connect()
    assert connections==[('93.184.216.34',443)] and tls==['public.example']


def test_redirect_revalidates_addresses(tmp_path,monkeypatch):
    visited=[]
    async def request(url,deadline,max_bytes,**kwargs):
        visited.append(url)
        if len(visited)==1:return 302,{'location':'http://169.254.169.254/latest/meta-data'},b''
        public_addresses(url,lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('169.254.169.254',80))])
    monkeypatch.setattr('app.web.pinned_request',request)
    with pytest.raises(WebError):asyncio.run(PageReader(local(tmp_path)).read({'url':'http://public.example/'}))
    assert len(visited)==2


def test_reader_size_compression_and_static_type(tmp_path,monkeypatch):
    class Response:
        status=200
        def getheaders(self):return [('content-length','1000001')]
    class Connection:
        def __init__(self,*a,**k):pass
        def request(self,*a,**k):pass
        def getresponse(self):return Response()
        def close(self):pass
    monkeypatch.setattr('app.web.PinnedConnection',Connection)
    with pytest.raises(WebError,match='size_limit'):read_http('https://public.example/','93.184.216.34',time.monotonic()+1,1000000)
    async def request(*a,**k):return 200,{'content-type':'application/pdf'},b'pdf'
    monkeypatch.setattr('app.web.pinned_request',request)
    with pytest.raises(WebError,match='static_html_only'):
        asyncio.run(PageReader(local(tmp_path)).read({'url':'https://public.example/'}))


def test_wall_deadline_interrupts_header_trickle(monkeypatch):
    import threading
    release=threading.Event()
    class Sock:
        def shutdown(self,*args):release.set()
        def close(self):pass
    class Connection:
        def __init__(self,*a,**k):self.wire_socket=Sock()
        def request(self,*a,**k):pass
        def getresponse(self):
            assert release.wait(2), 'hard deadline failed to interrupt headers'
            raise TimeoutError('synthetic_header_interrupt')
        def close(self):pass
    monkeypatch.setattr('app.web.PinnedConnection',Connection)
    started=time.monotonic()
    with pytest.raises(TimeoutError):read_http('https://public.example/','93.184.216.34',started+.05,10000)
    assert time.monotonic()-started<1


def test_official_search_payload_limit_and_rate_failure(tmp_path,monkeypatch):
    seen=[]
    async def request(url,deadline,max_bytes,**kwargs):
        seen.append(json.loads(kwargs['body']))
        return 200,{},json.dumps({'results':[{'url':'https://public.example/'+str(i),'content':'snippet'} for i in range(20)]}).encode()
    monkeypatch.setattr('app.web.pinned_request',request)
    search=TavilySearch(local(tmp_path,search_provider='tavily',search_api_key='fixture',search_max_results=3))
    assert len(asyncio.run(search.search('test'))['results'])==3
    assert seen[0]['max_results']==3 and not seen[0]['include_answer'] and not seen[0]['auto_parameters']
    async def limited(*a,**k):raise WebError('http_429')
    monkeypatch.setattr('app.web.pinned_request',limited)
    with pytest.raises(WebError,match='429'):asyncio.run(search.search('test'))


@pytest.mark.parametrize('scope,search_fail,read_fail',[('web',False,False),('combined',True,False),('combined',False,True)])
def test_fixed_online_sources_failures_and_no_snippet_evidence(tmp_path,monkeypatch,scope,search_fail,read_fail):
    fake_models(monkeypatch)
    service=Service(local(tmp_path,evidence_budget_bytes=12000))
    service.search=SearchFixture(search_fail);service.reader=ReaderFixture(read_fail)
    async def run():
        doc=await ingest(service)
        r=await service.query('证据收入',[] if scope=='web' else [doc['document_id']],scope=scope,route='qa')
        assert r['status']=='answered'
        assert all('UNVERIFIED_SNIPPET' not in e['text'] for e in r['evidence'])
        assert all(e.get('source_type')=='web' for e in r['evidence']) if scope=='web' else any(e.get('source_type')=='document' for e in r['evidence'])
        assert r['cache']['status']=='bypass'
        assert r['tool_trace'] and all('elapsed_ms' in t for t in r['tool_trace'])
        if read_fail:assert r['online']['pages'][0]['read_status']=='body_unavailable'
        await service.close()
    asyncio.run(run())


def test_web_snapshots_survive_page_change_restart_and_manual_import(tmp_path,monkeypatch):
    fake_models(monkeypatch)
    service=Service(local(tmp_path));service.search=SearchFixture();service.reader=ReaderFixture()
    async def run():
        first=await service.query('收入',[],scope='web',route='qa')
        first_id=first['evidence'][0]['id'];original=service.evidence(first_id)
        service.reader.body='网页更新为999亿元。'
        second=await service.query('收入',[],scope='web',route='qa')
        assert second['evidence'][0]['id']!=first_id
        assert service.evidence(first_id)==original
        assert not service.store.all('SELECT * FROM documents')
        imported=await service.save_web(first_id)
        await asyncio.gather(*service.tasks)
        assert imported['document_id']
        await service.close()
        reopened=Service(local(tmp_path))
        assert reopened.evidence(first_id)==original
        assert reopened.run(first['run_id'])['evidence'][0]['text']==first['evidence'][0]['text']
        await reopened.close()
    asyncio.run(run())


@pytest.mark.parametrize('actions,reason,search_count',[
    ([('search_web',{'query':'收入'}),('read_page',{'url':'https://public.example/evidence'}),('answer',{})],'model_answer',1),
    ([('search_web',{'query':'first'}),('search_web',{'query':'second'}),('search_web',{'query':'third'})],'controller_rejection',2),
    ([('search_web',{'query':'same'}),('search_web',{'query':' SAME ' }),('stop',{})],'model_stop',1),
    ([('retrieve_local',{'query':'secret'})],'controller_rejection',0),
    ([('read_page',{'url':'http://127.0.0.1/'})],'decision_limit',0),
    ([('stop',{})],'model_stop',0)])
def test_agent_selection_scope_duplicates_and_stop(tmp_path,monkeypatch,actions,reason,search_count):
    fake_models(monkeypatch)
    service=Service(local(tmp_path));service.search=SearchFixture();service.reader=ReaderFixture()
    counter=0
    async def structured(self,instruction,data,model=None):
        nonlocal counter
        name,args=actions[min(counter,len(actions)-1)];counter+=1
        return {'action':name,'args':args}
    monkeypatch.setattr(CloudModels,'structured',structured)
    async def run():
        r=await service.query('收入',[],scope='web',workflow='agent',route='qa')
        assert r['agent']['stop_reason']==reason
        assert len(service.search.calls)==search_count
        assert r['agent']['supplemental_rounds']<=2
        assert len(r['agent']['decisions'])<=9
        assert all('elapsed_ms' in d for d in r['agent']['decisions'])
        if reason=='model_answer':assert r['status']=='answered' and len(service.reader.calls)==1
        await service.close()
    asyncio.run(run())


def test_api_scope_and_offline_guard(tmp_path):
    with TestClient(create_app(Settings(_env_file=None,data_dir=tmp_path,search_provider='tavily',search_api_key='fixture'))) as client:
        assert client.post('/api/query',json={'question':'test'}).status_code==422
        r=client.post('/api/query',json={'question':'test','scope':'web'}).json()
        assert r['status']=='refused' and any('network_disabled_offline' in w for w in r['warnings'])
        assert client.post('/api/query',json={'question':'test','scope':'web','workflow':'agent'}).status_code==409


def test_ingest_failed_budget_preserves_active_and_resumes_batches(tmp_path,monkeypatch):
    import litellm
    calls=[]
    async def embedding(**kwargs):
        calls.append(len(kwargs['input']))
        return SimpleNamespace(data=[{'index':i,'embedding':[1.,.2]} for i in range(len(kwargs['input']))],usage=None)
    monkeypatch.setattr(litellm,'aembedding',embedding)
    service=Service(local(tmp_path,background_max_model_calls=1,retry_attempts=0))
    async def run():
        original=await ingest(service)
        update=await service.upload('fixture.txt','.txt','\n\n'.join('第'+str(i)+'段材料。' for i in range(33)).encode(),document=original['document_id'])
        await asyncio.gather(*service.tasks)
        assert service.store.one('SELECT status FROM jobs WHERE id=?',(update['job_id'],))['status']=='failed'
        assert service.store.one('SELECT active FROM documents')['active']==original['generation_id']
        await service.retry(update['job_id']);await asyncio.gather(*service.tasks)
        assert service.store.one('SELECT status FROM jobs WHERE id=?',(update['job_id'],))['status']=='completed'
        assert calls==[1,32,1]
        assert service.store.one('SELECT active FROM documents')['active']==update['generation_id']
        assert service.evidence(service.store.all('SELECT id FROM chunks WHERE generation=?',(original['generation_id'],))[0]['id'])
        await service.close()
    asyncio.run(run())


def test_graph_partial_budget_resume_and_published_vector_survives(tmp_path,monkeypatch):
    import litellm
    fake_models(monkeypatch)
    calls=[]
    async def completion(**kwargs):
        data=json.loads(kwargs['messages'][1]['content']);calls.append(data['text'])
        value={'facts':[{'subject':{'name':'星河','kind':'company'},'object':{'name':'芯片','kind':'product'},
                        'relation':'produces','quote':data['text'],'assertion':'historical'}]}
        return SimpleNamespace(model='fixture',usage=None,choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(value)))])
    monkeypatch.setattr(litellm,'acompletion',completion)
    service=Service(local(tmp_path,background_max_model_calls=1,graph_enabled=True))
    async def run():
        record=await ingest(service,'星河生产芯片。\n\n星河生产芯片用于传感器。')
        generation=record['generation_id']
        assert service.store.one('SELECT graph_status FROM generations WHERE id=?',(generation,))['graph_status']=='partial'
        assert service.store.one('SELECT vector_status FROM generations WHERE id=?',(generation,))['vector_status']=='ready'
        result=await service.build_graph(record['document_id'])
        assert result['status']=='ready' and len(calls)==2
        await service.close()
    asyncio.run(run())


def test_replay_integrity_reuse_and_unknown_query_fail_closed(tmp_path,monkeypatch):
    from pathlib import Path
    from app.web_replay import SnapshotReplay
    case=json.loads(Path('eval/web-snapshots.fixture.json').read_text(encoding='utf-8'))['cases'][0]
    replay=SnapshotReplay(case)
    fake_models(monkeypatch)
    service=Service(local(tmp_path));service.search=replay;service.reader=replay
    async def run():
        for _ in range(2):
            r=await service.query(case['question'],[],scope='web',route='qa')
            assert r['status']=='answered' and r['evidence']
        assert len(service.store.all('SELECT * FROM web_evidence'))==1
        with pytest.raises(WebError,match='not_recorded'):await replay.search('unrecorded')
        changed=json.loads(json.dumps(case));changed['pages'][0]['text']='tampered'
        with pytest.raises(ValueError,match='integrity'):SnapshotReplay(changed)
        await service.close()
    asyncio.run(run())
