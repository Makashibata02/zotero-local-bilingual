import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
import pymupdf
from fastapi.testclient import TestClient
from zotero_local_bilingual.app import create_app
from zotero_local_bilingual.config import Settings
from zotero_local_bilingual.database import Database
from zotero_local_bilingual.jobs import JobRunner
from zotero_local_bilingual.languages import LANGUAGES, font_language
from zotero_local_bilingual.translation_policy import TranslationOptions, role_prompt

class Secrets:
    def get_api_key(self, _): return "LOCAL-MOCK-ONLY"
    def set_api_key(self, *_): pass

def make_pdf(path, pages=2):
    with pymupdf.open() as doc:
        for i in range(pages): doc.new_page().insert_text((40,40),f"Source page {i+1}")
        doc.save(path)

def database(tmp_path):
    db=Database(tmp_path/'state.sqlite3');db.initialize()
    for id,provider in [('api','openai'),('free','bing')]:
        db.upsert_profile({'id':id,'name':id,'provider':provider,'base_url':'http://127.0.0.1/v1','model':'mock' if provider=='openai' else '', 'qps':2,'reasoning':None,'no_send_temperature':False})
    return db

@pytest.mark.parametrize('target',LANGUAGES)
def test_language_prompt_and_font(target):
    policy=TranslationOptions(target_language=target)
    assert policy.max_total_tokens==1000000 and policy.max_requests==1000
    assert LANGUAGES[target][1] in role_prompt('',target)
    if target=='ko': assert font_language(target)=='ko-KR'

@pytest.mark.parametrize('mode,kinds',[('dual',['dual']),('mono',['mono']),('dual_and_mono',['dual','mono'])])
@pytest.mark.asyncio
async def test_outputs_translate_once_and_select_pages(tmp_path,monkeypatch,mode,kinds):
    db=database(tmp_path);folder=tmp_path/'job';folder.mkdir();source=folder/'source.pdf';make_pdf(source)
    mono=folder/'engine.pdf';make_pdf(mono)
    db.create_job({'id':'job','status':'queued','source_title':'Paper','source_sha256':'x','source_path':str(source),'profile_id':'api','pages':'2',
        'options':TranslationOptions(output_mode=mode,target_language='fr').model_dump(), 'metadata':{'citation_label':'Tachella 等 - 2025 - UNSURE'}})
    calls=[]
    class Process:
        returncode=0
        def __init__(self):
            self.stdout=asyncio.StreamReader();self.stdout.feed_data((json.dumps({'type':'result','mono_pdf_path':str(mono),'usage':{'total_tokens':10},'quality':{}})+'\n').encode());self.stdout.feed_eof()
        async def wait(self):return 0
    async def spawn(*args,**kwargs): calls.append(args);return Process()
    monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
    await JobRunner(db,Secrets())._run('job')
    row=db.get_job('job');assert row['status']=='completed',row['error']
    artifacts=json.loads(row['artifacts_json']);assert [a['kind'] for a in artifacts]==kinds and len(calls)==1
    for a in artifacts:
        assert f"_fr_{a['kind']}_mock.pdf" in a['name'] and 'Tachella' in a['name']
        with pymupdf.open(a['path']) as pdf:
            assert len(pdf)==1 and 'Source page 2' in pdf[0].get_text() and 'Source page 1' not in pdf[0].get_text()

@pytest.mark.parametrize('parallel',[1,2])
@pytest.mark.asyncio
async def test_scheduler_fifo_caps_cancel_and_dedup(tmp_path,parallel):
    db=database(tmp_path);db.set_parallel_limit(parallel)
    runner=JobRunner(db,Secrets());active=set();free=set();peak=[0,0];order=[]
    async def run(id):
        row=db.get_job(id);db.update_job(id,status='running');active.add(id)
        if row['profile_id']=='free':free.add(id)
        peak[0]=max(peak[0],len(active));peak[1]=max(peak[1],len(free));order.append(id)
        await asyncio.sleep(.1)
        active.remove(id);free.discard(id);db.update_job(id,status='completed')
    runner._run=run
    await runner.start()
    try:
        for i,profile in enumerate(['free','free','api','api']):
            id=str(i);db.create_job({'id':id,'status':'queued','source_title':id,'source_sha256':id,'source_path':'unused','profile_id':profile})
            await runner.enqueue(id);await runner.enqueue(id)
        await asyncio.wait_for(runner.queue.join(),3)
        assert peak[0]==parallel and peak[1]==1 and len(order)==4
        assert order[0]=='0' and (parallel==2 or order==['0','1','2','3'])
    finally:await runner.stop()

