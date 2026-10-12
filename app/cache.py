"""Bounded scope buckets. Redis REST is optional; failure is a cache miss."""
import hashlib
import json
import math
import re
import time
from collections import OrderedDict

from .retrieval import tokenize


def guard(question):
    # Conservative calibration baseline: identical lexical constraints required.
    # Different years, names, negation or comparison direction cannot be accepted.
    return tokenize(re.sub(r'[？?。!！,，\s]+','',question))


def cosine(a,b):
    if not a or not b or len(a)!=len(b): return -1
    denominator=math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))
    return sum(x*y for x,y in zip(a,b))/denominator if denominator else -1


class AnswerCache:
    def __init__(self, settings):
        self.s=settings
        self.buckets=OrderedDict()
        self.errors=0
        self.remote_keys=set()

    async def command(self, command):
        import httpx
        async with httpx.AsyncClient(timeout=3) as client:
            r=await client.post(self.s.redis_url,headers={'Authorization':'Bearer '+self.s.redis_token.get_secret_value()},json=command)
            r.raise_for_status(); body=r.json()
            if body.get('error'): raise ValueError('redis_command_failed')
            return body.get('result')

    async def read(self, namespace):
        now=time.time()
        try:
            if self.s.redis_url and self.s.app_mode!='offline':
                raw=await self.command(['GET','ka:'+namespace])
                values=json.loads(raw) if raw else []
            else:
                values=self.buckets.get(namespace,[])
            return [v for v in values if v['expires']>now][:self.s.cache_capacity]
        except Exception:
            self.errors+=1; return []

    async def lookup(self, namespace, question, vector=None):
        for item in await self.read(namespace):
            if item['question']==question:
                return item, {'status':'exact','similarity':None,'source_run':item['result']['run_id']}
        if vector is not None:
            best=None
            for item in await self.read(namespace):
                if guard(item['question'])!=guard(question): continue
                score=cosine(item['vector'],vector)
                if score>=self.s.semantic_threshold and (best is None or score>best[0]): best=(score,item)
            if best:
                return best[1], {'status':'semantic','similarity':best[0],'source_run':best[1]['result']['run_id']}
        return None, {'status':'miss'}

    async def put(self, namespace, question, vector, result):
        values=await self.read(namespace)
        values=[v for v in values if v['question']!=question]
        values.insert(0,{'question':question,'vector':vector,'result':result,'expires':time.time()+self.s.cache_ttl})
        values=values[:self.s.cache_capacity]
        try:
            if self.s.redis_url and self.s.app_mode!='offline':
                await self.command(['SET','ka:'+namespace,json.dumps(values,ensure_ascii=False),'EX',self.s.cache_ttl])
                self.remote_keys.add('ka:'+namespace)
            else:
                self.buckets[namespace]=values; self.buckets.move_to_end(namespace)
                # Bound global entries as well as entries in each scope.
                while sum(map(len,self.buckets.values()))>self.s.cache_capacity:
                    self.buckets.popitem(last=False)
        except Exception:
            self.errors+=1

    async def invalidate(self):
        self.buckets.clear()
        if self.remote_keys:
            try:
                await self.command(['DEL',*sorted(self.remote_keys)])
                self.remote_keys.clear()
            except Exception:
                self.errors+=1
        # Buckets from a previous process expire by TTL; revision makes them unreachable.


def namespace(snapshot, policy):
    return hashlib.sha256(json.dumps([snapshot,policy],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
