import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from zotero_local_bilingual.app import create_app
from zotero_local_bilingual.config import Settings
from zotero_local_bilingual.database import Database
from zotero_local_bilingual.free_translate import FreeTranslator, split_text, MARKER, WebProvider
from zotero_local_bilingual.models import ProfileInput


class Provider:
    def __init__(self, damage=False, fail=False):
        self.inputs = []; self.damage = damage; self.fail = fail
    def translate(self, text):
        self.inputs.append(text)
        if self.fail: raise RuntimeError("rate limited")
        return text.replace("{v1}", "") if self.damage else "译" + text


def spec(tmp_path, **kwargs):
    return {"provider":"bing", "engine_working_dir":str(tmp_path), **kwargs}


def test_split_retains_every_character_and_markers():
    source = ("A long sentence. " * 100)[:790] + "{v123}" + " rest. " * 500
    parts = split_text(source)
    assert "".join(parts) == source
    assert max(map(len, parts)) <= 800
    assert sum(len(MARKER.findall(p)) for p in parts) == 1


def test_real_long_translation_is_complete(tmp_path):
    p = Provider(); translator = FreeTranslator(spec(tmp_path,ignore_cache=True), p, interval=0)
    source = "Some text. " * 180
    out = translator.do_translate(source)
    assert "".join(p.inputs) == source and len(p.inputs) >= 3
    assert out.count("Some text.") == 180
    assert translator.snapshot()["characters"] == len(source)
    assert translator.snapshot()["usage_kind"] == "characters"


def test_placeholder_single_fallback(tmp_path):
    p = Provider(damage=True); t = FreeTranslator(spec(tmp_path), p, interval=0)
    out = t.do_translate("Formula {v1} is useful.")
    assert MARKER.findall(out) == ["{v1}"]
    assert len(p.inputs) == 3  # Original plus two prose pieces, once.


def test_limit_and_resume_do_not_reset_usage(tmp_path):
    p = Provider(); t = FreeTranslator(spec(tmp_path,free_max_requests=1), p, interval=0)
    t.do_translate("First")
    restored = FreeTranslator(spec(tmp_path,free_max_requests=1), p, interval=0)
    assert restored.do_translate("First")  # Per-job cache avoids repeat billing/requests.
    with pytest.raises(RuntimeError, match="累计"):
        restored.do_translate("Second")
    assert len(p.inputs) == 1


def test_failed_request_is_reserved_and_stops_other_calls(tmp_path):
    p=Provider(fail=True);t=FreeTranslator(spec(tmp_path),p,interval=0)
    with pytest.raises(RuntimeError):t.do_translate("First")
    with pytest.raises(RuntimeError):t.do_translate("Second")
    assert len(p.inputs)==1 and t.snapshot()["characters"] == 5


def test_malformed_audit_fails_closed(tmp_path):
    (tmp_path/"api-usage.json").write_text('{"usage_kind":"tokens"}')
    with pytest.raises(RuntimeError): FreeTranslator(spec(tmp_path),Provider(),interval=0)


def test_free_profile_does_not_require_key_or_endpoint():
    assert ProfileInput(id="free",name="Free",provider="bing").qps == 1
    with pytest.raises(ValueError):ProfileInput(id="llm",name="API")


def test_upgrade_pauses_existing_jobs_once(tmp_path):
    db=Database(tmp_path/"state.sqlite3");db.initialize()
    db.upsert_profile({"id":"p","name":"p","base_url":"https://example.com","model":"m","qps":2,"no_send_temperature":False,"reasoning":None})
    db.create_job({"id":"a","status":"running","source_title":"x","source_sha256":"x","source_path":"x","profile_id":"p"})
    with db.connect() as con:con.execute("DELETE FROM metadata")
    db.initialize()
    assert db.get_job("a")["status"] == "paused"
    db.update_job("a",status="queued");db.initialize()
    assert db.get_job("a")["status"] == "queued"
    assert db.get_profile("p")["provider"]=="openai"


def test_fulltest_free_job_is_idempotent_and_bounded(tmp_path, monkeypatch):
    # Test job/idempotency behavior independently of live provider acceptance files.
    import zotero_local_bilingual.languages as languages
    monkeypatch.setattr(languages,"capabilities",lambda:{"providers":{"tencent":{"zh-CN":{"supported":True}}}})
    class Secrets:
        def get_api_key(self,_):return None
        def set_api_key(self,*_):raise AssertionError("free key must not be saved")
    app=create_app(Settings(tmp_path),Secrets())
    async def enqueue(_):pass
    monkeypatch.setattr(app.state.runner,"enqueue",enqueue)
    with TestClient(app) as client:
        h={"X-Local-Bilingual-Token":app.state.settings.get_or_create_service_token()}
        assert client.post('/v1/profiles',headers=h,json={"id":"free","name":"free","provider":"tencent"}).status_code==200
        payload={"client_request_id":"f"*32}
        first=client.post('/v1/profiles/free/selftest',headers=h,json=payload)
        assert first.status_code==200
        second=client.post('/v1/profiles/free/selftest',headers=h,json=payload)
        assert second.json()["id"]==first.json()["id"]
        rows=client.get('/v1/jobs',headers=h).json()
        assert len(rows)==1 and "不导入" in rows[0]["source_title"]
        options=json.loads(app.state.database.get_job(first.json()["id"])["options_json"])
        assert options["max_requests"]==4 and options["max_total_tokens"]==12000 and options["max_output_tokens"]==1024


@pytest.mark.asyncio
async def test_switching_profile_cannot_change_queued_free_job_to_paid(tmp_path):
    from zotero_local_bilingual.jobs import JobRunner
    db=Database(tmp_path/'state.sqlite3');db.initialize()
    profile={"id":"p","name":"p","provider":"bing","base_url":"","model":"","qps":1,"no_send_temperature":False,"reasoning":None}
    db.upsert_profile(profile)
    db.create_job({"id":"a","status":"queued","source_title":"x","source_sha256":"x","source_path":"x","profile_id":"p"})
    db.upsert_profile({**profile,"provider":"openai","base_url":"https://example.com/v1","model":"paid"})
    class Secrets:
        def get_api_key(self,_):raise AssertionError('must not access credentials for a changed job')
    await JobRunner(db,Secrets())._run('a')
    assert db.get_job('a')['status']=='failed'
    assert '不会把免费任务' in db.get_job('a')['error']