@pytest.fixture
def client(tmp_path):
    app=create_app(Settings(tmp_path),Secrets());app.state.runner.enqueue=AsyncMock()
    c=TestClient(app);c.headers['X-Local-Bilingual-Token']=app.state.settings.get_or_create_service_token()
    c.post('/v1/profiles',json={'id':'p','name':'model','base_url':'http://127.0.0.1/v1','model':'mock'})
    return c,app

def submit(c,tmp_path,**options):
    path=tmp_path/'input.pdf'
    if not path.exists():make_pdf(path)
    return c.post('/v1/jobs',data={'profile_id':'p','source_title':'title','options_json':json.dumps(options),
        'metadata_json':json.dumps({'filename':'中文.pdf','attachment_id':'1','library_id':'1'})},files={'file':('中文.pdf',path.read_bytes(),'application/pdf')})

def test_profile_lock_source_dedup_and_languages(client,tmp_path):
    c,app=client;a=submit(c,tmp_path).json();b=submit(c,tmp_path).json()
    assert a['id']==b['id'] and app.state.runner.enqueue.await_count==1
    assert a['metadata']['filename']=='中文.pdf' and a['queue_position']==1
    assert c.post('/v1/profiles',json={'id':'p','name':'replace','base_url':'http://127.0.0.1/v1','model':'other'}).status_code==409
    assert submit(c,tmp_path,target_language='fr').json()['id']!=a['id']

def test_history_preserves_formal_files_and_budget(client,tmp_path):
    c,app=client;id=submit(c,tmp_path).json()['id'];row=app.state.database.get_job(id)
    assert c.post(f'/v1/jobs/{id}/history',json={'hidden':True}).status_code==409
    app.state.database.update_job(id,status='completed')
    c.post(f'/v1/jobs/{id}/history',json={'hidden':True}).raise_for_status()
    assert not c.get('/v1/jobs').json() and Path(row['source_path']).is_file()
    assert c.get('/v1/jobs?include_hidden=true').json()[0]['budget']['max_total_tokens']==1000000
    c.post(f'/v1/jobs/{id}/history',json={'hidden':False}).raise_for_status()
    assert len(c.get('/v1/jobs').json())==1
    assert c.post(f'/v1/jobs/{id}/cleanup-test',json={'viewer_copy_opened':True}).status_code==409

def test_test_cleanup_keeps_numeric_audit_and_request_replay(client,tmp_path):
    c,app=client;request={'client_request_id':'a'*32}
    job=c.post('/v1/profiles/p/selftest',json=request).json();id=job['id'];row=app.state.database.get_job(id)
    folder=Path(row['source_path']).parent;working=folder/'engine-working';working.mkdir()
    (working/'api-usage.json').write_text(json.dumps({'total_tokens':123,'request_count':1,'usage_kind':'tokens'}))
    app.state.database.update_job(id,status='completed')
    assert c.post(f'/v1/jobs/{id}/cleanup-test',json={}).status_code==400
    result=c.post(f'/v1/jobs/{id}/cleanup-test',json={'viewer_copy_opened':True});assert result.json()['cleanup_state']=='cleaned'
    assert not folder.exists() and c.get(f'/v1/jobs/{id}').json()['metrics']['total_tokens']==123
    assert c.post('/v1/profiles/p/selftest',json=request).json()['id']==id
    assert app.state.runner.enqueue.await_count==1
    assert c.post(f'/v1/jobs/{id}/retry').status_code==409

def test_unknown_free_language_rejected_without_queue(client,tmp_path):
    c,app=client;c.post('/v1/profiles',json={'id':'free','name':'free','provider':'bing'})
    path=tmp_path/'s.pdf';make_pdf(path)
    response=c.post('/v1/jobs',data={'profile_id':'free','source_title':'x','options_json':json.dumps({'target_language':'xx'})},files={'file':('s.pdf',path.read_bytes(),'application/pdf')})
    assert response.status_code==400 and app.state.runner.enqueue.await_count==0

