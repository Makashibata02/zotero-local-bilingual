"""Real portable PDF pipeline with local model fixtures and isolated task storage."""
import os
import json
from pathlib import Path
import asyncio
import time
import uuid
from http.server import ThreadingHTTPServer
import threading
ROOT=Path(__file__).resolve().parents[1]
VERSION=json.loads((ROOT/'plugin/addon/manifest.json').read_text('utf8'))['version']
DATA=ROOT/f'output/acceptance-{VERSION.replace(".","")}/queue-models';DATA.mkdir(parents=True,exist_ok=True)
os.environ['ZLB_DATA_DIR']=str(DATA);os.environ['ZLB_RUNTIME_ROOT']=str(ROOT/f'output/runtime-build/{VERSION}')
import importlib.util
module_spec=importlib.util.spec_from_file_location('local_mock',ROOT/'scripts/acceptance_mock_api.py')
local_mock=importlib.util.module_from_spec(module_spec);module_spec.loader.exec_module(local_mock)
Handler,Secrets,CALLS=local_mock.Handler,local_mock.Secrets,local_mock.CALLS
os.environ['ZLB_DATA_DIR']=str(DATA)
from zotero_local_bilingual.app import create_app
from zotero_local_bilingual.config import Settings
from zotero_local_bilingual.selftest import make_test_pdf
from zotero_local_bilingual.languages import LANGUAGES
import httpx
import pymupdf

async def main():
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    app=create_app(Settings(DATA),Secrets());source=DATA/'source.pdf';make_test_pdf(source)
    headers={'X-Local-Bilingual-Token':app.state.settings.get_or_create_service_token()}
    summary={'paid_api_calls':0,'tests':[]};started=time.monotonic()
    async with app.router.lifespan_context(app),httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',headers=headers) as c:
        (await c.post('/v1/profiles',json={'id':'mock','name':'Mock multilingual','base_url':f'http://127.0.0.1:{server.server_port}/v1','model':'local-fixture','qps':2})).raise_for_status()
        (await c.post('/v1/scheduler',json={'max_parallel':2})).raise_for_status()
        jobs=[]
        for i,target in enumerate(LANGUAGES):
            mode=['dual','mono','dual_and_mono'][i%3]
            response=await c.post('/v1/jobs',data={'profile_id':'mock','source_title':'Full PDF language test','pages':'1','client_request_id':uuid.uuid4().hex,
                'metadata_json':json.dumps({'citation_label':'Shannon 等 - 2026 - PDF translation test','attachment_id':str(i),'filename':'中文测试.pdf','batch_id':'mock-batch'}),
                'options_json':json.dumps({'target_language':target,'output_mode':mode,'ignore_cache':True,'max_output_tokens':1024,'max_requests':4,'max_total_tokens':12000})},files={'file':('中文测试.pdf',source.read_bytes(),'application/pdf')})
            response.raise_for_status();jobs.append(response.json())
        assert len(jobs)==7
        peak=0
        for _ in range(600):
            jobs=[(await c.get('/v1/jobs/'+j['id'])).json() for j in jobs]
            health=(await c.get('/v1/health')).json();peak=max(peak,len(health['active_jobs']))
            assert len(health['active_jobs'])<=2
            if all(j['status'] not in {'running','queued'} for j in jobs):break
            await asyncio.sleep(.5)
        for job in jobs:
            assert job['status']=='completed',job
            assert len(job['artifacts'])==(2 if job['output_mode']=='dual_and_mono' else 1)
            for a in job['artifacts']:
                response=await c.get(f"/v1/jobs/{job['id']}/artifacts/{a['name']}");response.raise_for_status()
                path=DATA/f"{job['target_language']}-{a['kind']}.pdf";path.write_bytes(response.content)
                with pymupdf.open(path) as pdf:
                    assert len(pdf)==1; text=pdf[0].get_text();assert 'Shannon' in text and 'References' in text
                    assert '\ufffd' not in text
                    if job['target_language'] not in {'zh-CN'}:
                        expected=local_mock.TARGET_PARAGRAPHS[LANGUAGES[job['target_language']][1]]
                        import unicodedata
                        normalize=lambda s: ''.join(unicodedata.normalize('NFKC',s).split()).replace('’',"'")
                        assert normalize(expected) in normalize(text),(job['target_language'],text)
                    (path.with_suffix('.txt')).write_text(text,'utf8')
                    pix=pdf[0].get_pixmap(matrix=pymupdf.Matrix(1,1));pix.save(path.with_suffix('.png'))
            summary['tests'].append({'language':job['target_language'],'mode':job['output_mode'],'metrics':job['metrics'],'artifacts':job['artifacts']})
        assert peak==2
        # Deployment test is bounded; after success the everyday endpoint does not create another PDF job.
        test=(await c.post('/v1/profiles/mock/selftest',json={'client_request_id':uuid.uuid4().hex})).json()
        while test['status'] in {'running','queued'}:
            await asyncio.sleep(.5);test=(await c.get('/v1/jobs/'+test['id'])).json()
        assert test['status']=='completed'
        viewer=DATA/'viewer-copy.pdf';viewer.write_bytes((await c.get(f"/v1/jobs/{test['id']}/artifacts/{test['artifact_name']}")).content)
        (await c.post(f"/v1/jobs/{test['id']}/cleanup-test",json={'viewer_copy_opened':True})).raise_for_status()
        audit=(await c.get('/v1/jobs/'+test['id'])).json();assert audit['metrics']['total_tokens']==test['metrics']['total_tokens'] and audit['cleanup_state']=='cleaned'
        count=len((await c.get('/v1/jobs')).json());before=len(CALLS)
        probe=await c.post('/v1/profiles/mock/test');probe.raise_for_status()
        assert len(CALLS)==before+1 and len((await c.get('/v1/jobs')).json())==count
        summary.update(peak_running=peak,first_test_metrics=test['metrics'],connection_probe=probe.json(),mock_calls=len(CALLS),mock_tokens=sum(j['metrics']['total_tokens'] for j in jobs)+test['metrics']['total_tokens'],seconds=round(time.monotonic()-started,3))
    server.shutdown();(DATA/'results.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps(summary,ensure_ascii=True),flush=True)

if __name__=='__main__':asyncio.run(main())
