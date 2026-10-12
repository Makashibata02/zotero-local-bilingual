from __future__ import annotations

import os
import asyncio
import re
import hashlib
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .config import Settings
from .database import Database
from .jobs import JobRunner
from .models import (
    HealthResponse,
    JobStatus,
    JobView,
    ProfileInput,
    ProfilePublic,
    TestResult,
    BudgetExtension,
)
from .pdf import validate_pdf
from .pages import parse_pages
from .telemetry import job_metrics
from .translation_policy import TranslationOptions, thinking_setting
from pydantic import ValidationError
from .secrets import SecretStore


def create_app(
    settings: Settings | None = None,
    secret_store: SecretStore | None = None,
) -> FastAPI:
    settings = settings or Settings.load()
    settings.prepare()
    service_token = settings.get_or_create_service_token()
    database = Database(settings.database_path)
    database.initialize()
    secret_store = secret_store or SecretStore()
    runner = JobRunner(database, secret_store)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await runner.start()
        from .test_cleanup import cleanup_test
        for row in database.list_jobs(limit=100000, include_hidden=True):
            if row.get("cleanup_state") == "pending":
                await asyncio.to_thread(cleanup_test, database, settings.jobs_dir, row)
        yield
        await runner.stop()

    app = FastAPI(
        title="Zotero Local Bilingual",
        version=__version__,
        lifespan=lifespan,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )
    app.state.settings = settings
    app.state.database = database
    app.state.secret_store = secret_store
    app.state.runner = runner

    app.state.last_business = time.monotonic()

    @app.middleware("http")
    async def business_activity(request, call_next):
        response = await call_next(request)
        if request.method == "POST" and response.status_code < 400:
            app.state.last_business = time.monotonic()
        return response

    def authorize(x_local_bilingual_token: str | None = Header(default=None)) -> None:
        if not x_local_bilingual_token or not __import__("hmac").compare_digest(
            x_local_bilingual_token, service_token
        ):
            raise HTTPException(status_code=401, detail="本机服务令牌无效")

    def public_profile(row: dict) -> ProfilePublic:
        return ProfilePublic(
            id=row["id"],
            provider=row.get("provider", "openai"),
            name=row["name"],
            base_url=row["base_url"],
            model=row["model"],
            qps=row["qps"],
            no_send_temperature=bool(row["no_send_temperature"]),
            reasoning=row["reasoning"],
            has_api_key=row.get("provider", "openai") == "openai" and secret_store.get_api_key(row["id"]) is not None,
        )

    def job_view(row: dict) -> JobView:
        artifact = Path(row["artifact_path"]).name if row["artifact_path"] else None
        saved_artifacts = json.loads(row.get("artifacts_json") or "[]")
        if saved_artifacts:
            artifact = saved_artifacts[0]["name"]
        report = Path(row["report_path"]).name if row["report_path"] else None
        options = TranslationOptions.model_validate_json(row["options_json"])
        profile = json.loads(row.get("profile_snapshot_json") or "null") or database.get_profile(row["profile_id"]) or {}
        budget = None
        if profile.get("provider", "openai") == "openai":
            budget = {"max_total_tokens": options.max_total_tokens, "max_requests": options.max_requests,
                "can_extend": not options.is_selftest and row["status"] in {JobStatus.PAUSED, JobStatus.FAILED}
                    and (row["stage"] == "budget_limit" or "达到任务 token 或请求次数上限" in (row["error"] or ""))}
        metrics = job_metrics(row)
        if profile.get("provider", "openai") != "openai":
            metrics["usage_kind"] = "characters"
        return JobView(
            provider=profile.get("provider", "openai"),
            metadata=json.loads(row.get("metadata_json") or "{}"),
            target_language=options.target_language, output_mode=options.output_mode,
            artifacts=[{"kind": a["kind"], "name": a["name"]} for a in json.loads(row.get("artifacts_json") or "[]")],
            hidden=bool(row.get("hidden")), is_selftest=options.is_selftest,
            cleanup_state=row.get("cleanup_state"),
            queue_position=next((i+1 for i, r in enumerate(r for r in database.list_jobs(limit=100000) if r["status"] == "queued") if r["id"] == row["id"]), None) if row["status"] == "queued" else None,
            service_name=profile.get("name", row["profile_id"]) + (" · " + profile.get("model", "") if profile.get("model") else ""),
            id=row["id"],
            status=row["status"],
            source_title=row["source_title"],
            source_sha256=row["source_sha256"],
            profile_id=row["profile_id"],
            pages=row["pages"],
            stage=row["stage"],
            progress=row["progress"],
            error=row["error"],
            artifact_name=artifact,
            report_name=report,
            metrics=metrics,
            budget=budget,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @app.get("/v1/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        try:
            import babeldoc

            engine_version = getattr(babeldoc, "__version__", "0.6.4")
        except ImportError:
            engine_version = None
        return HealthResponse(
            ok=engine_version is not None,
            managed=os.environ.get("ZLB_MANAGED") == "1",
            runtime_ready=engine_version is not None,
            version=__version__,
            engine="BabelDOC",
            engine_version=engine_version,
            data_dir=str(settings.data_dir),
            active_job=runner.active_job,
            active_jobs=runner.active_jobs, max_parallel=runner.max_parallel,
            deployment_verified=any(TranslationOptions.model_validate_json(r["options_json"]).is_selftest and r["status"] == "completed" for r in database.list_jobs(limit=100000, include_hidden=True)),
        )

    @app.get(
        "/v1/profiles",
        response_model=list[ProfilePublic],
        dependencies=[Depends(authorize)],
    )
    async def list_profiles() -> list[ProfilePublic]:
        return [public_profile(row) for row in database.list_profiles()]

    @app.post(
        "/v1/profiles", response_model=ProfilePublic, dependencies=[Depends(authorize)]
    )
    async def save_profile(profile: ProfileInput) -> ProfilePublic:
        data = profile.model_dump(mode="json")
        api_key = data.pop("api_key", None)
        data["base_url"] = str(profile.base_url).rstrip("/") if profile.base_url else ""
        previous = database.get_profile(profile.id)
        if previous and database.profile_in_use(profile.id):
            if not api_key and all(previous.get(k) == v for k,v in data.items()):
                return public_profile(previous)
            raise HTTPException(status_code=409, detail="此配置被排队、运行或等待追加额度的任务引用，不能覆盖。请另存为新配置。")
        database.upsert_profile(data)
        if api_key:
            secret_store.set_api_key(profile.id, api_key)
        return public_profile(database.get_profile(profile.id))

    @app.post(
        "/v1/profiles/{profile_id}/test",
        response_model=TestResult,
        dependencies=[Depends(authorize)],
    )
    async def test_profile(profile_id: str) -> TestResult:
        profile = database.get_profile(profile_id)
        if profile and profile.get("provider", "openai") != "openai":
            from .free_translate import WebProvider
            started = time.perf_counter()
            try:
                preview = await asyncio.to_thread(WebProvider(profile["provider"]).translate, "A receiver does not know the message in advance.")
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"免费服务连接测试失败：{exc}") from exc
            return TestResult(ok=True, model=profile["provider"], response_preview=preview[:200], latency_ms=int((time.perf_counter()-started)*1000), usage={"usage_kind":"characters", "characters":len("A receiver does not know the message in advance."), "request_count":1})
        api_key = secret_store.get_api_key(profile_id)
        if not profile or not api_key:
            raise HTTPException(status_code=404, detail="模型配置或 API Key 不存在")
        started = time.perf_counter()
        url = profile["base_url"].rstrip("/") + "/chat/completions"
        payload = {
            "model": profile["model"],
            "messages": [
                {"role": "system", "content": "Translate faithfully."},
                {"role": "user", "content": "Translate to Chinese: signal space"},
            ],
            "max_tokens": 32,
        }
        if mode := thinking_setting(profile["base_url"]):
            payload["thinking"] = {"type": mode}
        if not profile["no_send_temperature"]:
            payload["temperature"] = 0
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
                choice = body["choices"][0]
                preview = choice["message"].get("content")
                if choice.get("finish_reason") != "stop" or not preview:
                    raise ValueError("模型返回空内容或截断内容")
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"模型连接测试失败：{exc}"
            ) from exc
        return TestResult(
            usage={"usage_kind":"tokens", "request_count":1, "total_tokens":body.get("usage", {}).get("total_tokens")},
            ok=True,
            model=profile["model"],
            response_preview=str(preview)[:200],
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    submission_lock = asyncio.Lock()

    @app.post("/v1/jobs", response_model=JobView, dependencies=[Depends(authorize)])
    async def create_job(
        file: Annotated[UploadFile, File()],
        profile_id: Annotated[str, Form()],
        source_title: Annotated[str, Form()],
        pages: Annotated[str | None, Form()] = None,
        options_json: Annotated[str, Form()] = "{}",
        client_request_id: Annotated[str | None, Form()] = None,
        metadata_json: Annotated[str, Form()] = "{}",
    ) -> JobView:
        async with submission_lock:
            return await create_job_once(file, profile_id, source_title, pages, options_json, client_request_id, metadata_json)

    async def create_job_once(file, profile_id, source_title, pages, options_json, client_request_id, metadata_json="{}") -> JobView:
        if client_request_id is not None and not re.fullmatch(r"[a-f0-9]{32}", client_request_id):
            raise HTTPException(status_code=400, detail="任务请求 ID 格式无效")
        if not database.get_profile(profile_id):
            raise HTTPException(status_code=404, detail="模型配置不存在")
        if database.get_profile(profile_id).get("provider", "openai") == "openai" and not secret_store.get_api_key(profile_id):
            raise HTTPException(status_code=400, detail="模型配置没有 API Key")
        try:
            options = json.loads(options_json)
            if not isinstance(options, dict):
                raise TypeError
            options = TranslationOptions.model_validate(options).model_dump()
            metadata = json.loads(metadata_json)
            if not isinstance(metadata, dict) or len(metadata_json) > 20000:
                raise TypeError
            metadata = {k: str(v)[:1000] for k, v in metadata.items() if k in {"title", "authors_year", "filename", "attachment_id", "library_id", "parent_id", "batch_id", "citation_label"}}
        except (json.JSONDecodeError, TypeError, ValidationError) as exc:
            raise HTTPException(
                status_code=400, detail="翻译选项格式或范围无效"
            ) from exc
        from .languages import capabilities
        provider = database.get_profile(profile_id).get("provider", "openai")
        if not capabilities()["providers"][provider].get(options["target_language"], {}).get("supported"):
            raise HTTPException(status_code=400, detail="该免费服务的目标语言未支持／未验收；请手动选择已验收语言或模型")
        job_id = client_request_id or uuid.uuid4().hex
        existing = database.get_job(job_id)
        if existing:
            try:
                old_pages = existing["pages"] if existing.get("cleanup_state") else parse_pages(existing["pages"], validate_pdf(Path(existing["source_path"])))
                new_pages = pages if existing.get("cleanup_state") else parse_pages(pages, validate_pdf(Path(existing["source_path"])))
            except ValueError as exc:
                raise HTTPException(status_code=409, detail="请求 ID 已对应另一任务") from exc
            if (existing["profile_id"] != profile_id or old_pages != new_pages
                    or TranslationOptions.model_validate_json(existing["options_json"]).model_dump() != options):
                raise HTTPException(status_code=409, detail="请求 ID 已对应另一任务，请恢复原任务")
            # A repeated request ID always refers to its original PDF, even if the
            # local attachment changed after the first upload; never enqueue again.
            return job_view(existing)
        job_dir = settings.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        source_path = job_dir / "source.pdf"
        digest = hashlib.sha256()
        size = 0
        with source_path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 1024 * 1024 * 1024:
                    raise HTTPException(status_code=413, detail="PDF 超过 1 GiB")
                digest.update(chunk)
                output.write(chunk)
        try:
            page_count = validate_pdf(source_path)
            selected = parse_pages(pages, page_count)
            pages = ",".join(str(i + 1) for i in selected) if pages else None
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"无效 PDF 或页码：{exc}") from exc
        if not options["is_selftest"] and metadata.get("attachment_id"):
            for previous in database.list_jobs(limit=100000, include_hidden=True):
                old_metadata = json.loads(previous.get("metadata_json") or "{}")
                snapshot = json.loads(previous.get("profile_snapshot_json") or "{}")
                current_profile = database.get_profile(profile_id)
                if (previous["source_sha256"] == digest.hexdigest() and previous["profile_id"] == profile_id
                    and all(snapshot.get(k) == current_profile.get(k) for k in ("provider", "model", "base_url"))
                    and previous["pages"] == (pages or None) and old_metadata.get("attachment_id") == metadata["attachment_id"]
                    and old_metadata.get("library_id") == metadata.get("library_id")
                    and TranslationOptions.model_validate_json(previous["options_json"]).model_dump() == options):
                    import shutil
                    shutil.rmtree(job_dir)
                    return job_view(previous)
        database.create_job(
            {
                "id": job_id,
                "status": JobStatus.QUEUED,
                "source_title": source_title[:500],
                "source_sha256": digest.hexdigest(),
                "source_path": str(source_path),
                "profile_id": profile_id,
                "pages": pages or None,
                "stage": "queued",
                "options": options,
                "metadata": {"filename": file.filename or "source.pdf", **metadata},
            }
        )
        await runner.enqueue(job_id)
        return job_view(database.get_job(job_id))

    @app.get("/v1/jobs", response_model=list[JobView], dependencies=[Depends(authorize)])
    async def list_jobs(limit: int = 50, offset: int = 0, include_hidden: bool = False, search: str = "", status: str = "") -> list[JobView]:
        return [job_view(row) for row in database.list_jobs(min(200, max(1, limit)), max(0, offset), include_hidden, search[:500], status)]

    @app.get("/v1/capabilities", dependencies=[Depends(authorize)])
    async def get_capabilities():
        from .languages import capabilities
        return capabilities()

    @app.get("/v1/scheduler", dependencies=[Depends(authorize)])
    async def get_scheduler():
        return {"max_parallel": runner.max_parallel, "counts": database.counts()}

    @app.post("/v1/scheduler", dependencies=[Depends(authorize)])
    async def set_scheduler(request: dict):
        try:
            database.set_parallel_limit(request.get("max_parallel"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return await get_scheduler()

    @app.post("/v1/jobs/{job_id}/history", response_model=JobView, dependencies=[Depends(authorize)])
    async def hide_history(job_id: str, request: dict):
        row = database.get_job(job_id)
        if not row:
            raise HTTPException(status_code=404, detail="任务不存在")
        hidden = request.get("hidden")
        if type(hidden) is not bool:
            raise HTTPException(status_code=400, detail="hidden 必须为布尔值")
        if hidden and row["status"] not in {"completed", "failed", "cancelled", "needs_review"}:
            raise HTTPException(status_code=409, detail="运行、排队和暂停任务不能直接移除")
        if hidden and TranslationOptions.model_validate_json(row["options_json"]).is_selftest:
            from .test_cleanup import cleanup_test
            await asyncio.to_thread(cleanup_test, database, settings.jobs_dir, row)
        database.update_job(job_id, hidden=int(hidden))
        return job_view(database.get_job(job_id))

    @app.post("/v1/jobs/{job_id}/cleanup-test", dependencies=[Depends(authorize)])
    async def cleanup_test_files(job_id: str, request: dict):
        row = database.get_job(job_id)
        if not row:
            raise HTTPException(status_code=404, detail="任务不存在")
        if request.get("viewer_copy_opened") is not True:
            raise HTTPException(status_code=400, detail="必须先成功复制并打开测试 PDF")
        from .test_cleanup import cleanup_test
        try:
            return {"cleanup_state": await asyncio.to_thread(cleanup_test, database, settings.jobs_dir, row)}
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/profiles/{profile_id}/selftest", response_model=JobView, dependencies=[Depends(authorize)])
    async def full_selftest(profile_id: str, request: dict) -> JobView:
        # Stable request ID prevents duplicate paid tests when a response is lost.
        request_id = request.get("client_request_id")
        if not isinstance(request_id, str) or not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise HTTPException(status_code=400, detail="完整测试需要唯一请求 ID")
        previous_test = database.get_job(request_id)
        if previous_test:
            if previous_test["profile_id"] != profile_id or not TranslationOptions.model_validate_json(previous_test["options_json"]).is_selftest:
                raise HTTPException(status_code=409, detail="测试请求 ID 已对应另一任务")
            return job_view(previous_test)
        if runtime_root := os.environ.get("ZLB_RUNTIME_ROOT"):
            from .runtime_assets import verify_resources
            try:
                await asyncio.to_thread(verify_resources, Path(runtime_root))
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"资源检查失败：{exc}") from exc
        from .selftest import make_test_pdf
        import io
        test_bytes = make_test_pdf()
        options = TranslationOptions(is_selftest=True, auto_extract_glossary=False,
            ignore_cache=True,
            max_requests=4, max_total_tokens=12000, max_output_tokens=1024,
            free_max_requests=20, max_characters=10000).model_dump()
        async with submission_lock:
            return await create_job_once(UploadFile(file=io.BytesIO(test_bytes), filename="selftest.pdf"),
                profile_id, "内置完整测试（不导入文献库）", "1", json.dumps(options), request_id)

    @app.get(
        "/v1/jobs/{job_id}", response_model=JobView, dependencies=[Depends(authorize)]
    )
    async def get_job(job_id: str) -> JobView:
        row = database.get_job(job_id)
        if not row:
            raise HTTPException(status_code=404, detail="任务不存在")
        if row.get("cleanup_state") == "pending":
            from .test_cleanup import cleanup_test
            await asyncio.to_thread(cleanup_test, database, settings.jobs_dir, row)
            row = database.get_job(job_id)
        return job_view(row)

    @app.post(
        "/v1/jobs/{job_id}/cancel",
        response_model=JobView,
        dependencies=[Depends(authorize)],
    )
    async def cancel_job(job_id: str) -> JobView:
        if not await runner.cancel(job_id):
            raise HTTPException(status_code=409, detail="任务不能取消")
        return job_view(database.get_job(job_id))

    @app.post(
        "/v1/jobs/{job_id}/retry",
        response_model=JobView,
        dependencies=[Depends(authorize)],
    )
    async def retry_job(job_id: str) -> JobView:
        async with submission_lock:
            row = database.get_job(job_id)
            if not row:
                raise HTTPException(status_code=404, detail="任务不存在")
            if row["status"] not in {JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.PAUSED}:
                raise HTTPException(status_code=409, detail="只有失败、取消或暂停任务可以恢复")
            if row.get("cleanup_state"):
                raise HTTPException(status_code=409, detail="测试文件已清理；恢复不会自动重新收费测试")
            view = job_view(row)
            if view.budget and view.budget.can_extend:
                raise HTTPException(status_code=409, detail="此任务已到额度上限，请确认追加额度后继续；普通恢复不增加上限")
            database.update_job(job_id, status=JobStatus.QUEUED, stage="queued", progress=0, error=None)
            await runner.enqueue(job_id)
            return job_view(database.get_job(job_id))

    @app.post("/v1/jobs/{job_id}/extend-budget", response_model=JobView, dependencies=[Depends(authorize)])
    async def extend_budget(job_id: str, request: BudgetExtension) -> JobView:
        if not request.confirmed:
            raise HTTPException(status_code=400, detail="需明确确认继续消耗 API 额度")
        payload = request.model_dump()
        async with submission_lock:
            row = database.get_job(job_id)
            if not row:
                raise HTTPException(status_code=404, detail="任务不存在")
            try:
                if database.budget_approval_matches(job_id, payload):
                    return job_view(row)
                view = job_view(row)
                if not view.budget or not view.budget.can_extend:
                    raise ValueError("只有达到额度上限的普通 API 任务可以追加；完整测试和免费任务不追加付费额度")
                old = view.budget
                if (request.max_total_tokens < old.max_total_tokens or request.max_requests < old.max_requests or
                    (request.max_total_tokens == old.max_total_tokens and request.max_requests == old.max_requests)):
                    raise ValueError("新上限需至少增加一项，且不能降低已有上限")
                metrics = view.metrics
                if metrics.budget_total_tokens is None or metrics.request_count is None:
                    raise ValueError("累计用量不可读，不能安全追加；请保留原审计")
                needed = max(metrics.budget_total_tokens, metrics.required_budget_tokens or 0)
                if request.max_total_tokens < needed or request.max_requests <= metrics.request_count:
                    raise ValueError(f"新上限不足：tokens至少{needed}，请求次数需大于{metrics.request_count}")
                changed = database.extend_budget(job_id, payload)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            if changed:
                await runner.enqueue(job_id)
            return job_view(database.get_job(job_id))

    @app.get(
        "/v1/jobs/{job_id}/artifacts/{name}",
        dependencies=[Depends(authorize)],
    )
    async def get_artifact(job_id: str, name: str) -> FileResponse:
        row = database.get_job(job_id)
        if not row:
            raise HTTPException(status_code=404, detail="任务不存在")
        allowed = {
            Path(path).name: Path(path)
            for path in (row["artifact_path"], row["report_path"])
            if path
        }
        allowed.update({a["name"]: Path(a["path"]) for a in json.loads(row.get("artifacts_json") or "[]")})
        path = allowed.get(name)
        if not path or not path.is_file():
            raise HTTPException(status_code=404, detail="产物不存在")
        media = (
            "application/pdf" if path.suffix.lower() == ".pdf" else "application/json"
        )
        return FileResponse(path, media_type=media, filename=name)

    return app


app = create_app()
