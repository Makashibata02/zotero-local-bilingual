from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Any


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = RLock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def initialize(self) -> None:
        with self._lock, self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    model TEXT NOT NULL,
                    qps INTEGER NOT NULL,
                    no_send_temperature INTEGER NOT NULL DEFAULT 0,
                    reasoning TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    source_title TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    profile_id TEXT NOT NULL,
                    pages TEXT,
                    stage TEXT,
                    progress REAL NOT NULL DEFAULT 0,
                    error TEXT,
                    artifact_path TEXT,
                    report_path TEXT,
                    options_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(profile_id) REFERENCES profiles(id)
                );
                CREATE TABLE IF NOT EXISTS budget_approvals (
                    client_request_id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES jobs(id),
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status);
                CREATE INDEX IF NOT EXISTS jobs_source_idx ON jobs(source_sha256);
                """
            )

            columns = {row[1] for row in db.execute("PRAGMA table_info(profiles)")}
            if "provider" not in columns:
                db.execute("ALTER TABLE profiles ADD COLUMN provider TEXT NOT NULL DEFAULT 'openai'")
            if "deleted_at" not in columns:
                db.execute("ALTER TABLE profiles ADD COLUMN deleted_at TEXT")
            job_columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
            if "profile_snapshot_json" not in job_columns:
                db.execute("ALTER TABLE jobs ADD COLUMN profile_snapshot_json TEXT")
            for row in db.execute("SELECT id,profile_id FROM jobs WHERE profile_snapshot_json IS NULL").fetchall():
                profile = db.execute("SELECT * FROM profiles WHERE id=?", (row["profile_id"],)).fetchone()
                if profile:
                    db.execute("UPDATE jobs SET profile_snapshot_json=? WHERE id=?", (json.dumps(dict(profile)), row["id"]))
            db.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            for column, definition in {"metadata_json": "TEXT NOT NULL DEFAULT '{}'", "artifacts_json": "TEXT NOT NULL DEFAULT '[]'",
                    "hidden": "INTEGER NOT NULL DEFAULT 0", "metrics_json": "TEXT", "cleanup_state": "TEXT", "queued_at": "TEXT", "finished_at": "TEXT"}.items():
                if column not in job_columns:
                    db.execute(f"ALTER TABLE jobs ADD COLUMN {column} {definition}")
            db.execute("UPDATE jobs SET queued_at=created_at WHERE queued_at IS NULL")
            db.execute("UPDATE jobs SET finished_at=updated_at WHERE finished_at IS NULL AND status IN ('completed','failed','cancelled','paused','needs_review')")
            if not db.execute("SELECT 1 FROM metadata WHERE key='migration-020'").fetchone():
                db.execute("UPDATE jobs SET status='paused', stage='paused_after_upgrade', error='升级后已暂停，请在工作台手动恢复；累计预算保留' WHERE status IN ('queued','running')")
                db.execute("UPDATE jobs SET status='cancelled', stage='cancelled' WHERE status='cancelling'")
                db.execute("INSERT INTO metadata VALUES ('migration-020','done')")
            if not db.execute("SELECT 1 FROM metadata WHERE key='migration-030'").fetchone():
                for old_job in db.execute("SELECT id,options_json FROM jobs").fetchall():
                    options = json.loads(old_job["options_json"])
                    for key, default in {"max_total_tokens":60000, "max_requests":100, "free_max_requests":200, "max_characters":100000}.items():
                        options.setdefault(key, default)
                    db.execute("UPDATE jobs SET options_json=? WHERE id=?", (json.dumps(options), old_job["id"]))
                db.execute("UPDATE jobs SET status='paused', stage='paused_after_upgrade', error='升级后已暂停，请手动恢复；累计预算保留' WHERE status IN ('queued','running')")
                db.execute("UPDATE jobs SET status='cancelled',stage='cancelled' WHERE status='cancelling'")
                db.execute("INSERT INTO metadata VALUES ('migration-030','done')")

    def list_jobs(self, limit: int = 100, offset: int = 0, include_hidden: bool = False, search: str = "", status: str = "") -> list[dict]:
        with self._lock, self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT * FROM jobs WHERE (? OR hidden=0)
                AND (?='' OR source_title LIKE ? OR metadata_json LIKE ?) AND (?='' OR status=?)
                ORDER BY CASE status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'cancelling' THEN 2 ELSE 3 END,
                CASE WHEN status='queued' THEN queued_at END ASC, updated_at DESC LIMIT ? OFFSET ?""",
                (include_hidden, search, '%'+search+'%', '%'+search+'%', status, status, limit, offset))]

    def counts(self) -> dict:
        with self.connect() as db:
            return dict(db.execute("SELECT status,COUNT(*) FROM jobs WHERE hidden=0 GROUP BY status").fetchall())

    def parallel_limit(self) -> int:
        with self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='max_parallel'").fetchone()
            return int(row[0]) if row else 1

    def set_parallel_limit(self, value: int) -> None:
        if type(value) is not int or value not in {1, 2}:
            raise ValueError("同时处理上限只能为 1 或 2")
        with self.connect() as db:
            db.execute("INSERT INTO metadata VALUES ('max_parallel',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(value),))

    def profile_in_use(self, profile_id: str) -> bool:
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM jobs WHERE profile_id=? AND (status IN ('queued','running','cancelling') OR (status IN ('paused','failed') AND (stage='budget_limit' OR error LIKE '%达到任务 token 或请求次数上限%'))) LIMIT 1", (profile_id,)).fetchone())

    def profile_delete_reason(self, profile_id: str) -> str | None:
        with self._lock, self.connect() as db:
            rows = db.execute("""SELECT id,status FROM jobs WHERE profile_id=? AND
                (status IN ('queued','running','cancelling','paused') OR
                 (status='failed' AND (stage='budget_limit' OR error LIKE '%达到任务 token 或请求次数上限%')))
                ORDER BY created_at LIMIT 3""", (profile_id,)).fetchall()
        if rows:
            return "配置被排队、运行、暂停或等待追加额度的任务使用，请先结束对应任务：" + ", ".join(row['id'] for row in rows)
        return None

    def default_profile_id(self) -> str | None:
        """None means not yet migrated; an empty string means no default API."""
        with self._lock, self.connect() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='default_api_profile'").fetchone()
        return row[0] if row else None

    def set_default_profile(self, profile_id: str) -> None:
        with self._lock, self.connect() as db:
            db.execute("INSERT INTO metadata VALUES ('default_api_profile',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (profile_id,))

    def mark_profile_deleted(self, profile_id: str, replacement: str) -> None:
        with self._lock, self.connect() as db:
            db.execute("UPDATE profiles SET deleted_at=?,updated_at=? WHERE id=? AND deleted_at IS NULL", (utc_now(), utc_now(), profile_id))
            db.execute("UPDATE metadata SET value=? WHERE key='default_api_profile' AND value=?", (replacement, profile_id))

    def upsert_profile(self, profile: dict[str, Any]) -> None:
        now = utc_now()
        with self._lock, self.connect() as db:
            previous = db.execute("SELECT deleted_at FROM profiles WHERE id=?", (profile["id"],)).fetchone()
            if previous and previous["deleted_at"]:
                raise ValueError("配置已删除，请另存为新配置")
            db.execute(
                """
                INSERT INTO profiles
                    (id, name, base_url, model, qps, no_send_temperature, provider,
                     reasoning, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    provider=excluded.provider,
                    name=excluded.name,
                    base_url=excluded.base_url,
                    model=excluded.model,
                    qps=excluded.qps,
                    no_send_temperature=excluded.no_send_temperature,
                    reasoning=excluded.reasoning,
                    updated_at=excluded.updated_at
                """,
                (
                    profile["id"],
                    profile["name"],
                    profile["base_url"],
                    profile["model"],
                    profile["qps"],
                    int(profile["no_send_temperature"]),
                    profile.get("provider", "openai"),
                    profile["reasoning"],
                    now,
                    now,
                ),
            )

    def get_profile(self, profile_id: str, include_deleted: bool = False) -> dict[str, Any] | None:
        with self._lock, self.connect() as db:
            row = db.execute(
                "SELECT * FROM profiles WHERE id=? AND (? OR deleted_at IS NULL)", (profile_id, include_deleted),
            ).fetchone()
        return dict(row) if row else None

    def list_profiles(self) -> list[dict[str, Any]]:
        with self._lock, self.connect() as db:
            rows = db.execute("SELECT * FROM profiles WHERE deleted_at IS NULL ORDER BY name,id").fetchall()
        return [dict(row) for row in rows]

    def create_job(self, job: dict[str, Any]) -> None:
        now = utc_now()
        with self._lock, self.connect() as db:
            db.execute(
                """
                INSERT INTO jobs
                    (id, status, source_title, source_sha256, source_path,
                     profile_id, pages, stage, progress, options_json,
                     created_at, updated_at, profile_snapshot_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["id"],
                    job["status"],
                    job["source_title"],
                    job["source_sha256"],
                    job["source_path"],
                    job["profile_id"],
                    job.get("pages"),
                    job.get("stage"),
                    job.get("progress", 0),
                    json.dumps(job.get("options", {}), ensure_ascii=False),
                    now,
                    now,
                    json.dumps(self.get_profile(job["profile_id"])),
                ),
            )
            db.execute("UPDATE jobs SET metadata_json=?,queued_at=? WHERE id=?", (json.dumps(job.get("metadata", {}), ensure_ascii=False), now, job["id"]))

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_recoverable_jobs(self) -> list[str]:
        with self._lock, self.connect() as db:
            rows = db.execute(
                "SELECT id FROM jobs WHERE status IN ('queued','running','cancelling') ORDER BY queued_at,created_at"
            ).fetchall()
        return [row["id"] for row in rows]

    def update_job(self, job_id: str, **fields: Any) -> None:
        allowed = {
            "status",
            "stage",
            "progress",
            "error",
            "artifact_path",
            "report_path",
            "artifacts_json", "metadata_json", "hidden", "metrics_json", "cleanup_state",
        }
        values = {key: value for key, value in fields.items() if key in allowed}
        if not values:
            return
        values["updated_at"] = utc_now()
        if values.get("status") == "queued":
            values["queued_at"] = values["updated_at"]
            values["finished_at"] = None
        elif values.get("status") in {"completed", "failed", "cancelled", "paused", "needs_review"}:
            values["finished_at"] = values["updated_at"]
        assignments = ", ".join(f"{key}=?" for key in values)
        with self._lock, self.connect() as db:
            db.execute(
                f"UPDATE jobs SET {assignments} WHERE id=?",
                (*values.values(), job_id),
            )

    def extend_budget(self, job_id: str, approval: dict) -> bool:
        """Commit consent and new lifetime limits once; never touch usage/cache."""
        encoded = json.dumps(approval, sort_keys=True)
        with self._lock, self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT * FROM budget_approvals WHERE client_request_id=?",
                                  (approval["client_request_id"],)).fetchone()
            if previous:
                if previous["job_id"] != job_id or previous["payload_json"] != encoded:
                    raise ValueError("确认 ID 已对应其他追加额度操作")
                return False
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] not in {"failed", "paused"}:
                raise ValueError("任务状态已改变，请刷新工作台")
            options = json.loads(row["options_json"])
            from .translation_policy import TranslationOptions
            current = TranslationOptions.model_validate(options)
            if (current.max_total_tokens != approval["expected_max_total_tokens"] or
                    current.max_requests != approval["expected_max_requests"]):
                raise ValueError("额度已改变，请刷新后重新确认")
            options.update(max_total_tokens=approval["max_total_tokens"], max_requests=approval["max_requests"])
            now = utc_now()
            db.execute("UPDATE jobs SET options_json=?,status='queued',stage='queued',progress=0,error=NULL,finished_at=NULL,updated_at=?,queued_at=? WHERE id=?",
                       (json.dumps(options), now, now, job_id))
            db.execute("INSERT INTO budget_approvals VALUES (?,?,?,?)",
                       (approval["client_request_id"], job_id, encoded, utc_now()))
            return True

    def budget_approval_matches(self, job_id: str, approval: dict) -> bool:
        with self._lock, self.connect() as db:
            row = db.execute("SELECT * FROM budget_approvals WHERE client_request_id=?",
                             (approval["client_request_id"],)).fetchone()
        if not row:
            return False
        if row["job_id"] != job_id or row["payload_json"] != json.dumps(approval, sort_keys=True):
            raise ValueError("确认 ID 已对应其他追加额度操作")
        return True
