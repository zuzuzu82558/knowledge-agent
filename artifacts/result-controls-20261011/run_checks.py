import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2];OUT=Path(__file__).resolve().parent
TEMP=ROOT/'.tmp/result-controls-20261011';TEMP.mkdir(exist_ok=True,parents=True)
env=os.environ.copy()
for name in ('MODEL_API_KEY LLM_API_KEY EMBEDDING_API_KEY OPENAI_API_KEY QDRANT_API_KEY NEO4J_PASSWORD REDIS_TOKEN LANGFUSE_SECRET_KEY LANGFUSE_PUBLIC_KEY NEO4J_URI REDIS_URL QDRANT_URL MODEL_API_BASE LLM_API_BASE EMBEDDING_API_BASE').split():env[name]=''
env.update(APP_MODE='offline',MAX_UPLOAD_MB='20',DATA_DIR=str(TEMP/'server-data-final'),PYTHONUTF8='1',TEMP=str(TEMP),TMP=str(TEMP),RAGAS_DO_NOT_TRACK='true',LITELLM_LOCAL_MODEL_COST_MAP='True')
if __name__=='__main__':
    action=sys.argv[1]
    if action=='tests':
        name=sys.argv[2] if len(sys.argv)>2 else 'first'
        with (OUT/('pytest-'+name+'.txt')).open('w',encoding='utf-8') as log:
            r=subprocess.run([sys.executable,'-m','pytest','tests','eval/tests','--basetemp='+str(TEMP/('pytest-'+name)),'-p','no:cacheprovider'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240)
        print((OUT/('pytest-'+name+'.txt')).read_text(encoding='utf-8'));sys.exit(r.returncode)
    elif action=='start':
        with (OUT/'server.log').open('a',encoding='utf-8') as log:
            p=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:create_app','--factory','--host','127.0.0.1','--port','18773'],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
        (OUT/'server-process.json').write_text(json.dumps({'pid':p.pid,'port':18773,'data':env['DATA_DIR']}));print('PID',p.pid)
    elif action=='evaluate':
        with (OUT/'evaluate-log.txt').open('w',encoding='utf-8') as log:
            r=subprocess.run([sys.executable,'scripts/evaluate.py','--summary','--report',str(OUT/'evaluation.json')],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180)
        print('evaluation exit',r.returncode);sys.exit(r.returncode)






