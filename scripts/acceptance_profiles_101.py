"""Start the bundled CLI with isolated data; never call a translation provider."""
from pathlib import Path
import argparse
import json
import os
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    data = ROOT / 'output/acceptance-101/cli-data'
    python = runtime / ('python/python.exe' if os.name == 'nt' else 'python/bin/python3.12')
    environment = {**os.environ, 'PYTHONDONTWRITEBYTECODE':'1', 'ZLB_DATA_DIR':str(data)}
    environment.pop('PYTHONPATH', None)
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    common = [str(python), '-B', '-X', 'utf8', '-m', 'zotero_local_bilingual.cli', '--runtime-root', str(runtime), '--data-dir', str(data)]
    preflight = subprocess.run(common + ['--preflight'], env=environment, creationflags=flags, capture_output=True, text=True, encoding='utf8', check=True)
    resources = json.loads(preflight.stdout.strip().splitlines()[-1])
    assert resources['version'] == '1.0.1' and resources['resources'] == 182
    process = subprocess.Popen(common + ['--managed', '--idle-seconds', '5'], env=environment, creationflags=flags, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        descriptor = data / 'managed-service.json'
        for _ in range(200):
            if descriptor.is_file():
                endpoint = json.loads(descriptor.read_text('utf8'))
                if endpoint['pid'] == process.pid:
                    break
            if process.poll() is not None:
                raise RuntimeError('Isolated service exited before readiness')
            time.sleep(.1)
        else:
            raise RuntimeError('Isolated service startup timed out')
        with urllib.request.urlopen(endpoint['url'] + '/v1/health') as response:
            health = json.load(response)
        assert health['version'] == '1.0.1' and health['managed'] and not health['active_jobs']
        headers = {'X-Local-Bilingual-Token': (data/'service-token.txt').read_text('utf8').strip()}
        with urllib.request.urlopen(urllib.request.Request(endpoint['url']+'/v1/profiles',headers=headers)) as response:
            assert json.load(response) == []
        request = urllib.request.Request(endpoint['url']+'/v1/profiles/default/default',data=b'{"initialize_only":true}',headers={**headers,'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(request) as response:
            assert json.load(response) == {'default_profile_id':''}
        assert process.wait(timeout=15) == 0
        assert not descriptor.exists()
        report = {'ok':True,'version':'1.0.1','resources_verified':182,'bundled_cli_started':True,'default_migration':True,'idle_exit':True,'translation_requests':0}
        (ROOT/'output/acceptance-101/cli-results.json').write_text(json.dumps(report,indent=2),'utf8')
        print(json.dumps(report))
    finally:
        if process.poll() is None:
            process.terminate()
        process.communicate(timeout=10)

if __name__=='__main__':
    main()
