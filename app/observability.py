"""Local events are authoritative; optional SDK OTLP export never blocks answers."""
import asyncio
import time
import hashlib
from contextlib import asynccontextmanager


class Observer:
    def __init__(self, settings, store):
        self.s,self.st=settings,store
        self.client=None

    def export(self, name, fields):
        if self.s.app_mode=='offline' or not self.s.langfuse_public_key:
            return
        try:
            if self.client is None:
                from langfuse import Langfuse
                self.client=Langfuse(public_key=self.s.langfuse_public_key,
                    secret_key=self.s.langfuse_secret_key.get_secret_value(),base_url=self.s.langfuse_base_url,
                    timeout=3)
            ident=fields.get('run_id') or fields.get('job_id') or name
            context={'trace_id':hashlib.sha256(ident.encode()).hexdigest()[:32]}
            with self.client.start_as_current_observation(as_type='span',name=name,
                    metadata=fields,trace_context=context) as root:
                for usage in fields.get('usage',[]):
                    raw=usage.get('usage') or {}
                    counts={k:v for k,v in {'input':raw.get('prompt_tokens'),'output':raw.get('completion_tokens'),
                            'total':raw.get('total_tokens')}.items() if isinstance(v,int)}
                    with root.start_as_current_observation(as_type='generation',name=usage['operation'],model=usage['model'],
                            usage_details=counts):
                        pass
        except Exception as exc:
            self.st.event('observability_failed',error=type(exc).__name__)

    async def record(self,name,**fields):
        self.st.event(name,**fields)
        if self.s.app_mode!='offline' and self.s.langfuse_public_key:
            await asyncio.to_thread(self.export,name,fields)

    @asynccontextmanager
    async def stage(self,name,**fields):
        started=time.monotonic()
        try:
            yield
        finally:
            await self.record(name,**fields,elapsed_ms=round((time.monotonic()-started)*1000),
                              timing='measured_operation_retrospective_export')

    def close(self):
        if self.client:
            try: self.client.flush(); self.client.shutdown()
            except Exception: pass
