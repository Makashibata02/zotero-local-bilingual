"""Exercise bundled preflight, cooperative handoff and restart with isolated data."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,required=True);args=parser.parse_args()
    runtime=args.runtime.resolve();version=json.loads((runtime/'runtime.json').read_text('utf8'))['version']
    out=ROOT/'output'/f'acceptance-{version.replace(".","")}';data=out/'cli-data'
    environment={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','ZLB_DATA_DIR':str(data)};environment.pop('PYTHONPATH',None)
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    common=[str(runtime/('python/python.exe' if os.name=='nt' else 'python/bin/python3.12')),'-B','-X','utf8','-m','zotero_local_bilingual.cli','--runtime-root',str(runtime),'--data-dir',str(data)]
    preflight=subprocess.run(common+['--preflight'],env=environment,creationflags=flags,capture_output=True,text=True,encoding='utf8',check=True)
    resources=json.loads(preflight.stdout.strip().splitlines()[-1]);assert resources['version']==version and resources['resources']==182
    processes=[]
    def launch():
        child=subprocess.Popen(common+['--managed','--idle-seconds','5'],env=environment,creationflags=flags,stdout=subprocess.PIPE,stderr=subprocess.PIPE);processes.append(child)
        descriptor=data/'managed-service.json'
        for _ in range(250):
            if descriptor.exists():
                endpoint=json.loads(descriptor.read_text('utf8'))
                if endpoint['pid']==child.pid:return child,endpoint
            if child.poll() is not None:raise RuntimeError('Isolated runtime failed before readiness')
            time.sleep(.1)
        raise RuntimeError('Isolated runtime startup timed out')
    def request(endpoint,path,body=None):
        headers={'X-Local-Bilingual-Token':(data/'service-token.txt').read_text('utf8').strip()}
        if body is not None:headers['Content-Type']='application/json'
        r=urllib.request.Request(endpoint['url']+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        with urllib.request.urlopen(r,timeout=5) as response:return json.load(response)
    try:
        first,endpoint=launch();health=request(endpoint,'/v1/health')
        assert health['version']==version and health['supports_idle_shutdown'] and health['process_id']==first.pid
        result=request(endpoint,'/v1/runtime/shutdown-if-idle',{'expected_version':version,'expected_pid':first.pid});assert result['accepted']
        try:
            request(endpoint,'/v1/profiles',{'id':'must-not-save','name':'mock','provider':'bing'})
            raise AssertionError('Shutdown accepted a new mutation')
        except urllib.error.HTTPError as error:assert error.code==503
        except urllib.error.URLError:pass  # Listener already closed after acceptance.
        assert first.wait(timeout=15)==0
        assert not (data/'managed-service.json').exists()
        second,endpoint=launch();health=request(endpoint,'/v1/health')
        assert health['process_id']==second.pid and health['version']==version and not health['shutdown_requested']
        assert request(endpoint,'/v1/profiles')==[]
        assert second.wait(timeout=15)==0
        assert not (data/'managed-service.json').exists()
        report={'ok':True,'version':version,'resources':182,'cooperative_exit':True,'new_mutations_rejected':True,'restart':True,'natural_idle_exit':True,'translation_requests':0}
        (out/'handoff-results.json').write_text(json.dumps(report,indent=2),'utf8');print(json.dumps(report))
    finally:
        for child in processes:
            if child.poll() is None:child.terminate()
            child.communicate(timeout=10)

if __name__=='__main__':main()
