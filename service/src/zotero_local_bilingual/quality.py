from __future__ import annotations
import json
from pathlib import Path


def audit_tracking(working_dir: Path) -> dict:
    result = {"tracking_files": 0, "paragraphs": 0, "empty_paragraphs": 0,
              "note": "Checks extracted paragraphs only; not proof of complete PDF extraction or layout quality."}
    def walk(value):
        if isinstance(value, dict):
            if "pdf_unicode" in value and "input" in value:
                result["paragraphs"] += 1
                if str(value.get("input") or "").strip() and not str(value.get("output") or "").strip():
                    result["empty_paragraphs"] += 1
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    for path in working_dir.rglob("translate_tracking.json"):
        result["tracking_files"] += 1
        walk(json.loads(path.read_text("utf8")))
    return result