def test_cleanup_busy_preserves_files_and_can_retry(client,tmp_path,monkeypatch):
    c,app=client
    job=c.post('/v1/profiles/p/selftest',json={'client_request_id':'b'*32}).json();id=job['id']
    app.state.database.update_job(id,status='completed')
    import shutil
    original=shutil.rmtree
    monkeypatch.setattr(shutil,'rmtree',lambda *_: (_ for _ in ()).throw(PermissionError('busy')))
    assert c.post(f'/v1/jobs/{id}/cleanup-test',json={'viewer_copy_opened':True}).json()['cleanup_state']=='pending'
    assert Path(app.state.database.get_job(id)['source_path']).exists()
    monkeypatch.setattr(shutil,'rmtree',original)
    assert c.get(f'/v1/jobs/{id}').json()['cleanup_state']=='cleaned'

def test_shared_profile_rate_across_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from zotero_local_bilingual.shared_rate import acquire
    import time
    def request(_):
        return acquire(str(tmp_path/'rate.sqlite3'),'same-profile',10)
    with ThreadPoolExecutor(max_workers=3) as pool:times=sorted(pool.map(request,range(3)))
    assert all(b-a>=.08 for a,b in zip(times,times[1:]))

def test_030_upgrade_preserves_old_budget(tmp_path):
    db=database(tmp_path);db.create_job({'id':'old','status':'running','source_title':'old','source_sha256':'x','source_path':'x','profile_id':'api','options':{}})
    with db.connect() as conn:conn.execute("DELETE FROM metadata WHERE key='migration-030'")
    db.initialize();row=db.get_job('old')
    assert row['status']=='paused' and json.loads(row['options_json'])['max_total_tokens']==60000

def test_scheduler_invalid_limits_rejected(client):
    c,app=client
    for value in [0,3,True,'2']:assert c.post('/v1/scheduler',json={'max_parallel':value}).status_code==400
    assert c.post('/v1/scheduler',json={'max_parallel':2}).json()['max_parallel']==2

def test_free_author_protection_and_short_formula(tmp_path):
    from zotero_local_bilingual.free_translate import FreeTranslator
    from zotero_local_bilingual.free_policy import simple_formula
    class Provider:
        def translate(self,text):return text.replace('Claude Shannon','错误音译').replace('Robert Gallager','错误音译')
    t=FreeTranslator({'provider':'bing','engine_working_dir':str(tmp_path)},Provider(),interval=0)
    result=t.do_translate('Authors: Claude Shannon and Robert Gallager. An equation describes the model.')
    assert 'Claude Shannon' in result and 'Robert Gallager' in result
    assert 'Gallager. An equation' in result
    assert simple_formula('E = m c^2') and not simple_formula('An equation describes E = m c^2')

def test_changed_model_creates_independent_job_and_hide_keeps_elapsed(client,tmp_path):
    c,app=client;first=submit(c,tmp_path).json();id=first['id'];app.state.database.update_job(id,status='completed')
    before=c.get('/v1/jobs/'+id).json()['metrics']['elapsed_seconds']
    c.post('/v1/profiles',json={'id':'p','name':'second','base_url':'http://127.0.0.1/v1','model':'other'}).raise_for_status()
    second=submit(c,tmp_path).json();assert second['id']!=id
    c.post(f'/v1/jobs/{id}/history',json={'hidden':True}).raise_for_status()
    assert c.get('/v1/jobs/'+id).json()['metrics']['elapsed_seconds']==before

def test_worker_lock_prevents_overlap_and_releases_after_exit(tmp_path):
    from zotero_local_bilingual.worker_lock import worker_lock
    import subprocess
    import sys
    code="from pathlib import Path; import sys; from zotero_local_bilingual.worker_lock import worker_lock; lock=worker_lock(Path(sys.argv[1])); lock.__enter__(); print('locked',flush=True); sys.stdin.read(); lock.__exit__(None,None,None)"
    child=subprocess.Popen([sys.executable,'-c',code,str(tmp_path)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    try:
        assert child.stdout.readline().strip()=='locked'
        with pytest.raises(RuntimeError,match='已有工作进程'):
            with worker_lock(tmp_path):raise AssertionError('must not run')
    finally:
        child.communicate('',timeout=5)
    with worker_lock(tmp_path):pass
