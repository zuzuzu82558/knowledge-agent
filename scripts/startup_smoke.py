"""Real process + localhost HTTP smoke, isolated data; offline only."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT=Path(__file__).resolve().parents[1]


def main():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    (ROOT/'.tmp').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='startup-',dir=ROOT/'.tmp') as directory:
        env=os.environ.copy();env.update(APP_MODE='offline',DATA_DIR=directory,PYTHONUTF8='1',
            MODEL_API_KEY='',LLM_API_KEY='',EMBEDDING_API_KEY='',OPENAI_API_KEY='',QDRANT_API_KEY='',
            LANGFUSE_PUBLIC_KEY='',LANGFUSE_SECRET_KEY='',NEO4J_URI='',NEO4J_PASSWORD='',REDIS_URL='',REDIS_TOKEN='')
        log=Path(directory)/'server.log'
        with log.open('w',encoding='utf-8') as output:
            process=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:create_app','--factory','--host','127.0.0.1','--port',str(port)],
                cwd=ROOT,env=env,stdout=output,stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            report={'mode':'offline','real_server_process':True,'checks':[]}
            try:
                with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=5,trust_env=False) as client:
                    for _ in range(100):
                        try:
                            health=client.get('/api/health');health.raise_for_status();break
                        except httpx.HTTPError:
                            if process.poll() is not None: raise RuntimeError('server_exited')
                            time.sleep(.1)
                    else: raise RuntimeError('startup_timeout')
                    assert health.json()['mode']=='offline'
                    report['checks'].append('health')
                    page=client.get('/');page.raise_for_status();assert '全文洞察' in page.text
                    report['checks'].append('ui')
                    sample=ROOT/'examples/supply-chain.md'
                    response=client.post('/api/documents',files={'file':(sample.name,sample.read_bytes())});response.raise_for_status()
                    doc=response.json()
                    for _ in range(300):
                        job=client.get('/api/jobs/'+doc['job_id']).json()
                        if job['status'] in ('completed','failed'): break
                        time.sleep(.1)
                    assert job['status']=='completed',job
                    report['checks'].append('ingest')
                    body={'question':'星河2025年收入','document_ids':[doc['document_id']],'route':'qa'}
                    result=client.post('/api/query',json=body).json()
                    assert result['status']=='offline_extracts' and result['evidence']
                    for e in result['evidence']:
                        assert client.get('/api/evidence/'+e['id']).status_code==200
                    report['checks'].extend(['query','citations'])
                    cached=client.post('/api/query',json=body).json()
                    assert cached['cache']['status']=='exact' and cached['run_id']!=result['run_id']
                    report['checks'].append('cache')
                    summary=client.post('/api/query',json={**body,'route':'summary'}).json()
                    assert summary['coverage']['total_sections']>8 and summary['status']=='offline_extracts'
                    report['checks'].append('full_document_coverage')
                    assert client.delete('/api/documents/'+doc['document_id']).json()['status']=='deleted'
                    assert client.get('/api/runs/'+result['run_id']).status_code in (404,410)
                    report['checks'].append('delete')
                    report['status']='passed'
            finally:
                if os.name=='nt' and process.poll() is None:
                    # Stop the test-owned launcher and its child Python process.
                    subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
                elif process.poll() is None: process.terminate()
                try: process.wait(timeout=10)
                except subprocess.TimeoutExpired: process.kill();process.wait(timeout=5)
                (ROOT/'artifacts').mkdir(exist_ok=True)
                (ROOT/'artifacts/startup-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__': main()
