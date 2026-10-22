import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pymupdf
import pytest
from fastapi.testclient import TestClient
from starlette.datastructures import UploadFile
from zotero_local_bilingual.app import create_app
from zotero_local_bilingual.config import Settings


class Secrets:
    def __init__(self):
        self.values = {}
        self.fail_delete = False
        self.deletions = []

    def get_api_key(self, id):
        return self.values.get(id)

    def set_api_key(self, id, key):
        self.values[id] = key

    def delete_api_key(self, id):
        if self.fail_delete:
            raise PermissionError("mock credential access denied")
        self.deletions.append(id)
        self.values.pop(id, None)


@pytest.fixture
def env(tmp_path):
    secrets = Secrets()
    settings = Settings(tmp_path)
    app = create_app(settings, secrets)
    app.state.runner.enqueue = AsyncMock()
    client = TestClient(app, headers={"X-Local-Bilingual-Token": settings.get_or_create_service_token()})
    return client, app, secrets


def save(client, id, name=None, key=True, provider="openai"):
    data = {"id":id, "name":name or id, "provider":provider}
    if provider == "openai":
        data.update(base_url="https://example.invalid/v1", model="mock-model")
        if key:
            data["api_key"] = "LOCAL-MOCK-ONLY"
    response = client.post("/v1/profiles", json=data)
    response.raise_for_status()
    return response.json()


def add_job(app, id="historical", status="completed", stage="done"):
    db = app.state.database
    db.create_job({"id":id, "status":status, "source_title":"Example paper", "source_sha256":"mock", "source_path":"unused.pdf", "profile_id":"p", "stage":stage, "options":{"max_total_tokens":12345}})
    db.update_job(id, metrics_json=json.dumps({"request_count":3,"total_tokens":789}))


def test_default_selection_does_not_follow_save_or_list(env):
    c, app, _ = env
    save(c,"p",name="Z model")
    save(c,"q",name="A model")
    assert app.state.database.default_profile_id() == "p"
    c.get("/v1/profiles").raise_for_status()
    assert c.post("/v1/profiles/q/default").json()["default_profile_id"] == "q"
    save(c,"r")
    assert app.state.database.default_profile_id() == "q"
    profiles = c.get("/v1/profiles").json()
    assert [p["id"] for p in profiles if p["is_default"]] == ["q"]
    assert all("api_key" not in p for p in profiles)
    app.state.runner.enqueue.assert_not_called()


def test_default_migration_prefers_previous_choice_once(env):
    c, app, _ = env
    save(c,"p");save(c,"q",name="A model")
    with app.state.database.connect() as db:
        db.execute("DELETE FROM metadata WHERE key='default_api_profile'")
    assert c.post("/v1/profiles/q/default",json={"initialize_only":True}).json()["default_profile_id"] == "q"
    assert c.post("/v1/profiles/p/default",json={"initialize_only":True}).json()["default_profile_id"] == "q"


def test_default_migration_falls_back_and_invalid_choices_rejected(env):
    c, app, _ = env
    save(c,"p",name="Z model");save(c,"q",name="A model")
    save(c,"no-key",key=False);save(c,"free",provider="bing")
    with app.state.database.connect() as db:
        db.execute("DELETE FROM metadata WHERE key='default_api_profile'")
    assert c.post("/v1/profiles/missing/default",json={"initialize_only":True}).json()["default_profile_id"] == "q"
    assert c.post("/v1/profiles/free/default").status_code == 400
    assert c.post("/v1/profiles/no-key/default").status_code == 400


def test_delete_preserves_history_and_cannot_resurrect(env):
    c, app, secrets = env
    save(c,"p");save(c,"q",name="A model");add_job(app,status="failed")
    before = app.state.database.get_job("historical")
    response = c.delete("/v1/profiles/p")
    assert response.status_code == 200
    assert response.json()["default_profile_id"] == "q"
    assert "p" not in secrets.values
    assert app.state.database.get_job("historical") == before
    assert app.state.database.get_profile("p") is None
    assert app.state.database.get_profile("p",include_deleted=True)["deleted_at"]
    assert c.get("/v1/jobs/historical").json()["service_name"] == "p · mock-model"
    assert all(p["id"] != "p" for p in c.get("/v1/profiles").json())
    assert c.delete("/v1/profiles/p").status_code == 200
    assert secrets.deletions == ["p"]
    assert c.post("/v1/profiles",json={"id":"p","name":"old request","base_url":"https://example.invalid/v1","model":"mock"}).status_code == 409
    assert c.post("/v1/profiles/p/test").status_code == 404
    assert c.post("/v1/profiles/p/selftest",json={"client_request_id":"a"*32}).status_code == 404
    assert c.post("/v1/jobs/historical/retry").status_code == 404
    app.state.runner.enqueue.assert_not_called()


