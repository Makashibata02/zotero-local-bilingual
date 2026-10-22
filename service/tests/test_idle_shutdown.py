import asyncio
import os
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi.testclient import TestClient
from zotero_local_bilingual.app import create_app
from zotero_local_bilingual import __version__
from zotero_local_bilingual.config import Settings

class Secrets:
    def get_api_key(self, _): return None

@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setenv("ZLB_MANAGED","1")
    settings=Settings(tmp_path)
    app=create_app(settings,Secrets())
    app.state.runner.enqueue=AsyncMock()
    c=TestClient(app,headers={"X-Local-Bilingual-Token":settings.get_or_create_service_token()})
    return c,app

def consent():
    return {"expected_version":__version__,"expected_pid":os.getpid()}

def add_job(app,status):
    app.state.database.upsert_profile({"id":"free","name":"free","provider":"bing","base_url":"","model":"","qps":1,"no_send_temperature":False,"reasoning":None})
    app.state.database.create_job({"id":"job","status":status,"source_title":"mock","source_sha256":"mock","source_path":"unused","profile_id":"free"})

def test_idle_shutdown_keeps_failed_history_and_rejects_new_mutations(env):
    c,app=env;add_job(app,"failed")
    previous=app.state.database.get_job("job")
    h=c.get("/v1/health").json()
    assert h["supports_idle_shutdown"] and h["process_id"]==os.getpid() and h["pending_jobs"]==0
    assert c.post("/v1/runtime/shutdown-if-idle",json=consent()).json()["accepted"]
    assert app.state.shutdown_requested
    assert c.post("/v1/scheduler",json={"max_parallel":2}).status_code==503
    assert c.post("/v1/profiles",json={"id":"new","name":"new","provider":"bing"}).status_code==503
    assert app.state.database.get_job("job")==previous
    assert c.get("/v1/health").json()["shutdown_requested"]

@pytest.mark.parametrize("status",["queued","running","cancelling"])
def test_nonidle_shutdown_is_rejected(env,status):
    c,app=env;add_job(app,status)
    assert c.post("/v1/runtime/shutdown-if-idle",json=consent()).status_code==409
    assert not app.state.shutdown_requested

def test_inflight_business_request_prevents_shutdown(env):
    c,app=env;app.state.business_requests=1
    assert c.post("/v1/runtime/shutdown-if-idle",json=consent()).status_code==409
    assert not app.state.shutdown_requested
    assert app.state.business_requests==1

def test_wrong_identity_or_missing_auth_does_not_stop_service(env):
    c,app=env
    wrong=consent();wrong["expected_pid"]+=1
    assert c.post("/v1/runtime/shutdown-if-idle",json=wrong).status_code==409
    wrong=consent();wrong["expected_version"]="other"
    assert c.post("/v1/runtime/shutdown-if-idle",json=wrong).status_code==409
    assert c.post("/v1/runtime/shutdown-if-idle",json=consent(),headers={"X-Local-Bilingual-Token":"invalid"}).status_code==401
    assert not app.state.shutdown_requested

def test_manual_service_never_accepts_automatic_shutdown(env,monkeypatch):
    c,app=env;monkeypatch.setenv("ZLB_MANAGED","0")
    assert not c.get("/v1/health").json()["supports_idle_shutdown"]
    assert c.post("/v1/runtime/shutdown-if-idle",json=consent()).status_code==409
    assert not app.state.shutdown_requested

@pytest.mark.asyncio
async def test_shutdown_during_connection_test_is_rejected(env,monkeypatch):
    c,app=env
    app.state.database.upsert_profile({"id":"free","name":"free","provider":"bing","base_url":"","model":"","qps":1,"no_send_temperature":False,"reasoning":None})
    import threading
    from zotero_local_bilingual.free_translate import WebProvider
    entered=threading.Event();release=threading.Event()
    def translate(*args):
        entered.set();release.wait(5);return "示例"
    monkeypatch.setattr(WebProvider,"translate",translate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://testserver",headers=c.headers) as client:
        testing=asyncio.create_task(client.post("/v1/profiles/free/test"))
        await asyncio.to_thread(entered.wait,5)
        try:
            assert (await client.post("/v1/runtime/shutdown-if-idle",json=consent())).status_code==409
            assert not app.state.shutdown_requested
        finally:
            release.set()
        assert (await testing).status_code==200
        assert (await client.post("/v1/runtime/shutdown-if-idle",json=consent())).status_code==200


def test_read_only_default_initialization_does_not_prolong_idle_wait(env):
    import time
    c,app=env
    c.post("/v1/profiles/default/default",json={"initialize_only":True}).raise_for_status()
    prior=time.monotonic()-100
    app.state.last_business=prior
    c.post("/v1/profiles/default/default",json={"initialize_only":True}).raise_for_status()
    assert app.state.last_business==prior
