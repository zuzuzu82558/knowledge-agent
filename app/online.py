"""Fixed online baseline and a model-selected, validated tool controller."""
import asyncio
import json
import time
from types import SimpleNamespace

from .web import canonical_url, WebError
from .retrieval import rrf


class OnlineSession:
    def __init__(self, service, model, result, progress_id=None):
        self.service,self.model,self.result=service,model,result
        self.s=service.s
        self.progress_id=progress_id
        self.discovered={}; self.reads=set(); self.searches=set(); self.local_queries=set()
        self.pages=[];self.calls=0;self.rounds=0
        self.evidence={}
        result['online']={'searches':[],'pages':self.pages,'citation_validation':'snapshot_existence_and_scope_not_semantic_support'}
        result['tool_trace']=[]

    def progress(self, stage, **fields):
        self.service.progress(self.progress_id,stage,**fields)

    async def tool(self, name, inputs, function):
        started=time.monotonic();self.progress(name)
        record={'tool':name,'input':inputs,'status':'running'}
        self.result['tool_trace'].append(record)
        try:
            value=await function()
            record['status']='ok'
            record['result_count']=len(value) if isinstance(value,list) else None
            return value
        except asyncio.CancelledError:
            record['status']='cancelled';raise
        except Exception as exc:
            record['status']='failed'
            # Only controlled error codes, never provider exception text/secrets.
            record['error']=str(exc) if isinstance(exc,WebError) else type(exc).__name__
            self.result['warnings'].append(name+'_failed:'+record['error'])
            return []
        finally:
            record['elapsed_ms']=round((time.monotonic()-started)*1000)

    async def search(self, query):
        key=' '.join(query.casefold().split())
        if key in self.searches: raise WebError('duplicate_search_denied')
        if self.calls>=self.s.search_max_calls: raise WebError('search_call_limit')
        self.searches.add(key);self.calls+=1
        response=await self.service.search.search(query)
        found=response['results'][:self.s.search_max_results]
        self.result['online']['searches'].append({'query':query,'results':found,
            'provider':response.get('provider'),'usage':response.get('usage')})
        for row in found:
            try: self.discovered.setdefault(canonical_url(row['url']),row)
            except WebError: continue
        return found

    async def read(self, url):
        url=canonical_url(url)
        if url not in self.discovered: raise WebError('read_requires_discovered_url')
        if url in self.reads: raise WebError('duplicate_read_denied')
        if len(self.reads)>=self.s.web_max_pages: raise WebError('page_limit')
        self.reads.add(url)
        page={'url':url,'title':self.discovered[url].get('title',''),'read_status':'reading'}
        self.pages.append(page)
        try:
            snapshot=await self.service.reader.read(self.discovered[url])
            if snapshot.get('read_status')!='body_read' or not snapshot.get('text') or not snapshot['id'].startswith('web:'):
                raise WebError('page_reader_contract')
            # A unique snapshot ID is append-only. Old citations never overwrite.
            existing=self.service.store.one('SELECT body FROM web_evidence WHERE id=?',(snapshot['id'],))
            if existing and json.loads(existing['body'])!=snapshot:
                raise WebError('immutable_snapshot_id_collision')
            if not existing:
                self.service.store.execute('INSERT INTO web_evidence VALUES (?,?)',
                                           (snapshot['id'],json.dumps(snapshot,ensure_ascii=False)))
            page.update({k:snapshot.get(k) for k in ('id','url','title','fetched_at','published_at','truncated','read_status')})
            self.evidence[snapshot['id']]={k:v for k,v in snapshot.items() if k!='html_snapshot'}
            self.progress('read_page',completed=len([p for p in self.pages if p['read_status']!='reading']),total=len(self.reads))
            return [self.evidence[snapshot['id']]]
        except BaseException as exc:
            page['read_status']='cancelled' if isinstance(exc,asyncio.CancelledError) else 'body_unavailable'
            page['error']=str(exc) if isinstance(exc,WebError) else type(exc).__name__
            page['snippet']=self.discovered[url].get('snippet','')
            raise

    async def fixed(self, question):
        await self.tool('search_web',{'query':question},lambda:self.search(question))
        urls=list(self.discovered)[:self.s.web_max_pages]
        self.progress('read_pages',completed=0,total=len(urls))
        read=await asyncio.gather(*(self.tool('read_page',{'url':u},lambda u=u:self.read(u)) for u in urls))
        return [e for group in read for e in group]

    async def local(self, query, strategy):
        if self.result['scope']=='web': raise WebError('local_tool_outside_scope')
        key=' '.join(query.casefold().split())
        if key in self.local_queries: raise WebError('duplicate_local_query_denied')
        self.local_queries.add(key)
        vector=None
        if strategy!='sparse':
            vector=(await self.model.embed_query([query]))[0]
        branches,fused=await self.service.blocking(self.service.index.search,query,vector,
            self.service.store.vocabulary(),self.result['snapshot']['generations'],strategy)
        self.result['retrieval'].setdefault('supplemental',[]).append({'query':query,'branches':branches,'fused':fused})
        evidence=[self.service.evidence(h['id']) for h in fused]
        if any(e['generation'] not in self.result['snapshot']['generations'] or
               e['document'] not in self.result['snapshot']['documents'] for e in evidence):
            raise WebError('local_tool_scope_violation')
        return evidence

    def candidate_pool(self):
        """Fuse independent local retrieval rounds, then interleave source families.

        An appended retrieval must compete by rank, not by insertion time. All
        raw branches and exclusions remain in the run for inspection.
        """
        retrieval=self.result['retrieval']
        rounds=[{'branches':retrieval.get('branches',{})},*retrieval.get('supplemental',[])]
        branches={f'round_{i}/{name}':[SimpleNamespace(id=h['id'],score=h['score']) for h in hits]
                  for i,record in enumerate(rounds) for name,hits in record['branches'].items()}
        fused=rrf(branches,sum(len(hits) for hits in branches.values()),self.s.rrf_k)
        if len(rounds)>1: retrieval['agent_fused']=fused
        local=[self.evidence[h['id']] for h in fused if h['id'] in self.evidence]
        known={e['id'] for e in local}
        local.extend(e for e in self.evidence.values() if e.get('source_type')!='web' and e['id'] not in known)
        web=[e for e in self.evidence.values() if e.get('source_type')=='web']
        discovery_order={url:i for i,url in enumerate(self.discovered)}
        web.sort(key=lambda e:discovery_order.get(e.get('discovered_url',e.get('url')),len(discovery_order)))
        merged=[]
        for i in range(max(len(local),len(web))):
            if i<len(local): merged.append(local[i])
            if i<len(web): merged.append(web[i])
        pool=merged[:self.s.retrieval_candidate_pool]
        retrieval['candidate_pool']=[e['id'] for e in pool]
        retrieval['pool_decisions']=[{'id':e['id'],'selection':'candidate' if i<len(pool) else 'discarded',
            'reason':'source_interleave_after_round_rrf' if i<len(pool) else 'candidate_pool_limit'} for i,e in enumerate(merged)]
        retrieval['pool_decisions'].extend({'id':h['id'],'selection':'discarded','reason':'per_query_candidate_pool_limit'}
            for h in fused if h['id'] not in self.evidence)
        return pool

    async def agent(self, question, initial, strategy):
        self.evidence.update({e['id']:e for e in initial})
        self.local_queries.add(' '.join(question.casefold().split()))
        allowed=['answer','stop']
        if self.result['scope']!='web': allowed.append('retrieve_local')
        if self.result['scope']!='documents': allowed.extend(['search_web','read_page'])
        self.result['agent']={'kind':'model_selected_tools','decisions':[],'max_supplemental_rounds':self.s.agent_max_rounds,
                              'stop_reason':None,'supplemental_rounds':0}
        agent=self.result['agent']
        # Search/retrieval spend rounds; reads have an independent global page cap.
        max_decisions=self.s.agent_max_rounds+self.s.web_max_pages+1
        for step in range(max_decisions):
            state={'question':question,'allowed_actions':allowed,'remaining_rounds':self.s.agent_max_rounds-self.rounds,
                'remaining_pages':self.s.web_max_pages-len(self.reads),
                'evidence':[{'id':e['id'],'source_type':e.get('source_type','document'),'text':e['text']} for e in self.candidate_pool()],
                'discovered':[{'url':u,'title':r.get('title'),'snippet':r.get('snippet','')[:400],
                               'already_read':u in self.reads} for u,r in list(self.discovered.items())[:30]],
                'last_tools':self.result['tool_trace'][-3:]}
            started=time.monotonic(); decision={'step':step,'status':'running'}
            agent['decisions'].append(decision); self.progress('agent_decision',step=step)
            try:
                instruction=('根据中间证据自主选择一个工具或结束。输出 JSON：action 和 args。'
                    'action 只能取 allowed_actions。retrieve_local/search_web 的 args 为 {query:字符串}；'
                    'read_page 的 args 为 {url:已发现的网址}；answer/stop 的 args 为 {}。'
                    '资料范围和预算由程序控制。剩余轮数为0不得搜索或检索；不得重复查询/读取。'
                    '网页摘要仅用于发现，未经正文读取不能作为回答证据。充分则 answer，无法继续则 stop。')
                state,decision['input']=self.model.fit_text_items(instruction,state,'evidence',question,max_chars=900)
                action=await self.model.structured(instruction,state)
                if not isinstance(action,dict) or set(action)!={'action','args'} or action['action'] not in allowed or not isinstance(action['args'],dict):
                    raise WebError('agent_action_contract')
                name,args=action['action'],action['args']
                expected={'query'} if name in {'retrieve_local','search_web'} else {'url'} if name=='read_page' else set()
                if set(args)!=expected: raise WebError('agent_args_contract')
                if expected and (not isinstance(next(iter(args.values())),str) or not 1<=len(next(iter(args.values())).strip())<=2000):
                    raise WebError('agent_args_contract')
                decision.update(action=name,args=args,status='validated')
                if name in {'answer','stop'}:
                    agent['stop_reason']='model_'+name
                    return self.candidate_pool()
                if name in {'retrieve_local','search_web'}:
                    if self.rounds>=self.s.agent_max_rounds: raise WebError('agent_round_limit')
                    self.rounds+=1;agent['supplemental_rounds']=self.rounds
                    if name=='search_web':
                        await self.tool(name,args,lambda:self.search(args['query']))
                    else:
                        found=await self.tool(name,args,lambda:self.local(args['query'],strategy))
                        self.evidence.update({e['id']:e for e in found})
                else:
                    found=await self.tool(name,args,lambda:self.read(args['url']))
                    self.evidence.update({e['id']:e for e in found})
            except asyncio.CancelledError:
                decision['status']='cancelled';agent['stop_reason']='request_cancelled';raise
            except Exception as exc:
                decision.update(status='rejected',error=str(exc) if isinstance(exc,WebError) else type(exc).__name__)
                agent['stop_reason']='controller_rejection'
                self.result['warnings'].append('agent_stopped:'+decision['error'])
                return self.candidate_pool()
            finally:
                decision['elapsed_ms']=round((time.monotonic()-started)*1000)
        agent['stop_reason']='decision_limit'
        return self.candidate_pool()
