"""Public long-document ingest/retrieval; no LLM quality claim."""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.service import Service


async def main():
    Path('.tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='public-smoke-',dir='.tmp') as directory:
        s=Settings(_env_file=None,data_dir=Path(directory),max_chunks=3000)
        service=Service(s)
        try:
            p=Path('examples/rfc9110.txt')
            rec=await service.upload(p.name,p.suffix,p.read_bytes())
            await asyncio.gather(*service.tasks)
            job=service.store.one('SELECT * FROM jobs WHERE id=?',(rec['job_id'],))
            assert job['status']=='completed',job
            rows=[]
            for question,anchor in [('Content-Length 204','Content-Length'),('safe idempotent methods GET HEAD OPTIONS TRACE','GET'),('Retry-After HTTP-date delay-seconds','Retry-After')]:
                result=await service.query(question,[rec['document_id']],bypass_cache=True,route='qa')
                assert result['status']=='offline_extracts'
                assert any(anchor in e['text'] for e in result['evidence'])
                for e in result['evidence']:
                    original=service.evidence(e['id'])
                    assert e['text'] in original['text']
                    assert all('line_start' in location and 'line_end' in location for location in e['locations'])
                rows.append({'question':question,'status':result['status'],'evidence':result['evidence'],'snapshot':result['snapshot']})
            report={'source':'RFC 9110','mode':'offline','status':'passed','max_chunks':3000,
                'note':'2168 chunks exceeds default limit 2000; explicit bounded override used. No model quality verified.',
                'queries':rows}
            Path('artifacts/public-document-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({'status':'passed','queries':len(rows)},ensure_ascii=False))
        finally: await service.close()


if __name__=='__main__': asyncio.run(main())
