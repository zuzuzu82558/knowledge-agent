import asyncio

import pytest

from app.cache import AnswerCache, guard, namespace
from app.config import Settings
from app.reliability import CallBudget


@pytest.fixture
def anyio_backend(): return 'asyncio'


@pytest.mark.anyio
async def test_cache_guards_ttl_capacity_failure(tmp_path):
    s=Settings(_env_file=None,data_dir=tmp_path,cache_capacity=2)
    cache=AnswerCache(s)
    await cache.put('scope','2025年收入是多少',[1,0],{'run_id':'a'})
    assert (await cache.lookup('scope','2025年收入是多少'))[1]['status']=='exact'
    assert (await cache.lookup('scope','2025年收入是多少？',[1,0]))[1]['status']=='semantic'
    assert (await cache.lookup('scope','2024年收入是多少',[1,0]))[0] is None
    assert (await cache.lookup('scope','2025年收入不是多少',[1,0]))[0] is None
    await cache.put('scope','星河比北辰收入高吗',[1,0],{'run_id':'b'})
    assert (await cache.lookup('scope','北辰比星河收入高吗',[1,0]))[0] is None
    assert (await cache.lookup('other','2025年收入是多少',[1,0]))[0] is None
    for i in range(3): await cache.put(str(i),str(i),[1,0],{'run_id':str(i)})
    assert sum(map(len,cache.buckets.values()))<=2
    cache.buckets['2'][0]['expires']=0
    assert (await cache.lookup('2','2'))[0] is None
    assert namespace({'revision':1},{})!=namespace({'revision':2},{})
    await cache.invalidate(); assert not cache.buckets
    cache.s=s.model_copy(update={'app_mode':'local','redis_url':'https://redis.invalid','redis_token':'test-only'})
    async def broken(command): raise ConnectionError('fixture')
    cache.command=broken
    assert (await cache.lookup('scope','test'))[0] is None and cache.errors>0


@pytest.mark.anyio
async def test_retry_fallback_auth_budget_cancel():
    s=Settings(_env_file=None,retry_attempts=1)
    budget=CallBudget(s)
    class RateLimitError(Exception): pass
    calls=[]
    async def transient(model):
        calls.append(model)
        if model=='primary': raise RateLimitError()
        return 'ok'
    assert await budget.run(transient,'primary','backup')=='ok'
    assert calls==['primary','primary','backup']
    calls.clear()
    assert await budget.run(transient,'primary','backup')=='ok'
    assert calls==['backup']
    shared=budget.failures
    shared['primary']=0  # Explicitly expire cooldown; no half-open claim.
    recovered=CallBudget(s,shared)
    async def healthy(model): return model
    assert await recovered.run(healthy,'primary','backup')=='primary'
    b=CallBudget(s)
    async def auth(model): raise ValueError('auth')
    with pytest.raises(ValueError): await b.run(auth,'primary','backup')
    assert b.calls==1
    b=CallBudget(Settings(_env_file=None,max_model_calls=1))
    with pytest.raises(TimeoutError): await b.run(transient,'primary','backup')
    b=CallBudget(s)
    async def cancelled(model): raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError): await b.run(cancelled,'primary','backup')
    assert b.calls==1
