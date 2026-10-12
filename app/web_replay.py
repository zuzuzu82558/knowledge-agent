"""Explicit immutable fixture/capture replay; unknown queries fail, never go online."""
import hashlib
import json

from .web import WebError, canonical_url


class SnapshotReplay:
    def __init__(self, case):
        self.case=case
        self.search_calls=0; self.read_calls=0
        for page in case.get('pages',[]):
            if (not page['id'].startswith('web:') or page.get('read_status')!='body_read' or
                    hashlib.sha256(page['text'].encode()).hexdigest()!=page['body_sha256']):
                raise ValueError('snapshot_replay_integrity')

    async def search(self, query):
        self.search_calls+=1
        for search in self.case.get('searches',[]):
            if ' '.join(search['query'].casefold().split())==' '.join(query.casefold().split()):
                return json.loads(json.dumps({'results':search['results'],'provider':'snapshot_replay','usage':None}))
        raise WebError('replay_query_not_recorded')

    async def read(self, row):
        self.read_calls+=1
        for page in self.case.get('pages',[]):
            if canonical_url(page['discovered_url'])==canonical_url(row['url']):
                return json.loads(json.dumps(page))
        raise WebError('replay_page_not_recorded')


class CaptureSearch:
    def __init__(self, adapter, case, meter):
        self.adapter,self.case,self.meter=adapter,case,meter

    async def search(self, query):
        if self.meter['calls']>=self.meter['limit']:raise WebError('experiment_search_call_limit')
        self.meter['calls']+=1
        result=await self.adapter.search(query)
        self.case['searches'].append({'query':query,**result})
        return result


class CaptureReader:
    def __init__(self, adapter, case):self.adapter,self.case=adapter,case
    async def read(self, row):
        result=await self.adapter.read(row)
        self.case['pages'].append(result)
        return result