@pytest.mark.parametrize("status,stage",[("queued","queued"),("running","translate"),("cancelling","cancelling"),("paused","paused_after_upgrade"),("failed","budget_limit")])
def test_delete_in_use_is_rejected(env,status,stage):
    c, app, secrets = env
    save(c,"p");add_job(app,status=status,stage=stage)
    profile = c.get("/v1/profiles").json()[0]
    assert not profile["can_delete"] and profile["delete_block_reason"]
    assert c.delete("/v1/profiles/p").status_code == 409
    assert secrets.get_api_key("p")
    assert not secrets.deletions


def test_failed_credentials_leave_configuration_and_default(env):
    c, app, secrets = env
    save(c,"p");secrets.fail_delete=True
    assert c.delete("/v1/profiles/p").status_code == 503
    assert app.state.database.get_profile("p")
    assert app.state.database.default_profile_id() == "p"
    assert secrets.get_api_key("p")


def test_delete_last_api_then_first_new_api_becomes_default(env):
    c, app, _ = env
    save(c,"p")
    assert c.delete("/v1/profiles/p").json()["default_profile_id"] == ""
    save(c,"free",provider="bing")
    assert app.state.database.default_profile_id() == ""
    save(c,"new")
    assert app.state.database.default_profile_id() == "new"


@pytest.mark.asyncio
async def test_submit_and_delete_are_serialized(env,monkeypatch):
    c, app, _ = env
    save(c,"p")
    entered,release = asyncio.Event(),asyncio.Event()
    original = UploadFile.read
    async def delayed(self,size=-1):
        entered.set();await release.wait();return await original(self,size)
    monkeypatch.setattr(UploadFile,"read",delayed)
    with pymupdf.open() as doc:
        doc.new_page();data=doc.tobytes()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://testserver",headers=c.headers) as client:
        submission=asyncio.create_task(client.post("/v1/jobs",files={"file":("mock.pdf",data,"application/pdf")},data={"profile_id":"p","source_title":"mock"}))
        await entered.wait()
        deletion=asyncio.create_task(client.delete("/v1/profiles/p"))
        await asyncio.sleep(0.02)
        assert not deletion.done()
        release.set()
        assert (await submission).status_code == 200
        assert (await deletion).status_code == 409


@pytest.mark.asyncio
async def test_connection_test_blocks_deletion_without_blocking_other_profiles(env,respx_mock):
    c, app, _ = env
    save(c,"p");save(c,"q")
    entered,release=asyncio.Event(),asyncio.Event()
    async def response(request):
        entered.set();await release.wait()
        return httpx.Response(200,json={"choices":[{"finish_reason":"stop","message":{"content":"信号空间"}}],"usage":{"total_tokens":21}})
    respx_mock.post("https://example.invalid/v1/chat/completions").mock(side_effect=response)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://testserver",headers=c.headers) as client:
        testing=asyncio.create_task(client.post("/v1/profiles/p/test"))
        await entered.wait()
        assert (await client.delete("/v1/profiles/p")).status_code == 409
        assert (await client.delete("/v1/profiles/q")).status_code == 200
        release.set()
        assert (await testing).status_code == 200
        assert (await client.delete("/v1/profiles/p")).status_code == 200


def test_cancel_budget_block_releases_profile_for_deletion(env):
    c, app, _ = env
    save(c,"p");add_job(app,status="failed",stage="budget_limit")
    assert c.delete("/v1/profiles/p").status_code == 409
    assert c.post("/v1/jobs/historical/cancel").status_code == 200
    assert c.delete("/v1/profiles/p").status_code == 200


def test_deleted_profile_cannot_submit_a_new_job(env):
    c, app, _ = env
    save(c,"p");c.delete("/v1/profiles/p").raise_for_status()
    with pymupdf.open() as doc:
        doc.new_page();payload=doc.tobytes()
    assert c.post("/v1/jobs",files={"file":("mock.pdf",payload,"application/pdf")},data={"profile_id":"p","source_title":"mock"}).status_code == 404
    assert not app.state.database.list_jobs()
    app.state.runner.enqueue.assert_not_called()


def test_importing_factory_does_not_create_application_data(tmp_path):
    import os
    import subprocess
    import sys
    target=tmp_path/"not-created"
    environment={**os.environ,"ZLB_DATA_DIR":str(target)}
    subprocess.run([sys.executable,"-B","-c","from zotero_local_bilingual.app import create_app"],env=environment,check=True)
    assert not target.exists()
