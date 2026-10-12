import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from zotero_local_bilingual.app import create_app
from zotero_local_bilingual.config import Settings
from zotero_local_bilingual.translation_policy import TranslationOptions
from test_job_budget_recovery import translator, response, MESSAGES


@pytest.fixture
def prepared(tmp_path):
    class Secrets:
        def get_api_key(self, _): raise AssertionError("budget endpoint must not send/read a paid key")
    app = create_app(Settings(tmp_path), Secrets())
    db = app.state.database
    db.upsert_profile({"id":"p","name":"p","provider":"openai","base_url":"https://example.invalid/v1",
                       "model":"mock","qps":1,"no_send_temperature":False,"reasoning":None})
    source = tmp_path/'job/source.pdf';source.parent.mkdir();source.write_bytes(b'%PDF')
    db.create_job({"id":"a"*32,"status":"paused","source_title":"x","source_sha256":"x",
                   "source_path":str(source),"profile_id":"p","stage":"budget_limit",
                   "options":TranslationOptions(max_total_tokens=60000,max_requests=100).model_dump()})
    working=source.parent/'engine-working';working.mkdir()
    audit=working/'api-usage.json'
    audit.write_text(json.dumps({"usage_kind":"tokens","total_tokens":47306,"request_count":34,
        "budget_total_tokens":47306,"required_budget_tokens":65000,"unconfirmed_tokens":0}), 'utf8')
    enqueued=[]
    async def enqueue(job_id):enqueued.append(job_id)
    app.state.runner.enqueue=enqueue
    client=TestClient(app)
    headers={"X-Local-Bilingual-Token":app.state.settings.get_or_create_service_token()}
    payload={"confirmed":True,"client_request_id":"b"*32,"expected_max_total_tokens":60000,
        "expected_max_requests":100,"max_total_tokens":120000,"max_requests":200}
    return client,headers,payload,db,audit,enqueued


def test_explicit_extension_is_atomic_idempotent_and_preserves_audit(prepared):
    c,h,p,db,audit,queue=prepared;original=audit.read_bytes()
    result=c.post('/v1/jobs/'+'a'*32+'/extend-budget',headers=h,json=p)
    assert result.status_code==200,result.text
    assert result.json()['budget']['max_total_tokens']==120000
    assert result.json()['metrics']['total_tokens']==47306
    assert c.post('/v1/jobs/'+'a'*32+'/extend-budget',headers=h,json=p).status_code==200
    assert len(queue)==1 and audit.read_bytes()==original
    with db.connect() as con:assert con.execute('SELECT COUNT(*) FROM budget_approvals').fetchone()[0]==1
    different={**p,'client_request_id':'c'*32}
    assert c.post('/v1/jobs/'+'a'*32+'/extend-budget',headers=h,json=different).status_code==409


@pytest.mark.parametrize('change',[
    {'confirmed':False}, {'expected_max_total_tokens':61000}, {'max_total_tokens':60000},
    {'max_requests':1}, {'max_total_tokens':2000001},
])
def test_invalid_or_unconfirmed_extension_never_queues(prepared,change):
    c,h,p,db,audit,queue=prepared
    assert c.post('/v1/jobs/'+'a'*32+'/extend-budget',headers=h,json={**p,**change}).status_code>=400
    assert queue==[] and db.get_job('a'*32)['status']=='paused'


def test_retry_cannot_bypass_budget_confirmation_and_legacy_error_is_supported(prepared):
    c,h,p,db,audit,queue=prepared
    db.update_job('a'*32,status='failed',stage='failed',error='达到任务 token 或请求次数上限；已停止新请求。')
    assert c.get('/v1/jobs/'+'a'*32,headers=h).json()['budget']['can_extend']
    assert c.post('/v1/jobs/'+'a'*32+'/retry',headers=h).status_code==409
    assert queue==[]


def test_missing_audit_fails_closed_and_selftest_cannot_extend(prepared):
    c,h,p,db,audit,queue=prepared;audit.unlink()
    assert c.post('/v1/jobs/'+'a'*32+'/extend-budget',headers=h,json=p).status_code==409
    with db.connect() as con:
        options=TranslationOptions(is_selftest=True).model_dump()
        con.execute('UPDATE jobs SET options_json=? WHERE id=?',(json.dumps(options),'a'*32))
    assert not c.get('/v1/jobs/'+'a'*32,headers=h).json()['budget']['can_extend']
    assert queue==[]


def test_approval_id_cannot_be_reused_for_changed_limits(prepared):
    c,h,p,db,audit,queue=prepared
    assert c.post('/v1/jobs/'+'a'*32+'/extend-budget',headers=h,json=p).status_code==200
    assert c.post('/v1/jobs/'+'a'*32+'/extend-budget',headers=h,json={**p,'max_total_tokens':180000}).status_code==409
    assert len(queue)==1


@pytest.mark.parametrize('kind',['free','network'])
def test_free_and_unrelated_failures_cannot_extend_paid_budget(prepared,kind):
    c,h,p,db,audit,queue=prepared
    if kind=='free':
        with db.connect() as con:
            con.execute('UPDATE jobs SET profile_snapshot_json=? WHERE id=?',(json.dumps({'provider':'bing'}),'a'*32))
    else:
        db.update_job('a'*32,status='failed',stage='failed',error='网络断开')
    assert c.post('/v1/jobs/'+'a'*32+'/extend-budget',headers=h,json=p).status_code==409
    assert queue==[]


def test_extended_translator_retains_historical_usage_and_reports_needed_reservation(tmp_path):
    calls=[]
    def handler(request):calls.append(request);return response(1000,200)
    translator(tmp_path,handler,max_total_tokens=5000)._request(MESSAGES)
    stopped=translator(tmp_path,handler,max_total_tokens=5000)
    with pytest.raises(RuntimeError,match='上限'):stopped._request(MESSAGES)
    audit=json.loads(stopped.audit_path.read_text('utf8'))
    assert audit['failure_reason']=='budget_limit' and audit['required_budget_tokens']>5000
    resumed=translator(tmp_path,handler,max_total_tokens=10000)
    assert resumed.snapshot()['total_tokens']==1200
    resumed._request(MESSAGES)
    assert resumed.snapshot()['total_tokens']==2400 and len(calls)==2
