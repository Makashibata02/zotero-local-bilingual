from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

from .database import Database
from .models import JobStatus
from .pdf import combine_side_by_side, validate_pdf
from .pages import parse_pages
from .secrets import SecretStore
from .translation_policy import TranslationOptions

logger = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, database: Database, secrets: SecretStore):
        self.database = database
        self.secrets = secrets
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.loop_task: asyncio.Task | None = None
        self.active_job: str | None = None
        self.active_process: asyncio.subprocess.Process | None = None
        self.processes: dict[str, asyncio.subprocess.Process] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.pending: list[str] = []
        self.enqueued: set[str] = set()

    @property
    def max_parallel(self):
        return getattr(self.database, "parallel_limit", lambda: 1)()

    @property
    def active_jobs(self):
        return list(self.tasks)

    async def start(self) -> None:
        for job_id in self.database.list_recoverable_jobs():
            job = self.database.get_job(job_id)
            if job and job["status"] == JobStatus.CANCELLING:
                self.database.update_job(job_id, status=JobStatus.CANCELLED, stage="cancelled")
                continue
            self.database.update_job(
                job_id,
                status=JobStatus.QUEUED,
                stage="recovered_after_restart",
                error=None,
            )
            await self.enqueue(job_id)
        self.loop_task = asyncio.create_task(self._loop(), name="zlb-job-runner")

    async def stop(self) -> None:
        for process in list(self.processes.values()):
            if process.returncode is None:
                process.terminate()
        if self.loop_task:
            self.loop_task.cancel()
            try:
                await self.loop_task
            except asyncio.CancelledError:
                pass
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    async def enqueue(self, job_id: str) -> None:
        if job_id not in self.enqueued:
            self.enqueued.add(job_id)
            await self.queue.put(job_id)

    async def cancel(self, job_id: str) -> bool:
        job = self.database.get_job(job_id)
        if not job or job["status"] in {
            JobStatus.COMPLETED,
            JobStatus.CANCELLED,
        }:
            return False
        if job["status"] in {JobStatus.PAUSED, JobStatus.FAILED}:
            self.database.update_job(job_id, status=JobStatus.CANCELLED, stage="cancelled")
            return True
        self.database.update_job(
            job_id, status=JobStatus.CANCELLING, stage="cancelling"
        )
        process = self.processes.get(job_id)
        if process and process.returncode is None:
            process.terminate()
        return True

    async def _loop(self) -> None:
        while True:
            while not self.queue.empty():
                self.pending.append(self.queue.get_nowait())
            for job_id in list(self.pending):
                job = self.database.get_job(job_id)
                if not job or job["status"] != JobStatus.QUEUED:
                    if job and job["status"] == JobStatus.CANCELLING:
                        self.database.update_job(job_id, status=JobStatus.CANCELLED, stage="cancelled")
                    self.pending.remove(job_id)
                    self.enqueued.discard(job_id)
                    self.queue.task_done()
                    continue
                if len(self.tasks) >= self.max_parallel:
                    break
                profile = self.database.get_profile(job["profile_id"]) or {}
                free = profile.get("provider", "openai") != "openai"
                if free and any((self.database.get_profile(self.database.get_job(i)["profile_id"]) or {}).get("provider", "openai") != "openai" for i in self.tasks):
                    continue
                self.pending.remove(job_id)
                self.tasks[job_id] = asyncio.create_task(self._execute(job_id))
            await asyncio.sleep(0.05)

    async def _execute(self, job_id):
        try:
            await self._run(job_id)
        except Exception as exc:
            logger.error("Job boundary failure: %s", type(exc).__name__)
            self.database.update_job(job_id, status=JobStatus.FAILED, stage="failed", error=str(exc)[:2000])
        finally:
            self.tasks.pop(job_id, None)
            self.enqueued.discard(job_id)
            self.queue.task_done()

    async def _run(self, job_id: str) -> None:
        job = self.database.get_job(job_id)
        if not job:
            return
        profile = self.database.get_profile(job["profile_id"])
        snapshot = json.loads(job.get("profile_snapshot_json") or "null")
        if snapshot and profile and any(snapshot.get(k) != profile.get(k) for k in ("provider", "base_url", "model")):
            self.database.update_job(job_id, status=JobStatus.FAILED, stage="configuration_changed",
                error="任务创建后服务配置已更改。请恢复原配置再恢复任务，或明确创建新任务；不会把免费任务改用收费 API。")
            return
        profile = snapshot or profile
        api_key = self.secrets.get_api_key(job["profile_id"]) if profile and profile.get("provider", "openai") == "openai" else None
        if not profile or (profile.get("provider", "openai") == "openai" and not api_key):
            self.database.update_job(
                job_id,
                status=JobStatus.FAILED,
                error="模型配置或 API Key 不存在",
                stage="configuration_error",
            )
            return

        job_dir = Path(job["source_path"]).parent
        output_dir = job_dir / "engine-output"
        working_dir = job_dir / "engine-working"
        output_dir.mkdir(exist_ok=True)
        working_dir.mkdir(exist_ok=True)
        options = TranslationOptions.model_validate_json(job["options_json"]).model_dump()
        spec = {
            **options,
            "source_path": job["source_path"],
            "engine_output_dir": str(output_dir),
            "engine_working_dir": str(working_dir),
            "provider": profile.get("provider", "openai"),
            "profile_id": job["profile_id"],
            "rate_database": str(self.database.path) if hasattr(self.database, "path") else None,
            "model": profile["model"],
            "base_url": profile["base_url"],
            "qps": profile["qps"],
            "no_send_temperature": bool(profile["no_send_temperature"]),
            "reasoning": profile["reasoning"],
            "pages": job["pages"],
            "custom_system_prompt": options.get("custom_system_prompt"),
            "primary_font_family": options.get("primary_font_family", "serif"),
            "disable_rich_text_translate": options.get(
                "disable_rich_text_translate", False
            ),
            "ignore_cache": options.get("ignore_cache", False),
        }
        spec_path = job_dir / "job-spec.json"
        spec_path.write_text(json.dumps(spec, ensure_ascii=False, indent=2), "utf-8")
        stderr_path = job_dir / "engine-stderr.log"
        env = os.environ.copy()
        env.pop("ZLB_API_KEY", None)
        if api_key:
            env["ZLB_API_KEY"] = api_key
        self.active_job = job_id
        self.database.update_job(
            job_id,
            status=JobStatus.RUNNING,
            stage="starting_engine",
            progress=0,
            error=None,
        )
        mono_path: Path | None = None
        usage: dict = {}
        quality: dict = {}
        started = time.perf_counter()
        stage_times: dict[str, float] = {}
        engine_seconds: float | None = None
        fatal_error: str | None = None
        try:
            with stderr_path.open("wb") as stderr_stream:
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-B",
                    "-m",
                    "zotero_local_bilingual.engine_worker",
                    str(spec_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=stderr_stream,
                    env=env,
                    **({"creationflags": 0x08000000} if sys.platform == "win32" else {}),
                )
                self.active_process = process
                self.processes[job_id] = process
                if self.database.get_job(job_id)["status"] == JobStatus.CANCELLING:
                    process.terminate()
                assert process.stdout is not None
                async for raw_line in process.stdout:
                    try:
                        event = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if event.get("type") == "progress":
                        stage_times.setdefault(str(event.get("stage")), round(time.perf_counter() - started, 3))
                        self.database.update_job(
                            job_id,
                            stage=str(event.get("stage") or "processing"),
                            progress=max(
                                0.0, min(99.0, float(event.get("progress", 0)))
                            ),
                        )
                    elif event.get("type") == "result":
                        mono_path = Path(event["mono_pdf_path"])
                        usage = event.get("usage", {})
                        quality = event.get("quality", {})
                        engine_seconds = event.get("engine_seconds")
                    elif event.get("type") == "fatal":
                        fatal_error = str(event.get("error", "引擎失败"))
                return_code = await process.wait()

            current = self.database.get_job(job_id)
            if current and current["status"] == JobStatus.CANCELLING:
                self.database.update_job(
                    job_id, status=JobStatus.CANCELLED, stage="cancelled"
                )
                return
            if return_code != 0 or mono_path is None:
                raise RuntimeError(fatal_error or f"引擎退出码 {return_code}")

            import pymupdf
            language = options["target_language"]
            mode = options["output_mode"]
            artifact_path = job_dir / f"bilingual-left-en-right-{language}.pdf"
            report_path = job_dir / "report.json"
            self.database.update_job(job_id, stage="combining_pages", progress=99)
            selected = parse_pages(job["pages"], validate_pdf(Path(job["source_path"])))
            report = combine_side_by_side(
                Path(job["source_path"]), mono_path, artifact_path, report_path,
                selected_pages=selected
            ) if mode != "mono" else {"layout": "translation_only", "selected_pages": selected, "quality": {"manual_review_required": True}}
            artifacts = []
            if mode != "mono":
                artifacts.append({"kind": "dual", "name": artifact_path.name, "path": str(artifact_path)})
            if mode != "dual":
                translated_path = job_dir / f"translated-{language}.pdf"
                with pymupdf.open(mono_path) as document, pymupdf.open() as output:
                    for page in selected:
                        output.insert_pdf(document, from_page=page, to_page=page)
                    output.save(translated_path, garbage=4, deflate=True)
                artifacts.append({"kind": "mono", "name": translated_path.name, "path": str(translated_path)})
            artifact_path = Path(artifacts[0]["path"])
            from .artifact_names import artifact_name
            metadata = json.loads(job.get("metadata_json") or "{}")
            for artifact in artifacts:
                artifact["name"] = artifact_name(metadata, job.get("source_title", "PDF"), language, artifact["kind"], profile)
            report["artifacts"] = [{"kind": a["kind"], "name": a["name"]} for a in artifacts]
            report["translation_usage"] = usage
            report["translation_quality"] = quality
            report["selected_pages"] = job["pages"] or "all"
            report["timing"] = {"engine_seconds": engine_seconds,
                "processing_seconds": round(time.perf_counter() - started, 3),
                "stage_first_seen_seconds": stage_times}
            report["settings"] = {k: options[k] for k in ["glossary_text", "primary_font_family", "disable_rich_text_translate", "thinking_mode"]}
            report["engine"] = {"name": "BabelDOC", "version": "0.6.4"}
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.database.update_job(
                job_id,
                status=JobStatus.COMPLETED,
                stage="completed",
                progress=100,
                artifact_path=str(artifact_path),
                artifacts_json=json.dumps(artifacts),
                report_path=str(report_path),
            )
        except Exception as exc:  # noqa: BLE001 - task boundary must persist failures
            failure_report = job_dir / "report.json"
            audit = working_dir / "api-usage.json"
            failure_payload = {"status": "failed", "error": str(exc)[:2000], "translation_usage": {}}
            try:
                if audit.exists():
                    saved_usage = json.loads(audit.read_text("utf8"))
                    if not isinstance(saved_usage, dict):
                        raise ValueError("usage audit is not an object")
                    failure_payload["translation_usage"] = saved_usage
            except (OSError, ValueError, TypeError):
                failure_payload["usage_audit_error"] = "用量审计不可读；保留原文件，未将未知用量视为零。"
            # Persist the status even when the report cannot be written.
            budget_stop = failure_payload["translation_usage"].get("failure_reason") == "budget_limit"
            if budget_stop:
                failure_payload["status"] = "paused"
            self.database.update_job(
                job_id,
                status=JobStatus.PAUSED if budget_stop else JobStatus.FAILED,
                stage="budget_limit" if budget_stop else "failed",
                error=str(exc)[:2000],
                report_path=None,
            )
            try:
                failure_report.write_text(json.dumps(failure_payload, ensure_ascii=False, indent=2), encoding="utf8")
                self.database.update_job(job_id, report_path=str(failure_report))
            except OSError as report_error:
                logger.error("Cannot write failure report: %s", type(report_error).__name__)
        finally:
            own_process = self.processes.get(job_id)
            if own_process and own_process.returncode is None:
                # Stop work if parsing its events or recording state failed.
                # Do not leave an unmonitored worker making paid requests.
                try:
                    own_process.terminate()
                    await asyncio.wait_for(own_process.wait(), timeout=10)
                except ProcessLookupError:
                    pass
                except TimeoutError:
                    own_process.kill()
                    await own_process.wait()
            env.pop("ZLB_API_KEY", None)
            self.processes.pop(job_id, None)
            self.active_job = next(iter(self.processes), None)
            self.active_process = next(iter(self.processes.values()), None)
