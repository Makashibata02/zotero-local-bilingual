from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from .atomic_io import read_json


def job_metrics(row: dict) -> dict:
    if row.get("metrics_json") and row.get("cleanup_state"):
        return json.loads(row["metrics_json"])
    # Expose only numeric counters; never prompts, paths or provider credentials.
    usage = {}
    try:
        working = Path(row["source_path"]).parent / "engine-working" / "api-usage.json"
        if working.exists():
            usage = read_json(working)
        elif row.get("report_path"):
            usage = json.loads(Path(row["report_path"]).read_text("utf8")).get("translation_usage", {})
    except (OSError, ValueError, TypeError):
        pass  # A writer may be between writes; try again at the next poll.
    result = {}
    for key in ["request_count", "total_tokens", "prompt_tokens", "completion_tokens", "budget_total_tokens", "unconfirmed_tokens", "characters", "required_budget_tokens"]:
        value = usage.get(key) if isinstance(usage, dict) else None
        result[key] = value if type(value) is int and value >= 0 else None
    result["usage_kind"] = "characters" if usage.get("usage_kind") == "characters" else "tokens"
    try:
        start = datetime.fromisoformat(row["created_at"])
        end = datetime.now(timezone.utc) if row["status"] in {"queued", "running", "cancelling"} else datetime.fromisoformat(row.get("finished_at") or row["updated_at"])
        result["elapsed_seconds"] = round(max(0, (end - start).total_seconds()), 1)
    except (ValueError, KeyError, TypeError):
        result["elapsed_seconds"] = None
    return result
