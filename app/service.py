import asyncio
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone

from fastapi import HTTPException

from .models import CloudModels, AnswerValidationError
from .context import select_context
from .retrieval import Index, tokenize
from .storage import Store
from .graph import Graph, validate_facts
from .insights import insights
from .cache import AnswerCache, namespace
from .observability import Observer


def uid(*parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, '|'.join(parts)))


def resolve_route(question, route, scope, workflow):
    if scope != 'documents' or workflow == 'agent': return 'qa'
    if route != 'auto': return route
    if any(w in question for w in ('全文','全局')): return 'summary'
    return 'relation' if any(w in question for w in ('关系','依赖','供应链')) else 'qa'


class Service:
    @staticmethod
    async def blocking(function,*args):
        task=asyncio.create_task(asyncio.to_thread(function,*args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Synchronous SDK operations cannot be killed safely. Finish their
            # bounded IO before releasing snapshot locks or closing storage.
            await asyncio.gather(task,return_exceptions=True)
            raise

    def __init__(self, settings):
        self.s = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / 'files').mkdir(exist_ok=True)
        self.store = Store(settings.data_dir)
        self.graph = Graph(settings, self.store)
        self.cache = AnswerCache(settings)
        self.observer = Observer(settings, self.store)
        self.store.execute('CREATE TABLE IF NOT EXISTS summaries(id TEXT PRIMARY KEY,generation TEXT,body TEXT)')
        binding = json.dumps({'backend': settings.qdrant_url if settings.app_mode == 'cloud' else 'local',
                              'prefix': settings.collection_prefix}, sort_keys=True)
        previous = self.store.one("SELECT value FROM metadata WHERE key='storage_binding'")
        if previous and previous['value'] != binding:
            self.store.db.close()
            raise ValueError('Storage endpoint/prefix changed: use a separate DATA_DIR to preserve cleanup ownership')
        self.store.execute("INSERT OR IGNORE INTO metadata VALUES ('storage_binding',?)", (binding,))
        self.index = Index(settings)
        self.lock = asyncio.Lock()
        self.tasks = set()
        self.model_cooldowns = {}
        from .web import TavilySearch, PageReader
        self.search = TavilySearch(settings)
        self.reader = PageReader(settings)
        self.progress_states = {}
        self.model_meter = None

    def models(self, purpose='interactive'):
        model=CloudModels(self.s,self.model_cooldowns,purpose=purpose)
        model.budget.meter=self.model_meter
        return model

    def progress(self, progress_id, stage, **fields):
        if not progress_id: return
        if len(self.progress_states)>=100 and progress_id not in self.progress_states:
            self.progress_states.pop(next(iter(self.progress_states)))
        self.progress_states[progress_id]={'stage':stage,**fields,'updated_at':datetime.now(timezone.utc).isoformat()}

    async def save_web(self, evidence_id):
        snapshot=self.evidence(evidence_id)
        if snapshot.get('source_type')!='web' or snapshot.get('read_status')!='body_read':
            raise HTTPException(422,'read_web_snapshot_required')
        content='\n'.join(['来源网址：'+snapshot['url'],'网页标题：'+snapshot['title'],
            '获取时间：'+snapshot['fetched_at'],'发布时间：'+str(snapshot.get('published_at') or '未知'),
            '网络证据 ID：'+snapshot['id'],'','正文快照：',snapshot['text']])
        return await self.upload((snapshot['title'][:100] or '网页快照')+'.txt','.txt',content.encode())

    def launch(self, job):
        task = asyncio.create_task(self.ingest(job))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def upload(self, name, suffix, content, document=None):
        async with self.lock:
            st = self.store
            suffix = suffix.lower()
            if sum(not t.done() for t in self.tasks) >= 8:
                raise HTTPException(429, '摄入队列已满，请稍后重试')
            sha = hashlib.sha256(content).hexdigest()
            if document:
                doc = st.one('SELECT * FROM documents WHERE id=? AND deleted=0', (document,))
                if not doc:
                    raise HTTPException(404, 'document_not_found')
            else:
                # Identical content and format is idempotent, regardless of supplied filename.
                old = st.one('SELECT d.id FROM documents d JOIN versions v ON v.document=d.id WHERE v.sha=? AND v.suffix=? AND d.deleted=0', (sha, suffix))
                document = old['id'] if old else str(uuid.uuid4())
                st.execute('INSERT OR IGNORE INTO documents(id,name) VALUES (?,?)', (document, name))
            old_version = st.one('SELECT * FROM versions WHERE document=? AND sha=? AND suffix=?', (document, sha, suffix))
            version = old_version['id'] if old_version else uid(document, sha, suffix)
            path = Path(old_version['path']) if old_version else self.s.data_dir / 'files' / (version + suffix)
            path.write_bytes(content)
            st.execute('INSERT OR IGNORE INTO versions VALUES (?,?,?,?,?)',
                       (version, document, sha, suffix, str(path.resolve())))
            # Prefer a published generation; failed rebuilds never replace it.
            reusable = st.one('''SELECT g.id FROM generations g
                WHERE g.version=? AND g.config=? AND (g.vector_status='ready' OR EXISTS
                (SELECT 1 FROM jobs j WHERE j.generation=g.id AND j.status IN ('queued','running')))
                ORDER BY (g.vector_status='ready') DESC,g.rowid DESC LIMIT 1''', (version,self.s.fingerprint))
            generation = reusable['id'] if reusable else uid(version, self.s.fingerprint)
            if not reusable and st.one('SELECT id FROM generations WHERE id=?',(generation,)):
                generation = str(uuid.uuid4())
            st.execute('INSERT OR IGNORE INTO generations(id,version,config,contract,parse_status,vector_status) VALUES (?,?,?,?,?,?)',
                       (generation, version, self.s.fingerprint, json.dumps(self.s.contract), 'pending', 'pending'))
            existing = st.one("SELECT * FROM jobs WHERE generation=? AND status IN ('queued','running','completed') ORDER BY rowid DESC LIMIT 1", (generation,))
            if existing:
                if existing['status'] == 'completed':
                    st.execute('UPDATE documents SET active=? WHERE id=?', (generation, document))
                return {'document_id': document, 'version_id': version, 'generation_id': generation, 'job_id': existing['id'], 'idempotent': True}
            job = str(uuid.uuid4())
            st.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', (job, generation, 'queued', 'queued', None))
            self.launch(job)
            return {'document_id': document, 'version_id': version, 'generation_id': generation, 'job_id': job, 'idempotent': False}

    async def parse(self, record):
        output = self.s.data_dir / 'files' / (record['generation'] + '.parsed.json')
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'app.parsing',
            record['path'], record['suffix'], str(output.resolve()),
            json.dumps({'size':self.s.chunk_size,'strategy':self.s.chunk_strategy,'overlap':self.s.chunk_overlap}),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            _, error = await asyncio.wait_for(process.communicate(), timeout=60)
            if process.returncode:
                raise ValueError('parse_failed_' + error.decode(errors='replace').strip()[-80:])
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        parsed = json.loads(output.read_text(encoding='utf-8'))
        if len(parsed['chunks']) > self.s.max_chunks:
            raise ValueError('too_many_chunks')
        return parsed, str(output.resolve())

    async def ingest(self, job):
        async with self.lock:
            st = self.store
            rec = st.one('''SELECT j.generation,v.*,d.deleted FROM jobs j
                JOIN generations g ON g.id=j.generation JOIN versions v ON v.id=g.version
                JOIN documents d ON d.id=v.document WHERE j.id=?''', (job,))
            if not rec or rec['deleted']:
                st.execute("UPDATE jobs SET status='cancelled',stage='deleted' WHERE id=?", (job,))
                return
            gen = rec['generation']
            st.execute("UPDATE jobs SET status='running',stage='parse',error=NULL WHERE id=?", (job,))
            st.execute("UPDATE generations SET parse_status='building',vector_status='building' WHERE id=?", (gen,))
            st.event('ingest_start', job_id=job, generation=gen)
            started = time.monotonic()
            model = None
            try:
                async with self.observer.stage('parse',job_id=job,generation=gen):
                    parsed, output = await self.parse(rec)
                st.execute("UPDATE generations SET parse_status='ready',parsed=? WHERE id=?", (output, gen))
                chunks = parsed['chunks']
                for i, c in enumerate(chunks):
                    c['id'] = uid(gen, str(i))
                    c['generation'] = gen
                    c['version'] = rec['id']
                    c['document'] = rec['document']
                    st.execute('INSERT OR REPLACE INTO chunks VALUES (?,?,?)', (c['id'], gen, json.dumps(c, ensure_ascii=False)))
                st.execute("UPDATE jobs SET stage='embedding' WHERE id=?", (job,))
                # Parsing/queueing does not spend the model processing deadline.
                model = self.models('background')
                from .checkpoints import Checkpoints
                model.embedding_cache = Checkpoints(st,gen,'embedding')
                vectors = None
                if self.s.app_mode != 'offline':
                    async with self.observer.stage('embedding',job_id=job,generation=gen):
                        vectors = await model.embed([c['text'] for c in chunks])
                vocab = await asyncio.to_thread(st.vocabulary, [t for c in chunks for t in tokenize(c['text'])])
                st.execute("UPDATE jobs SET stage='index' WHERE id=?", (job,))
                # Revision advances before any remote mutation, including partial failure.
                st.bump()
                async with self.observer.stage('index',job_id=job,generation=gen):
                    await asyncio.to_thread(self.index.upsert, chunks, vectors, vocab, gen)
                st.execute("UPDATE generations SET vector_status='ready' WHERE id=?", (gen,))
                st.execute('UPDATE documents SET active=? WHERE id=?', (gen, rec['document']))
                st.execute("UPDATE generations SET graph_status='not_built',summary_status='not_built' WHERE id=?", (gen,))
                if self.s.graph_enabled and self.s.app_mode != 'offline':
                    async with self.observer.stage('graph_extract',job_id=job,generation=gen):
                        graph_model = self.models('background')
                        await self._build_graph(gen, chunks, graph_model)
                        model.usage.extend(graph_model.usage)
                st.execute("UPDATE jobs SET status='completed',stage='ready' WHERE id=?", (job,))
                await self.observer.record('ingest_complete', job_id=job, generation=gen, chunks=len(chunks),
                         elapsed_ms=round((time.monotonic()-started)*1000), usage=model.usage)
            except asyncio.CancelledError:
                st.execute("UPDATE jobs SET status='interrupted',error='shutdown_retry_required' WHERE id=?", (job,))
                st.execute("UPDATE generations SET vector_status=CASE WHEN vector_status='ready' THEN 'ready' ELSE 'interrupted' END WHERE id=?", (gen,))
                st.execute("UPDATE generations SET graph_status='interrupted' WHERE id=? AND graph_status='building'",(gen,))
                raise
            except Exception as exc:
                error = type(exc).__name__
                if isinstance(exc, (ValueError, TimeoutError)) and (str(exc).startswith('parse_failed_') or str(exc) in ('too_many_chunks', 'embedding_batch_contract', 'embedding_dimension_or_values', 'embedding_response_model_mismatch', 'background_call_or_time_budget_exhausted')):
                    error = str(exc)
                st.execute("UPDATE jobs SET status='failed',error=? WHERE id=?", (error, job))
                st.execute("UPDATE generations SET parse_status=CASE WHEN parse_status='building' THEN 'failed' ELSE parse_status END,vector_status=CASE WHEN vector_status='ready' THEN 'ready' ELSE 'failed' END WHERE id=?", (gen,))
                st.event('ingest_failed', job_id=job, error=error)
                if model:
                    await self.observer.record('ingest_model_budget',job_id=job,calls=model.budget.calls,
                                               purpose=model.budget.purpose,usage=model.usage)

    async def retry(self, job):
        async with self.lock:
            rec = self.store.one('''SELECT j.*,g.config,d.deleted FROM jobs j JOIN generations g ON g.id=j.generation
                JOIN versions v ON v.id=g.version JOIN documents d ON d.id=v.document WHERE j.id=?''', (job,))
            if not rec or rec['deleted']:
                raise HTTPException(404, 'job_not_found')
            if rec['config'] != self.s.fingerprint:
                raise HTTPException(409, '重新上传以创建当前配置的索引代次')
            if rec['status'] not in ('failed', 'interrupted'):
                raise HTTPException(409, 'job_not_retryable')
            self.store.execute("UPDATE jobs SET status='queued',stage='queued',error=NULL WHERE id=?", (job,))
            self.launch(job)
            return {'job_id': job, 'status': 'queued'}

    async def rebuild(self, document):
        async with self.lock:
            st = self.store
            row = st.one('''SELECT g.* FROM documents d JOIN generations g ON g.id=d.active
                WHERE d.id=? AND d.deleted=0''', (document,))
            if not row:
                raise HTTPException(409, '没有可重建的活动版本，请上传原文件')
            # Reparse the recorded version into a new configuration generation;
            # keep the old generation and its immutable citations intact.
            if st.one('''SELECT j.id FROM jobs j JOIN generations g ON g.id=j.generation
                JOIN versions v ON v.id=g.version WHERE v.document=? AND j.status IN ('queued','running')''',(document,)):
                raise HTTPException(409, 'job_already_running')
            generation = str(uuid.uuid4())
            st.execute('INSERT OR IGNORE INTO generations(id,version,config,contract,parse_status,vector_status) VALUES (?,?,?,?,?,?)',
                       (generation, row['version'], self.s.fingerprint, json.dumps(self.s.contract), 'pending', 'pending'))
            if st.one("SELECT id FROM jobs WHERE generation=? AND status IN ('queued','running')", (generation,)):
                raise HTTPException(409, 'job_already_running')
            job = str(uuid.uuid4())
            st.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', (job, generation, 'queued', 'rebuild', None))
            self.launch(job)
            return {'job_id': job, 'status': 'queued'}

    async def delete(self, document):
        async with self.lock:
            st = self.store
            if not st.one('SELECT id FROM documents WHERE id=?', (document,)):
                raise HTTPException(404, 'document_not_found')
            st.execute("UPDATE documents SET deleted=1,active=NULL,cleanup_status='pending' WHERE id=?", (document,))
            st.bump()
            await self.cache.invalidate()
            generations = st.all('SELECT g.* FROM generations g JOIN versions v ON v.id=g.version WHERE v.document=?', (document,))
            try:
                # A shared persistent Qdrant client can delete all configuration collections.
                for config in set(g['config'] for g in generations):
                    name = f'{self.s.collection_prefix}_{config}'
                    ids = [g['id'] for g in generations if g['config'] == config]
                    from qdrant_client import models as m
                    if await asyncio.to_thread(self.index.client.collection_exists, name):
                        await asyncio.to_thread(self.index.client.delete, name,
                            points_selector=m.FilterSelector(filter=Index.scope(ids)), wait=True)
                await asyncio.to_thread(self.graph.delete, [g['id'] for g in generations])
                for g in generations:
                    st.execute('DELETE FROM processing_checkpoints WHERE generation=?', (g['id'],))
                    st.execute('DELETE FROM summaries WHERE generation=?', (g['id'],))
                    (self.s.data_dir / 'files' / (g['id'] + '.parsed.json')).unlink(missing_ok=True)
                    if g['parsed']:
                        Path(g['parsed']).unlink(missing_ok=True)
                    st.execute('DELETE FROM chunks WHERE generation=?', (g['id'],))
                for v in st.all('SELECT path FROM versions WHERE document=?', (document,)):
                    Path(v['path']).unlink(missing_ok=True)
                # Query records contain excerpts; delete records whose frozen scope includes this document.
                for run in st.all('SELECT * FROM runs'):
                    if document in json.loads(run['body'])['snapshot']['documents']:
                        st.execute('DELETE FROM runs WHERE id=?', (run['id'],))
                st.execute("UPDATE documents SET cleanup_status='completed' WHERE id=?", (document,))
                return {'status': 'deleted'}
            except Exception as exc:
                st.event('delete_pending', document_id=document, error=type(exc).__name__)
                return {'status': 'cleanup_pending', 'retry': 'repeat_DELETE'}

    def evidence(self, chunk_id):
        if chunk_id.startswith('web:'):
            row = self.store.one('SELECT body FROM web_evidence WHERE id=?', (chunk_id,))
            if not row:
                raise HTTPException(410, 'web_snapshot_unavailable')
            return json.loads(row['body'])
        row = self.store.one('''SELECT c.body,d.deleted,d.name FROM chunks c JOIN generations g ON g.id=c.generation
            JOIN versions v ON v.id=g.version JOIN documents d ON d.id=v.document WHERE c.id=?''', (chunk_id,))
        if not row or row['deleted']:
            raise HTTPException(410, 'evidence_deleted_or_unavailable')
        return {**json.loads(row['body']), 'document_name':row['name'], 'source_type':'document'}

    def run(self, run_id):
        row = self.store.one('SELECT body FROM runs WHERE id=?', (run_id,))
        if not row:
            raise HTTPException(404, 'run_missing_or_deleted')
        result = json.loads(row['body'])
        result['document_names'] = {}
        # Enforce logical deletion on every read, including legacy records left
        # behind by an earlier remote cleanup failure. Reject the whole mixed run.
        for document in result['snapshot']['documents']:
            doc = self.store.one('SELECT deleted,name FROM documents WHERE id=?', (document,))
            if not doc or doc['deleted']:
                raise HTTPException(410, 'run_scope_deleted_or_unavailable')
            result['document_names'][document] = doc['name']
        return result

    async def _build_graph(self, generation, chunks, model):
        # Capability changes invalidate answers even if the subsequent write fails.
        self.store.bump()
        self.store.execute("UPDATE generations SET graph_status='building' WHERE id=?", (generation,))
        facts, errors = [], []
        from .checkpoints import Checkpoints
        from .reliability import BudgetExhausted
        checkpoints = Checkpoints(self.store,generation,'graph:'+uid(self.s.llm_model,self.s.fallback_model or '',
            self.s.llm_api_base or self.s.model_api_base or '', self.s.generation_options_fingerprint, 'facts-v1'))
        for c in chunks:
            try:
                saved = checkpoints.get(c['id'])
                if saved is not None:
                    facts.extend(saved)
                    continue
                raw = await model.structured('输出 facts 数组，每项必须有 subject/object（name,kind,aliases），relation '
                    '仅 produces/supplies/depends_on/affects/has_metric/reports；quote 必须逐字来自原文；'
                    'value/unit/period/scope 可为 null，但 has_metric 均必填；assertion 为 historical/forecast/opinion。'
                    'kind 为 company/product/supplier/metric/risk/other。不确定关系不抽取，禁止把共现当作关系。',
                    {'text':c['text']})
                validated = validate_facts(raw.get('facts'), c)
                for f in validated:
                    f['extraction_model'] = model.usage[-1]['model'] if model.usage else self.s.llm_model
                facts.extend(validated)
                checkpoints.put(c['id'],validated)
            except BudgetExhausted:
                errors.append({'chunk':c['id'], 'error':'budget_exhausted_resume_required'})
                break
            except Exception as exc:
                errors.append({'chunk':c['id'], 'error':type(exc).__name__})
        try:
            await asyncio.to_thread(self.graph.replace, generation, facts)
            status = 'partial' if errors else 'ready'
        except Exception as exc:
            errors.append({'error':type(exc).__name__}); status = 'failed'
        self.store.execute('UPDATE generations SET graph_status=? WHERE id=?', (status,generation))
        self.store.event('graph_build', generation=generation, status=status, facts=len(facts), failures=len(errors))
        return {'generation':generation,'status':status,'facts':len(facts),'errors':errors}

    async def build_graph(self, document):
        async with self.lock:
            row = self.store.one('SELECT d.active,g.config FROM documents d JOIN generations g ON g.id=d.active WHERE d.id=? AND d.deleted=0', (document,))
            if not row or not row['active']:
                raise HTTPException(409, 'document_not_ready')
            if row['config'] != self.s.fingerprint:
                raise HTTPException(409, 'rebuild_current_index_before_graph')
            if self.s.app_mode == 'offline':
                raise HTTPException(409, 'offline_has_no_model_extraction')
            chunks = [json.loads(r['body']) for r in self.store.all('SELECT body FROM chunks WHERE generation=?',(row['active'],))]
            build_id=str(uuid.uuid4())
            model=self.models('background')
            async with self.observer.stage('graph_extract',run_id=build_id,generation=row['active']):
                result=await self._build_graph(row['active'],chunks,model)
            result['build_id']=build_id
            await self.observer.record('graph_extraction_complete',run_id=build_id,status=result['status'],usage=model.usage)
            return result

    async def query(self,question,documents,request_id=None,route='auto',strategy='hybrid',bypass_cache=False,rerank=False,
                    scope='documents', workflow='fixed', progress_id=None):
        if scope not in {'documents','web','combined'} or workflow not in {'fixed','agent'}:
            raise HTTPException(422,'invalid_scope_or_workflow')
        if strategy not in {'sparse','dense','hybrid','multi','graph'}:
            raise HTTPException(422,'invalid_strategy')
        if scope != 'web' and not documents:
            raise HTTPException(422,'documents_required')
        if scope == 'web':
            documents = []
        if scope != 'documents' and route in {'summary','relation'}:
            raise HTTPException(422,'online_scope_supports_qa_only')
        if workflow == 'agent' and (self.s.app_mode == 'offline' or route in {'summary','relation'}):
            raise HTTPException(409,'agent_requires_model_and_qa')
        if workflow == 'agent' and strategy not in {'sparse','dense','hybrid'}:
            raise HTTPException(422,'agent_requires_base_retrieval_strategy')
        background = resolve_route(question,route,scope,workflow) == 'summary'
        try:
            self.progress(progress_id,'queued')
            return await asyncio.wait_for(self._query(question,documents,request_id,route,strategy,bypass_cache,rerank,
                scope,workflow,progress_id),self.s.background_timeout if background else self.s.request_timeout)
        except asyncio.TimeoutError:
            raise HTTPException(504,'request_deadline_exceeded_while_queued')
        finally:
            self.progress(progress_id,'finished')

    async def _query(self, question, documents, request_id=None, route='auto', strategy='hybrid', bypass_cache=False, rerank=False,
                     scope='documents', workflow='fixed', progress_id=None):
        arrived = time.monotonic()
        async with self.lock:
            st = self.store
            selected = st.all('''SELECT d.id,d.active,g.version FROM documents d JOIN generations g ON g.id=d.active
                WHERE d.deleted=0 AND g.vector_status='ready' AND g.config=?''', (self.s.fingerprint,))
            selected = [d for d in selected if d['id'] in documents]
            if {d['id'] for d in selected} != set(documents):
                raise HTTPException(409, '所选文档未就绪、已删除或索引配置不匹配')
            run = str(uuid.uuid4())
            snapshot = {'documents': documents, 'generations': [d['active'] for d in selected],
                        'versions': [d['version'] for d in selected], 'corpus_revision': st.revision(),
                        'collection': self.index.name, 'contract': self.s.contract,
                        'idf_scope': 'whole_collection_including_retained_and_partial_generations'}
            result = {'run_id': run, 'request_id': request_id, 'question': question, 'mode': self.s.app_mode, 'snapshot': snapshot, 'evidence': [],
                      'claims': [], 'status': 'refused', 'warnings': [], 'steps': [], 'usage': [],
                      'created_at': datetime.now(timezone.utc).isoformat()}
            result['scope'] = scope
            result['workflow'] = workflow
            result['policy'] = {'prompt': 'online-citations-v1', 'controller':'round-rrf-bounded-input-v2',
                                'rerank_input':'equal_text_caps_full_request_bytes_v1',
                                'rrf_k': self.s.rrf_k, 'branch_limit': self.s.retrieval_branch_limit,
                                'fused_limit': self.s.retrieval_candidate_pool, 'top_k':self.s.retrieval_top_k,
                                'context_tokens': self.s.evidence_bytes, 'evidence_budget_bytes':self.s.evidence_bytes,
                                'model_request_budget_bytes': self.s.model_request_budget_bytes,
                                'scope':scope,'workflow':workflow,'agent_max_rounds':self.s.agent_max_rounds,
                                'search_provider':self.s.search_provider,'search_max_results':self.s.search_max_results,
                                'network_limits':{'search_calls':self.s.search_max_calls,'search_seconds':self.s.search_timeout,
                                    'page_seconds':self.s.web_timeout,'page_bytes':self.s.web_max_bytes,
                                    'page_text_chars':self.s.web_max_text_chars,'pages':self.s.web_max_pages,
                                    'redirects':self.s.web_max_redirects,'concurrency':self.s.web_concurrency},
                                'generation_max_tokens':self.s.generation_max_tokens,
                                'budget_method': 'model_evidence_JSON_UTF8_bytes_v2', 'generation_model': self.s.llm_model,
                                'answer_contract':'explicit_refusal_and_full_citation_ids_v2',
                                'context_allocation':'remaining_candidates_equal_shares_v1' if scope!='documents' else 'rank_order_greedy_v1',
                                'retries': self.s.retry_attempts, 'citation_validation': 'existence_and_scope_not_entailment',
                                'route':route,'strategy':strategy,'graph':'facts-v1','summary':'summary-v1',
                                'graph_storage':uid(self.s.neo4j_uri or 'local',self.s.neo4j_database,self.s.collection_prefix),
                                'rerank_requested':rerank,'rerank':self.s.rerank_model if rerank else None,'fallback':self.s.fallback_model,
                                'generation_endpoint':self.s.llm_api_base or self.s.model_api_base,
                                'generation_options':self.s.generation_options_fingerprint}
            actual_route = resolve_route(question,route,scope,workflow)
            background = actual_route == 'summary'
            model = self.models('background' if background else 'interactive')
            started = time.monotonic()
            result['queue_ms'] = round((started-arrived)*1000)
            vector = None
            cache_scope = namespace(snapshot,result['policy'])
            use_cache = self.s.cache_enabled and not bypass_cache and scope == 'documents' and workflow == 'fixed' and route != 'summary' and not any(w in question for w in ('全文','全局'))
            result['cache'] = {'status':'bypass' if not use_cache else 'miss'}
            async def cached(vector=None):
                item, hit = await self.cache.lookup(cache_scope,question,vector)
                if not item: return None
                current = json.loads(json.dumps(item['result']))
                current['source_model_calls'] = current.pop('model_calls', [])
                current['source_model_request_budget'] = current.pop('model_request_budget', [])
                current['source_model_budget'] = current.pop('model_budget', None)
                current.update(run_id=run,created_at=result['created_at'],request_id=request_id,question=question,cache=hit,usage=model.usage,model_calls=model.budget.events,
                               model_request_budget=model.request_audit,
                               model_budget={'purpose':model.budget.purpose,'limit_calls':model.budget.limit,'used_calls':model.budget.calls},
                               queue_ms=result['queue_ms'],elapsed_ms=round((time.monotonic()-started)*1000),
                               cost={'value':None if model.usage else 0,'status':'unknown' if model.usage else 'no_cloud_calls'})
                current['source_generation_cost'] = item['result'].get('cost')
                st.execute('INSERT INTO runs VALUES (?,?)',(run,json.dumps(current,ensure_ascii=False)))
                await self.observer.record('cache_hit',run_id=run,status=hit['status'],source_run=hit['source_run'],usage=model.usage)
                return current
            if use_cache:
                hit = await cached()
                if hit: return hit
            candidates, online = [], None
            try:
                self.progress(progress_id,'retrieve_local')
                result['route'] = actual_route
                result['strategy'] = strategy
                if actual_route == 'summary':
                    async with self.observer.stage('summary',run_id=run):
                        result.update(await insights(self, snapshot['generations'], model))
                    result['steps'] = ['固定快照','遍历全部章节与原文块','带原文引用的章节摘要','去重聚合（不跨口径相加）']
                    result['fallback_used'] = bool(self.s.fallback_model) and any(
                        e.get('status') == 'success' and e.get('model') == self.s.fallback_model
                        for e in model.budget.events)
                    if result['fallback_used']:
                        result['warnings'].append('fallback_model_used:主模型暂不可用，本次使用备用模型。')
                    result['usage'] = model.usage
                    result['model_calls'] = model.budget.events
                    result['model_request_budget'] = model.request_audit
                    result['model_budget'] = {'purpose':model.budget.purpose,'limit_calls':model.budget.limit,'used_calls':model.budget.calls}
                    result['elapsed_ms'] = round((time.monotonic()-started)*1000)
                    result['cost'] = {'value':None,'status':'no_cloud_calls' if self.s.app_mode=='offline' else 'unknown'}
                    st.execute('INSERT INTO runs VALUES (?,?)',(run,json.dumps(result,ensure_ascii=False)))
                    await self.observer.record('summary_complete',run_id=run,steps=result['steps'],usage=model.usage,coverage=result['coverage'])
                    return result
                if scope != 'web' and strategy != 'sparse' and self.s.app_mode != 'offline':
                    try:
                        vector = (await model.embed_query([question]))[0]
                        if use_cache:
                            hit = await cached(vector)
                            if hit: return hit
                    except Exception as exc:
                        result['warnings'].append('embedding_failed_sparse_only:' + type(exc).__name__)
                async with self.observer.stage('retrieval',run_id=run):
                    branches, fused = await self.blocking(self.index.search, question, vector, st.vocabulary(), snapshot['generations'],strategy)
                if strategy == 'sparse':
                    branches = {'sparse':branches.get('sparse',[])}
                if strategy == 'dense' and scope != 'web':
                    branches = {'dense':branches.get('dense',[])}
                    if vector is None:
                        result['warnings'].append('dense_unavailable_no_substitute')
                if strategy == 'multi' and scope != 'web' and self.s.app_mode != 'offline':
                    try:
                        rewrite = await model.structured('输出 query 字符串：保留实体、年份、否定、比较方向，改写为一次检索词。', {'question':question})
                        q = rewrite['query']
                        if not isinstance(q,str) or not 1 <= len(q) <= 2000:
                            raise ValueError('rewrite_contract')
                        v = (await model.embed_query([q]))[0]
                        extra, _ = await self.blocking(self.index.search,q,v,st.vocabulary(),snapshot['generations'])
                        for name, hits in extra.items():
                            known = {h['id'] for h in branches.get(name,[])}
                            branches.setdefault(name,[]).extend(h for h in hits if h['id'] not in known)
                        result['supplemental_queries'] = [q]
                    except Exception as exc:
                        result['warnings'].append('supplemental_failed:'+type(exc).__name__)
                if scope != 'web' and (strategy == 'graph' or (actual_route == 'relation' and strategy not in {'dense','sparse'})):
                    try:
                        statuses = [st.one('SELECT graph_status FROM generations WHERE id=?',(g,))['graph_status'] for g in snapshot['generations']]
                        if any(s != 'ready' for s in statuses):
                            result['warnings'].append('graph_not_complete_hybrid_fallback')
                        else:
                            async with self.observer.stage('graph_retrieval',run_id=run):
                                branches['graph'], result['graph_paths'] = await self.blocking(self.graph.search,question,snapshot['generations'])
                            for path in result['graph_paths']:
                                for fact in path:
                                    source=self.evidence(fact['chunk'])
                                    if source['generation'] not in snapshot['generations'] or source['generation']!=fact['generation'] or fact['quote'] not in source['text']:
                                        raise ValueError('graph_provenance_scope_violation')
                    except Exception as exc:
                        branches.pop('graph',None);result.pop('graph_paths',None)
                        result['warnings'].append('graph_failed_hybrid_fallback:'+type(exc).__name__)
                from types import SimpleNamespace
                from .retrieval import rrf
                for hits in branches.values():
                    for rank,hit in enumerate(hits,1):
                        hit.setdefault('query_rank',hit['rank']);hit['rank']=rank
                fusion_candidates = rrf({name:[SimpleNamespace(id=h['id'],score=h['score']) for h in hits] for name,hits in branches.items()},
                                        sum(len(hits) for hits in branches.values()),self.s.rrf_k)
                fused = fusion_candidates[:self.s.retrieval_candidate_pool]
                result['retrieval'] = {'branches': branches, 'fused': fused, 'fusion_candidates':fusion_candidates,
                    'candidate_pool':[h['id'] for h in fused],
                    'pool_decisions':[{'id':h['id'],'selection':'candidate' if i<len(fused) else 'discarded',
                        'reason':'rrf_rank' if i<len(fused) else 'candidate_pool_limit'} for i,h in enumerate(fusion_candidates)]}
                candidates = [self.evidence(hit['id']) for hit in fused]
                if any(c['generation'] not in snapshot['generations'] or c['document'] not in snapshot['documents'] for c in candidates):
                    raise ValueError('retrieval_scope_violation')
                if scope != 'documents' or workflow == 'agent':
                    from .online import OnlineSession
                    online = OnlineSession(self,model,result,progress_id)
                    online.evidence.update({c['id']:c for c in candidates})
                    if workflow == 'agent':
                        candidates = await online.agent(question,candidates,strategy)
                    else:
                        await online.fixed(question)
                        candidates = online.candidate_pool()
                if rerank and candidates:
                    if self.s.app_mode=='offline' or not self.s.rerank_model:
                        result['warnings'].append('rerank_not_configured_or_offline')
                    else:
                        try:
                            instruction='按问题相关性排序，输出 ids 数组，每个输入 id 恰好一次。text 可能是为请求预算摘录的片段。'
                            payload,audit=model.fit_text_items(instruction,
                                {'question':question,'candidates':[{'id':c['id'],'text':c['text']} for c in candidates]},'candidates',question)
                            result['rerank']={'model':self.s.rerank_model,'input':audit,'status':'running'}
                            ranked = await model.structured(instruction,payload,model=self.s.rerank_model)
                            ids=ranked.get('ids')
                            if not isinstance(ids,list) or len(ids)!=len(candidates) or set(ids)!={c['id'] for c in candidates}:
                                raise ValueError('rerank_contract')
                            by_id={c['id']:c for c in candidates}; candidates=[by_id[i] for i in ids]
                            result['rerank'].update(ids=ids,kind='LLM_listwise',status='completed')
                        except Exception as exc:
                            result.setdefault('rerank',{'model':self.s.rerank_model}).update(status='failed',error=type(exc).__name__)
                            result['warnings'].append('rerank_failed_original_order:'+type(exc).__name__)
                async with self.observer.stage('context_selection',run_id=run):
                    result['evidence'], result['context'] = select_context(candidates, question, self.s.evidence_bytes,self.s.retrieval_top_k,balanced=scope!='documents')
                decisions = {d['id']:d for d in result['context']['decisions']}
                for hit in fused:
                    hit.update({k:v for k,v in decisions.get(hit['id'],{}).items() if k!='id'})
                if any('excerpt' in e for e in result['evidence']):
                    result['warnings'].append('部分证据按上下文预算摘录；excerpt 记录相对原 chunk 的字符区间，引用链接可查看完整原块。')
                result['steps'] = ['固定文档版本/索引代次/语料修订号', '独立召回', 'RRF 融合', '引用范围与上下文预算检查']
                if result['evidence']:
                    if result.get('agent',{}).get('stop_reason')=='model_stop':
                        result['status']='agent_stopped'
                        result['warnings'].append('模型选择停止，已保留证据。')
                    elif self.s.app_mode == 'offline':
                        result['status'] = 'offline_extracts'
                        result['warnings'].append('离线词项检索与原文摘录；未调用 embedding 或生成模型，不代表问题已得到回答。')
                    else:
                        try:
                            self.progress(progress_id,'generation')
                            async with self.observer.stage('generation',run_id=run):
                                result.update(await model.answer(question, result['evidence']))
                        except Exception as exc:
                            result['status'] = 'generation_failed'
                            result['generation_error'] = {'type':type(exc).__name__,
                                'code':str(exc) if isinstance(exc,AnswerValidationError) else 'generation_request_failed'}
                            result['warnings'].append(type(exc).__name__ + ':保留证据，不返回模拟回答')
                elif candidates:
                    result['status'] = 'context_budget_exceeded'
                    result['warnings'].append('已召回证据，但证据元数据/最小摘录无法放入上下文预算，未调用生成模型。')
                else:
                    if strategy=='dense' and scope!='web' and vector is None:
                        result['status']='dense_unavailable'
                    result['warnings'].append('当前范围未召回证据，拒绝回答。')
            except asyncio.CancelledError:
                result['status']='request_timeout'
                result['warnings'].append('request_deadline_exceeded_models_cancelled_bounded_io_drained')
                # Keep successfully read snapshots and scoped retrievals even
                # when another online tool consumes the total request deadline.
                if online is not None: candidates = online.candidate_pool()
                if candidates:
                    result['evidence'],result['context']=select_context(candidates,question,self.s.evidence_bytes,self.s.retrieval_top_k,balanced=scope!='documents')
            except Exception as exc:
                result['status'] = 'retrieval_failed'
                result['warnings'].append(type(exc).__name__)
            result['fallback_used'] = any(e.get('status')=='success' and e.get('model')==self.s.fallback_model
                                          for e in model.budget.events) if self.s.fallback_model else False
            if result['fallback_used']:
                result['warnings'].append('fallback_model_used:主模型暂不可用，本次使用备用模型；结果不写入普通缓存。')
            result['usage'] = model.usage
            result['model_calls'] = model.budget.events
            result['model_request_budget'] = model.request_audit
            result['model_budget'] = {'purpose':model.budget.purpose,'limit_calls':model.budget.limit,'used_calls':model.budget.calls}
            result['elapsed_ms'] = round((time.monotonic() - started) * 1000)
            result['cost'] = {'value': None, 'status': 'unknown' if self.s.app_mode != 'offline' else 'no_cloud_calls'}
            st.execute('INSERT INTO runs VALUES (?,?)', (run, json.dumps(result, ensure_ascii=False)))
            await self.observer.record('query_complete', run_id=run, request_id=request_id, status=result['status'],
                elapsed_ms=result['elapsed_ms'],steps=result['steps'],usage=model.usage)
            if use_cache and result['status'] in ('answered','offline_extracts') and not result['warnings'][:]:
                await self.cache.put(cache_scope,question,vector,result)
            elif use_cache and result['status']=='offline_extracts' and len(result['warnings'])==1:
                await self.cache.put(cache_scope,question,vector,result)
            return result

    async def close(self):
        # Let any in-flight synchronous index operation finish before closing its storage.
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.index.close()
        self.graph.close()
        await asyncio.to_thread(self.observer.close)
        self.store.db.close()
