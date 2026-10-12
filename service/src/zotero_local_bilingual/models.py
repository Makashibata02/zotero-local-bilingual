from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class JobStatus(StrEnum):
    PAUSED = "paused"
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"


class ProfileInput(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9._-]{1,64}$")
    name: str = Field(min_length=1, max_length=100)
    provider: str = Field(default="openai", pattern=r"^(openai|bing|tencent)$")
    base_url: HttpUrl | None = None
    model: str = Field(default="", max_length=200)
    api_key: str | None = Field(default=None, min_length=1, max_length=4096)
    qps: int = Field(default=2, ge=1, le=20)
    no_send_temperature: bool = False
    reasoning: str | None = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def validate_provider(self):
        if self.provider == "openai" and (not self.base_url or not self.model.strip()):
            raise ValueError("大模型模式需要 API 地址和模型名称")
        if self.provider != "openai":
            self.qps = 1
        return self

    @field_validator("base_url")
    @classmethod
    def normalize_base_url(cls, value: HttpUrl) -> HttpUrl:
        return HttpUrl(str(value).rstrip("/")) if value else None


class ProfilePublic(BaseModel):
    provider: str = "openai"
    id: str
    name: str
    base_url: str
    model: str
    qps: int
    no_send_temperature: bool
    reasoning: str | None
    has_api_key: bool


class TestResult(BaseModel):
    usage: dict = Field(default_factory=dict)
    ok: bool
    model: str
    response_preview: str
    latency_ms: int


class JobMetrics(BaseModel):
    usage_kind: str = "tokens"
    characters: int | None = None
    request_count: int | None = None
    total_tokens: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    budget_total_tokens: int | None = None
    unconfirmed_tokens: int | None = None
    elapsed_seconds: float | None = None
    required_budget_tokens: int | None = None


class BudgetExtension(BaseModel):
    confirmed: bool
    client_request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    expected_max_total_tokens: int = Field(ge=5000, le=2000000)
    expected_max_requests: int = Field(ge=1, le=5000)
    max_total_tokens: int = Field(ge=5000, le=2000000)
    max_requests: int = Field(ge=1, le=5000)


class JobBudget(BaseModel):
    max_total_tokens: int
    max_requests: int
    can_extend: bool = False


class JobView(BaseModel):
    provider: str = "openai"
    metadata: dict = Field(default_factory=dict)
    target_language: str = "zh-CN"
    output_mode: str = "dual"
    artifacts: list[dict] = Field(default_factory=list)
    hidden: bool = False
    is_selftest: bool = False
    cleanup_state: str | None = None
    queue_position: int | None = None
    service_name: str = ""
    id: str
    status: JobStatus
    source_title: str
    source_sha256: str
    profile_id: str
    pages: str | None
    stage: str | None
    progress: float
    error: str | None
    artifact_name: str | None
    report_name: str | None
    metrics: JobMetrics | None = None
    budget: JobBudget | None = None
    created_at: datetime
    updated_at: datetime


class HealthResponse(BaseModel):
    deployment_verified: bool = False
    active_jobs: list[str] = Field(default_factory=list)
    max_parallel: int = 1
    protocol_version: int = 2
    managed: bool = False
    runtime_ready: bool = False
    ok: bool
    version: str
    engine: str
    engine_version: str | None
    data_dir: str
    active_job: str | None
