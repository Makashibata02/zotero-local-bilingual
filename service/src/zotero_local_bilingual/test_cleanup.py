"""Remove only a validated selftest job directory; retain a minimal DB record."""
import json
from pathlib import Path
import shutil
from .telemetry import job_metrics

def cleanup_test(database, jobs_root: Path, row: dict) -> str:
    options = json.loads(row["options_json"])
    if not options.get("is_selftest"):
        raise ValueError("只能清理明确标记的测试目录")
    if row["status"] not in {"completed", "failed", "cancelled"}:
        raise ValueError("运行中或暂停任务不能清理")
    target = Path(row["source_path"]).parent.resolve()
    expected = (jobs_root / row["id"]).resolve()
    if target != expected or target.parent != jobs_root.resolve() or target.is_symlink():
        raise ValueError("测试目录边界不符")
    metrics = json.loads(row["metrics_json"]) if row.get("metrics_json") else job_metrics(row)
    database.update_job(row["id"], metrics_json=json.dumps(metrics), cleanup_state="pending")
    try:
        if target.exists():
            # Never follow junctions or symlinks into a shared/attachment directory.
            if any(p.is_symlink() or (hasattr(p, "is_junction") and p.is_junction()) for p in target.rglob("*")):
                raise OSError("测试目录含链接，保留文件")
            shutil.rmtree(target)
    except OSError:
        return "pending"
    database.update_job(row["id"], cleanup_state="cleaned", artifact_path=None,
                        artifacts_json="[]", report_path=None)
    return "cleaned"
