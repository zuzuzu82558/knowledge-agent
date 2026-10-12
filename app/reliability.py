"""One retry owner; no automatic embedding model substitution."""
import asyncio
import time


class CallBudget:
    def __init__(self, settings, cooldowns=None, purpose='interactive'):
        self.s=settings; self.calls=0; self.purpose=purpose
        self.limit = settings.background_max_model_calls if purpose == 'background' else settings.max_model_calls
        self.deadline=time.monotonic()+(settings.background_timeout if purpose == 'background' else settings.request_timeout)
        self.failures=cooldowns if cooldowns is not None else {}; self.events=[]
        self.meter = None

    async def run(self, function, model, fallback=None, **kwargs):
        candidates=[model]+([fallback] if fallback and fallback!=model else [])
        last=None
        for candidate in candidates:
            if self.failures.get(candidate,0)>time.monotonic():
                self.events.append({'model':candidate,'status':'cooldown_skip'}); continue
            for attempt in range(self.s.retry_attempts+1):
                remaining=self.deadline-time.monotonic()
                if remaining<=0 or self.calls>=self.limit:
                    raise BudgetExhausted(self.purpose+'_call_or_time_budget_exhausted')
                if self.meter is not None:
                    if self.meter['calls'] >= self.meter['limit']:
                        raise BudgetExhausted('experiment_model_call_limit')
                    self.meter['calls'] += 1
                self.calls+=1
                try:
                    response=await asyncio.wait_for(function(model=candidate,**kwargs),min(remaining,self.s.model_call_timeout))
                    self.failures.pop(candidate,None)
                    self.events.append({'model':candidate,'attempt':attempt,'status':'success'})
                    return response
                except asyncio.CancelledError:
                    self.events.append({'model':candidate,'attempt':attempt,'status':'cancelled'})
                    raise
                except Exception as exc:
                    last=exc
                    transient=type(exc).__name__ in {'RateLimitError','ServiceUnavailableError','InternalServerError','APIConnectionError','Timeout','TimeoutError'}
                    self.events.append({'model':candidate,'attempt':attempt,'status':type(exc).__name__})
                    if not transient: raise
                    if attempt<self.s.retry_attempts:
                        await asyncio.sleep(min(.1*(2**attempt), max(0,self.deadline-time.monotonic())))
            self.failures[candidate]=time.monotonic()+10
        if last: raise last
        raise RuntimeError('all_models_in_cooldown')


class BudgetExhausted(TimeoutError):
    """A recoverable operation boundary, distinct from transient provider timeouts."""
