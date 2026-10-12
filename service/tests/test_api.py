from __future__ import annotations

from fastapi.testclient import TestClient
from zotero_local_bilingual.app import create_app
from zotero_local_bilingual.config import Settings


class MemorySecretStore:
    def __init__(self):
        self.values: dict[str, str] = {}

    def set_api_key(self, profile_id: str, api_key: str) -> None:
        self.values[profile_id] = api_key

    def get_api_key(self, profile_id: str) -> str | None:
        return self.values.get(profile_id)


def test_health_and_profile_authentication(tmp_path):
    secrets = MemorySecretStore()
    settings = Settings(data_dir=tmp_path)
    token = settings.get_or_create_service_token()
    app = create_app(settings, secrets)

    with TestClient(app) as client:
        health = client.get("/v1/health")
        assert health.status_code == 200
        assert health.json()["engine"] == "BabelDOC"
        assert client.get("/v1/profiles").status_code == 401

        response = client.post(
            "/v1/profiles",
            headers={"X-Local-Bilingual-Token": token},
            json={
                "id": "default",
                "name": "Default",
                "base_url": "https://example.invalid/v1",
                "model": "example-model",
                "api_key": "secret",
                "qps": 2,
            },
        )
        assert response.status_code == 200
        assert response.json()["has_api_key"] is True
        assert "api_key" not in response.json()
        assert secrets.values["default"] == "secret"


def test_page_selection_validated_before_queue(tmp_path):
    import pymupdf
    import json
    secrets=MemorySecretStore()
    settings=Settings(data_dir=tmp_path)
    token=settings.get_or_create_service_token()
    app=create_app(settings,secrets)
    queued=[]
    async def enqueue(job_id):queued.append(job_id)
    app.state.runner.enqueue=enqueue
    headers={"X-Local-Bilingual-Token":token}
    with pymupdf.open() as pdf:
        pdf.new_page();pdf.new_page();payload=pdf.tobytes()
    with TestClient(app) as client:
        client.post('/v1/profiles',headers=headers,json={"id":"test","name":"test","base_url":"https://example.invalid/v1","model":"mock","api_key":"test"}).raise_for_status()
        def submit(pages,options=None):
            return client.post('/v1/jobs',headers=headers,files={"file":("source.pdf",payload,"application/pdf")},data={"profile_id":"test","source_title":"test","pages":pages,"options_json":json.dumps(options or {})})
        assert submit('3').status_code==400
        assert submit('1',{"glossary_text":"missing delimiter"}).status_code==400
        assert not queued
        good=submit('2,1,2');assert good.status_code==200
        assert good.json()['pages']=='1,2'
        assert len(queued)==1


def test_idempotent_submission_does_not_enqueue_twice(tmp_path):
    import pymupdf
    from unittest.mock import AsyncMock
    secrets = MemorySecretStore()
    settings = Settings(data_dir=tmp_path)
    app = create_app(settings, secrets)
    app.state.runner.enqueue = AsyncMock()
    headers = {"X-Local-Bilingual-Token": settings.get_or_create_service_token()}
    with pymupdf.open() as pdf:
        pdf.new_page()
        payload = pdf.tobytes()
    with TestClient(app) as client:
        client.post('/v1/profiles', headers=headers, json={"id":"test","name":"test","base_url":"https://example.invalid/v1","model":"mock","api_key":"test"}).raise_for_status()
        data = {"profile_id":"test", "source_title":"test", "pages":"1", "client_request_id":"a" * 32}
        def submit():
            return client.post('/v1/jobs', headers=headers, files={"file":("source.pdf",payload,"application/pdf")}, data=data)
        first = submit(); first.raise_for_status()
        second = submit(); second.raise_for_status()
        assert first.json()['id'] == second.json()['id'] == 'a' * 32
        assert app.state.runner.enqueue.await_count == 1
        data['options_json'] = '{"glossary_text":"signal=信号"}'
        assert submit().status_code == 409
        data['client_request_id'] = '../invalid'
        assert submit().status_code == 400
        assert app.state.runner.enqueue.await_count == 1
