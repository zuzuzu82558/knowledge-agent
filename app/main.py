import asyncio
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import portalocker
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import Settings
from .service import Service
from .formats import SUPPORTED


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    document_ids: list[str] = Field(default_factory=list, max_length=30)
    route: Literal['auto','qa','relation','summary'] = 'auto'
    strategy: Literal['sparse','dense','hybrid','multi','graph'] = 'hybrid'
    scope: Literal['documents','web','combined'] = 'documents'
    workflow: Literal['fixed','agent'] = 'fixed'
    progress_id: uuid.UUID | None = None
    bypass_cache: bool = False
    rerank: bool = False


class BodyLimit:
    """Bound total multipart bytes before the framework spools uploaded files."""
    def __init__(self, app, limit):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PUT'):
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            part = message.get('body', b'')
            size += len(part)
            if size > self.limit:
                return await JSONResponse({'detail': 'request_too_large'}, status_code=413)(scope, receive, send)
            chunks.append(part)
            if not message.get('more_body'):
                break
        sent = False
        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {'type':'http.request', 'body':b''.join(chunks), 'more_body':False}
            return await receive()
        await self.app(scope, replay, send)


def create_app(settings=None):
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        with portalocker.Lock(str(settings.data_dir / 'instance.lock'), timeout=0):
            app.state.service = Service(settings)
            try:
                yield
            finally:
                await app.state.service.close()

    app = FastAPI(title='Knowledge Agent', lifespan=lifespan)
    app.add_middleware(BodyLimit, limit=settings.max_upload_mb * 1024 * 1024 + 65536)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['localhost', '127.0.0.1', 'testserver'])

    @app.middleware('http')
    async def headers(request, call_next):
        request.state.request_id = str(uuid.uuid4())
        # Local UI has no cross-origin write contract; reject browser cross-origin mutations.
        if request.method in ('POST', 'DELETE', 'PUT'):
            origin = request.headers.get('origin')
            if origin and origin != str(request.base_url).rstrip('/'):
                return JSONResponse({'detail': 'cross_origin_denied'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; frame-ancestors 'none'"
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/api/health')
    async def health():
        return {'status': 'ok', 'mode': settings.app_mode, 'config': settings.fingerprint,
                'online_configured':settings.search_provider!='disabled' and bool(settings.search_api_key.get_secret_value()),
                'max_upload_mb': settings.max_upload_mb, 'cloud_validated': False, 'note': 'liveness only; use explicit cloud smoke test'}

    @app.get('/api/documents')
    async def documents():
        return app.state.service.store.all('''SELECT d.*,g.parse_status,g.vector_status,g.graph_status,g.summary_status,g.config,
            j.id AS job_id,j.status AS job_status,j.stage AS job_stage,j.error AS job_error
            FROM documents d LEFT JOIN generations g ON g.id=d.active
            LEFT JOIN jobs j ON j.id=(SELECT jj.id FROM jobs jj JOIN generations gg ON gg.id=jj.generation
                JOIN versions vv ON vv.id=gg.version WHERE vv.document=d.id ORDER BY jj.rowid DESC LIMIT 1)
            WHERE d.deleted=0 OR d.cleanup_status='pending' ''')

    @app.post('/api/documents', status_code=202)
    async def upload(file: UploadFile = File(...), document_id: str | None = Form(None)):
        name = (file.filename or 'document').replace('\\', '/').split('/')[-1][:200]
        suffix = Path(name).suffix.lower()
        if suffix not in SUPPORTED:
            raise HTTPException(415, '支持 PDF、MD、TXT、CSV、DOCX、HTML、PPTX、XLSX、XLS')
        content = bytearray()
        while part := await file.read(65536):
            content.extend(part)
            if len(content) > settings.max_upload_mb * 1024 * 1024:
                raise HTTPException(413, 'upload_too_large')
        await file.close()
        if not content:
            raise HTTPException(422, 'empty_file')
        return await app.state.service.upload(name, suffix, bytes(content), document_id)

    @app.get('/api/jobs')
    async def jobs():
        return app.state.service.store.all('''SELECT j.*,d.name AS document_name,d.id AS document_id
            FROM jobs j JOIN generations g ON g.id=j.generation JOIN versions v ON v.id=g.version
            JOIN documents d ON d.id=v.document WHERE d.deleted=0 ORDER BY j.rowid DESC LIMIT 100''')

    @app.get('/api/jobs/{job_id}')
    async def job(job_id: str):
        row = app.state.service.store.one('SELECT * FROM jobs WHERE id=?', (job_id,))
        if not row:
            raise HTTPException(404, 'job_not_found')
        return row

    @app.post('/api/jobs/{job_id}/retry', status_code=202)
    async def retry(job_id: str):
        return await app.state.service.retry(job_id)

    @app.delete('/api/documents/{document_id}')
    async def delete(document_id: str):
        return await app.state.service.delete(document_id)

    @app.post('/api/documents/{document_id}/rebuild', status_code=202)
    async def rebuild(document_id: str):
        return await app.state.service.rebuild(document_id)

    @app.post('/api/documents/{document_id}/graph')
    async def graph(document_id: str):
        return await app.state.service.build_graph(document_id)

    @app.get('/api/documents/{document_id}/versions')
    async def versions(document_id: str):
        service = app.state.service
        doc = service.store.one('SELECT * FROM documents WHERE id=? AND deleted=0', (document_id,))
        if not doc:
            raise HTTPException(410, 'document_deleted_or_missing')
        rows = service.store.all('''SELECT v.id AS version_id,v.sha,v.suffix,g.* FROM versions v
            JOIN generations g ON g.version=v.id WHERE v.document=? ORDER BY g.rowid DESC''', (document_id,))
        for row in rows:
            row['active'] = row['id'] == doc['active']
            path = row.pop('parsed')
            row['quality'] = None
            if path and Path(path).exists():
                import json
                parsed = json.loads(Path(path).read_text(encoding='utf-8'))
                row['quality'] = {k: parsed[k] for k in ('warnings', 'coverage')}
        return rows

    @app.get('/api/evidence/{chunk_id}')
    async def evidence(chunk_id: str):
        return app.state.service.evidence(chunk_id)

    @app.get('/api/versions/{version_id}/file')
    async def original_file(version_id: str):
        row = app.state.service.store.one('''SELECT v.path,v.suffix,d.name FROM versions v
            JOIN documents d ON d.id=v.document WHERE v.id=? AND d.deleted=0''',(version_id,))
        if not row or not Path(row['path']).is_file():
            raise HTTPException(410, 'evidence_deleted_or_unavailable')
        # Download untrusted originals as attachments, never execute HTML in our origin.
        return FileResponse(row['path'],media_type='application/octet-stream',
                            filename=Path(row['name']).stem+row['suffix'])

    @app.post('/api/query')
    async def query(body: Question, request: Request):
        if not body.question.strip():
            raise HTTPException(422, 'empty_question')
        return await app.state.service.query(body.question, list(dict.fromkeys(body.document_ids)), request_id=request.state.request_id,
            route=body.route,strategy=body.strategy,bypass_cache=body.bypass_cache,rerank=body.rerank,
            scope=body.scope,workflow=body.workflow,progress_id=str(body.progress_id) if body.progress_id else None)

    @app.get('/api/query-progress/{progress_id}')
    async def query_progress(progress_id: uuid.UUID):
        return app.state.service.progress_states.get(str(progress_id),{'stage':'waiting'})

    @app.post('/api/web-evidence/{evidence_id}/save',status_code=202)
    async def save_web(evidence_id: str):
        return await app.state.service.save_web(evidence_id)

    @app.get('/api/runs')
    async def recent_runs():
        service = app.state.service
        items=[]
        for row in service.store.all('SELECT id FROM runs ORDER BY rowid DESC LIMIT 200'):
            try:
                result=service.run(row['id'])
            except HTTPException:
                continue
            items.append({'run_id':row['id'],'question':result.get('question','历史问答'),
                          'status':result.get('status','unknown'),'documents':result.get('document_names',{}),
                          'scope':result.get('scope','documents'),'workflow':result.get('workflow','fixed'),
                          'created_at':result.get('created_at')})
            if len(items)>=50: break
        return items

    @app.get('/api/runs/{run_id}')
    async def run(run_id: str):
        return app.state.service.run(run_id)

    static = Path(__file__).parent / 'static'
    app.mount('/static', StaticFiles(directory=static), name='static')

    @app.get('/')
    async def home():
        return FileResponse(static / 'index.html')

    @app.get('/evidence/{chunk_id}')
    async def evidence_page(chunk_id: str):
        return FileResponse(static / 'evidence.html')

    return app
