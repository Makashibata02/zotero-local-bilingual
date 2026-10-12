import json
import os
from pathlib import Path
import threading
import time

import pytest
from zotero_local_bilingual import atomic_io
from zotero_local_bilingual.free_translate import FreeTranslator


def test_transient_replace_retry_preserves_previous_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "api-usage.json"
    path.write_text('{"requests": 7}', "utf8")
    real = os.replace
    attempts = []
    def replace(src, dst):
        attempts.append(src)
        if len(attempts) < 4:
            assert json.loads(path.read_text("utf8"))["requests"] == 7
            raise PermissionError("reader temporarily holds snapshot")
        real(src, dst)
    monkeypatch.setattr(atomic_io.os, "replace", replace)
    monkeypatch.setattr(atomic_io.time, "sleep", lambda _: None)
    atomic_io.write_json_atomic(path, {"requests": 8})
    assert len(attempts) == 4
    assert atomic_io.read_json(path) == {"requests": 8}
    assert not list(tmp_path.glob("*.tmp"))


def test_persistent_denial_keeps_audit_and_has_bounded_retries(tmp_path, monkeypatch):
    path = tmp_path / "api-usage.json"
    path.write_text('{"requests": 7}', "utf8")
    attempts = []
    def denied(*args):
        attempts.append(args)
        raise PermissionError("permanent permission problem")
    monkeypatch.setattr(atomic_io.os, "replace", denied)
    monkeypatch.setattr(atomic_io.time, "sleep", lambda _: None)
    with pytest.raises(PermissionError):
        atomic_io.write_json_atomic(path, {"requests": 8})
    assert len(attempts) == 10
    assert atomic_io.read_json(path) == {"requests": 7}
    assert not list(tmp_path.glob("*.tmp"))


def test_other_io_errors_are_not_retried(tmp_path, monkeypatch):
    attempts = []
    def broken(*args):
        attempts.append(args)
        raise OSError("disk unavailable")
    monkeypatch.setattr(atomic_io.os, "replace", broken)
    with pytest.raises(OSError):
        atomic_io.write_json_atomic(tmp_path / "usage.json", {})
    assert len(attempts) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing semantics")
def test_actual_windows_open_reader_reproduces_error_then_retry_succeeds(tmp_path):
    path = tmp_path / "api-usage.json"
    path.write_text('{"old": true}', "utf8")
    replacement = tmp_path / "raw.tmp"
    replacement.write_text('{"new": true}', "utf8")
    stream = path.open("rb")
    try:
        with pytest.raises(PermissionError) as exc:
            replacement.replace(path)
        assert exc.value.winerror in {5, 32}
        release = threading.Thread(target=lambda: (time.sleep(.12), stream.close()))
        release.start()
        atomic_io.write_json_atomic(path, {"new": True})
        release.join()
    finally:
        stream.close()
    assert atomic_io.read_json(path) == {"new": True}


def test_json_reader_closes_handle_before_decoding(tmp_path, monkeypatch):
    path = tmp_path / "api-usage.json"
    path.write_text('{"old": true}', "utf8")
    load = json.loads
    def replace_during_decode(payload):
        atomic_io.write_json_atomic(path, {"new": True})
        return load(payload)
    monkeypatch.setattr(atomic_io.json, "loads", replace_during_decode)
    assert atomic_io.read_json(path) == {"old": True}
    assert load(path.read_text("utf8")) == {"new": True}


def test_file_retry_does_not_repeat_translation_request(tmp_path, monkeypatch):
    class Provider:
        calls = 0
        def translate(self, text):
            self.calls += 1
            return "译文"
    provider = Provider()
    translator = FreeTranslator({"provider":"bing", "engine_working_dir":str(tmp_path),
        "ignore_cache":True}, provider, interval=0)
    real = os.replace
    denied = False
    def intermittent(src, dst):
        nonlocal denied
        if provider.calls == 1 and not denied and Path(dst).name == "api-usage.json":
            denied = True
            raise PermissionError("short reader lock after successful network call")
        return real(src, dst)
    monkeypatch.setattr(atomic_io.os, "replace", intermittent)
    assert translator.do_translate("A sentence") == "译文"
    assert provider.calls == 1
    assert atomic_io.read_json(tmp_path/"api-usage.json")["request_count"] == 1


def test_concurrent_polling_observes_only_complete_json(tmp_path):
    path = tmp_path / "api-usage.json"
    atomic_io.write_json_atomic(path, {"count":0, "payload":"x" * 2000})
    stop = threading.Event()
    failures = []
    snapshots = []
    def poll():
        while not stop.is_set():
            try:
                result = atomic_io.read_json(path)
                assert result["payload"] == "x" * 2000
                snapshots.append(result["count"])
            except PermissionError:
                # job_metrics skips this transient read exactly the same way.
                pass
            except Exception as exc:
                failures.append(exc)
                break
            finally:
                # 200 polls/sec is already far above the workbench's 0.4/sec.
                # A busy loop would hold the file almost continuously.
                time.sleep(.005)
    reader = threading.Thread(target=poll)
    reader.start()
    try:
        for count in range(100):
            atomic_io.write_json_atomic(path, {"count":count, "payload":"x" * 2000})
    finally:
        stop.set(); reader.join()
    assert failures == []
    assert snapshots
