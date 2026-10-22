from pathlib import Path
import os
import subprocess
import sys
import pytest
from zotero_local_bilingual.config import Settings
from zotero_local_bilingual.secrets import SecretStore, SERVICE_NAME


def test_macos_application_support(monkeypatch, tmp_path):
    import zotero_local_bilingual.config as config
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.delenv("ZLB_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert Settings.load().data_dir == tmp_path / "Library/Application Support/ZoteroLocalBilingual"
    monkeypatch.setenv("ZLB_DATA_DIR", str(tmp_path / "explicit"))
    assert Settings.load().data_dir == tmp_path / "explicit"


def test_secret_store_never_swallows_denied_keychain_delete():
    import keyring.errors
    class Denied:
        def get_password(self, service, profile):
            assert service == SERVICE_NAME
            return "test-only"
        def delete_password(self, service, profile):
            raise keyring.errors.PasswordDeleteError("denied")
    store = SecretStore(); store._keyring = Denied()
    with pytest.raises(keyring.errors.PasswordDeleteError): store.delete_api_key("test")


def test_missing_credential_delete_is_idempotent():
    class Missing:
        def get_password(self, service, profile): return None
        def delete_password(self, *args): raise AssertionError("must not delete missing key")
    store = SecretStore(); store._keyring = Missing(); store.delete_api_key("test")


def test_credential_save_failure_is_clear_and_does_not_save_profile(tmp_path):
    from fastapi.testclient import TestClient
    from zotero_local_bilingual.app import create_app
    class Denied:
        def set_api_key(self, *args): raise RuntimeError("locked")
        def get_api_key(self, *args): return None
    settings = Settings(data_dir=tmp_path); token = settings.get_or_create_service_token()
    with TestClient(create_app(settings, Denied())) as client:
        headers={"X-Local-Bilingual-Token": token}
        result=client.post("/v1/profiles",headers=headers,json={"id":"test","name":"test","model":"mock","base_url":"https://example.invalid/v1","api_key":"test-only"})
        assert result.status_code == 503
        assert "钥匙串" in result.json()["detail"]
        assert client.get("/v1/profiles",headers=headers).json() == []


@pytest.mark.skipif(os.name == "nt", reason="UNIX permission and flock semantics require a UNIX host")
def test_posix_token_permissions_and_process_lock(tmp_path):
    import fcntl
    settings=Settings(data_dir=tmp_path / "data"); settings.get_or_create_service_token()
    assert settings.data_dir.stat().st_mode & 0o777 == 0o700
    assert settings.token_path.stat().st_mode & 0o777 == 0o600
    with (settings.data_dir / "service.lock").open("a+b") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result=subprocess.run([sys.executable,"-B","-m","zotero_local_bilingual.cli","--managed","--data-dir",str(settings.data_dir)],capture_output=True,text=True,timeout=30)
        assert result.returncode != 0
        assert "已经启动" in result.stderr
        assert not (settings.data_dir / "managed-service.json").exists()
