"""Own isolated offline server and Edge profile/ports; never use business service."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def main():
    edge=Path(os.environ.get('EDGE_BINARY','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'))
    if not edge.is_file(): raise ValueError('configure_EDGE_BINARY')
    output=ROOT/'artifacts/rag-upgrade-browser';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='rag-browser-',dir=ROOT/'.tmp') as directory:
        http_port,debug_port=port(),port()
        env={**os.environ,'APP_MODE':'offline','DATA_DIR':str(Path(directory)/'data'),'PYTHONUTF8':'1'}
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        server=browser=None
        try:
            with (output/'server.log').open('w',encoding='utf-8') as log:
                server=subprocess.Popen([sys.executable,'-m','uvicorn','app.main:create_app','--factory','--host','127.0.0.1',
                    '--port',str(http_port)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=flags)
                base='http://127.0.0.1:'+str(http_port)
                with httpx.Client(timeout=2,trust_env=False) as client:
                    for _ in range(100):
                        try:
                            client.get(base+'/api/health').raise_for_status();break
                        except httpx.HTTPError:time.sleep(.1)
                    else:raise RuntimeError('browser_server_start_timeout')
                browser=subprocess.Popen([str(edge),'--headless=new','--disable-gpu','--disable-background-networking','--no-first-run',
                    '--no-default-browser-check','--remote-debugging-port='+str(debug_port),'--user-data-dir='+str(Path(directory)/'edge'),
                    'about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
                subprocess.run(['node',str(ROOT/'scripts/browser_smoke.cjs'),base,str(debug_port),str(output)],check=True,timeout=55,cwd=ROOT)
        finally:
            for process in (browser,server):
                if process and process.poll() is None:
                    if os.name=='nt':
                        # Windows venv launchers own a child Python process.
                        subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
                                       stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=flags)
                    else:process.terminate()
                    try:process.wait(timeout=5)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=5)
            time.sleep(.2)


if __name__=='__main__':main()
