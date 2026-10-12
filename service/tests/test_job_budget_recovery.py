from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from zotero_local_bilingual.guarded_translator import GuardedTranslator
from zotero_local_bilingual.jobs import JobRunner


def translator(tmp_path, handler, **options):
    result = GuardedTranslator({
        "model": "mock", "base_url": "https://example.invalid/v1",
        "engine_working_dir": str(tmp_path), "ignore_cache": True, **options,
    }, "fake-key")
    result.client = result.client.with_options(
        http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return result


def response(prompt=20, completion=10):
    return httpx.Response(200, json={
        "id": "mock", "object": "chat.completion", "created": 0, "model": "mock",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "译文"}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion,
                  "total_tokens": prompt + completion},
    })


MESSAGES = [{"role": "user", "content": "x"}]


def test_retry_restores_request_limit_and_history(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        # Request reservation must be durable before the network transport runs.
        audit = json.loads((tmp_path / "api-usage.json").read_text("utf8"))
        assert audit["requests"][-1]["status"] == "running"
        assert audit["requests"][-1]["reserved_tokens"] > 0
        return response()

    first = translator(tmp_path, handler, max_requests=1)
    first._request(MESSAGES)
    second = translator(tmp_path, handler, max_requests=1)
    assert second.snapshot()["total_tokens"] == 30
    assert second.snapshot()["request_count"] == 1
    with pytest.raises(RuntimeError, match="上限"):
        second._request(MESSAGES)
    assert len(calls) == 1
    audit = json.loads(second.audit_path.read_text("utf8"))
    assert audit["requests"][0]["status"] == "ok"
    assert audit["total_tokens"] == 30


def test_retry_cannot_reset_token_budget(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        return response(1000, 200)

    translator(tmp_path, handler, max_total_tokens=5000)._request(MESSAGES)
    resumed = translator(tmp_path, handler, max_total_tokens=5000)
    with pytest.raises(RuntimeError, match="上限"):
        resumed._request(MESSAGES)
    assert len(calls) == 1
    assert resumed.snapshot()["total_tokens"] == 1200


def test_retry_keeps_unknown_timeout_spend_reserved(tmp_path):
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("simulated response loss", request=request)

    first = translator(tmp_path, handler, max_total_tokens=6000)
    with pytest.raises(RuntimeError):
        first._request(MESSAGES)
    reserved = first.snapshot()["unconfirmed_tokens"]
    assert reserved > 4000
    resumed = translator(tmp_path, handler, max_total_tokens=6000)
    assert resumed.snapshot()["unconfirmed_tokens"] == reserved
    assert resumed.snapshot()["total_tokens"] == 0  # Known usage stays honest.
    assert resumed.snapshot()["previous_failures"]
    with pytest.raises(RuntimeError, match="上限"):
        resumed._request(MESSAGES)
    assert len(calls) == 1


def test_crash_with_inflight_request_preserves_reservation(tmp_path):
    initial = translator(tmp_path, lambda request: response())
    initial.requests = [{"number": 1, "status": "running", "reserved_tokens": 4500}]
    initial.pending_tokens = 4500
    initial.save_audit()
    resumed = translator(tmp_path, lambda request: pytest.fail("must not send"), max_total_tokens=6000)
    assert resumed.requests[0]["status"] == "interrupted"
    assert resumed.snapshot()["pending_tokens"] == 0
    assert resumed.snapshot()["unconfirmed_tokens"] == 4500
    with pytest.raises(RuntimeError, match="上限"):
        resumed._request(MESSAGES)


@pytest.mark.parametrize("payload", ["{", "[]", '{"total_tokens":0}'])
def test_damaged_audit_fails_closed_without_overwriting(tmp_path, payload):
    audit = tmp_path / "api-usage.json"
    audit.write_text(payload, "utf8")
    with pytest.raises(RuntimeError, match="审计损坏"):
        translator(tmp_path, lambda request: pytest.fail("must not send"))
    assert audit.read_text("utf8") == payload


def test_legacy_unaccounted_request_fails_closed(tmp_path):
    initial = translator(tmp_path, lambda request: response())
    initial.requests = [{"number": 1, "status": "failed"}]
    initial.save_audit()
    with pytest.raises(RuntimeError, match="审计损坏"):
        translator(tmp_path, lambda request: pytest.fail("must not send"))


def test_audit_write_failure_prevents_network_and_latches_stop(tmp_path, monkeypatch):
    calls = []
    current = translator(tmp_path, lambda request: calls.append(request) or response())

    def cannot_write():
        raise OSError("disk unavailable")

    monkeypatch.setattr(current, "save_audit", cannot_write)
    for _ in range(2):
        with pytest.raises(RuntimeError, match="未发送"):
            current._request(MESSAGES)
    assert not calls


class FakeDatabase:
    def __init__(self, tmp_path):
        self.jobs = {
            name: {"id": name, "status": state, "source_path": str(tmp_path / name / "source.pdf"),
                   "profile_id": "mock", "options_json": "{}", "pages": None}
            for name, state in (("first", "running"), ("cancelled", "cancelling"), ("second", "queued"))
        }
        for row in self.jobs.values():
            Path(row["source_path"]).parent.mkdir()

    def list_recoverable_jobs(self):
        return list(self.jobs)

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def update_job(self, job_id, **fields):
        self.jobs[job_id].update(fields)

    def get_profile(self, _profile_id):
        return {"model": "mock", "base_url": "https://example.invalid/v1", "qps": 2,
                "no_send_temperature": False, "reasoning": None}


class FakeSecrets:
    def get_api_key(self, _profile_id):
        return "fake-key"


def test_restart_does_not_revive_cancelling_and_consumer_survives_failure(tmp_path):
    async def scenario():
        database = FakeDatabase(tmp_path)
        runner = JobRunner(database, FakeSecrets())
        seen = []

        async def run(job_id):
            seen.append(job_id)
            if job_id == "first":
                raise ValueError("broken preparation")
            database.update_job(job_id, status="completed")

        runner._run = run
        await runner.start()
        await asyncio.wait_for(runner.queue.join(), timeout=2)
        assert seen == ["first", "second"]
        assert database.jobs["cancelled"]["status"] == "cancelled"
        assert database.jobs["first"]["status"] == "failed"
        assert database.jobs["second"]["status"] == "completed"
        assert not runner.loop_task.done()
        await runner.stop()

    asyncio.run(scenario())


def test_failure_report_tolerates_damaged_audit(tmp_path, monkeypatch):
    async def scenario():
        database = FakeDatabase(tmp_path)
        runner = JobRunner(database, FakeSecrets())
        working = tmp_path / "first" / "engine-working"
        working.mkdir()
        audit = working / "api-usage.json"
        audit.write_text("{", "utf8")

        async def cannot_start(*args, **kwargs):
            raise OSError("worker unavailable")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", cannot_start)
        await runner._run("first")
        assert database.jobs["first"]["status"] == "failed"
        report = json.loads((tmp_path / "first" / "report.json").read_text("utf8"))
        assert "usage_audit_error" in report
        assert audit.read_text("utf8") == "{"

    asyncio.run(scenario())


def test_failure_status_survives_report_write_error(tmp_path, monkeypatch):
    async def scenario():
        database = FakeDatabase(tmp_path)
        runner = JobRunner(database, FakeSecrets())
        original_write = Path.write_text

        def write(path, *args, **kwargs):
            if path.name == "report.json":
                raise OSError("report destination unavailable")
            return original_write(path, *args, **kwargs)

        async def cannot_start(*args, **kwargs):
            raise OSError("worker unavailable")

        monkeypatch.setattr(Path, "write_text", write)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", cannot_start)
        await runner._run("first")
        assert database.jobs["first"]["status"] == "failed"
        assert database.jobs["first"]["report_path"] is None

    asyncio.run(scenario())


def test_bad_worker_event_stops_unmonitored_process(tmp_path, monkeypatch):
    async def scenario():
        database = FakeDatabase(tmp_path)
        runner = JobRunner(database, FakeSecrets())

        async def lines():
            yield b'{"type":"progress","progress":"not a number"}\n'

        class Process:
            returncode = None
            stdout = lines()
            terminated = False

            def terminate(self):
                self.terminated = True
                self.returncode = 1

            async def wait(self):
                return self.returncode

        process = Process()

        async def start(*args, **kwargs):
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
        await runner._run("first")
        assert process.terminated
        assert runner.active_process is None
        assert database.jobs["first"]["status"] == "failed"

    asyncio.run(scenario())
