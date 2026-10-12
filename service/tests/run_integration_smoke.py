from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pymupdf
from fastapi.testclient import TestClient
from zotero_local_bilingual.app import create_app
from zotero_local_bilingual.config import Settings


class MemorySecretStore:
    def __init__(self):
        self.values: dict[str, str] = {}

    def set_api_key(self, profile_id: str, api_key: str) -> None:
        self.values[profile_id] = api_key

    def get_api_key(self, profile_id: str) -> str | None:
        return self.values.get(profile_id)


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    runtime = project_root / "runtime" / "jobrunner-smoke"
    if runtime.exists():
        shutil.rmtree(runtime)
    settings = Settings(data_dir=runtime)
    secrets = MemorySecretStore()
    token = settings.get_or_create_service_token()
    app = create_app(settings, secrets)
    headers = {"X-Local-Bilingual-Token": token}
    source = project_root.parent / "讲义" / "MIT6.450_2006_Ch01_数字通信导论_EN.pdf"

    with TestClient(app) as client:
        profile = client.post(
            "/v1/profiles",
            headers=headers,
            json={
                "id": "mock",
                "name": "Mock integration",
                "base_url": "http://127.0.0.1:17867/v1",
                "model": "mock-integration",
                "api_key": "test-only-key",
                "qps": 2,
            },
        )
        profile.raise_for_status()
        with source.open("rb") as stream:
            response = client.post(
                "/v1/jobs",
                headers=headers,
                files={"file": ("source.pdf", stream, "application/pdf")},
                data={
                    "profile_id": "mock",
                    "source_title": "MIT 6.450 Chapter 1 integration smoke",
                    "pages": "1",
                    "options_json": json.dumps({"ignore_cache": True}),
                },
            )
        response.raise_for_status()
        job = response.json()
        deadline = time.monotonic() + 120
        while job["status"] in {"queued", "running", "cancelling"}:
            if time.monotonic() > deadline:
                raise TimeoutError(f"Job timed out: {job}")
            time.sleep(0.5)
            job_response = client.get(f"/v1/jobs/{job['id']}", headers=headers)
            job_response.raise_for_status()
            job = job_response.json()
        if job["status"] != "completed":
            raise RuntimeError(f"Job failed: {job}")
        artifact = client.get(
            f"/v1/jobs/{job['id']}/artifacts/{job['artifact_name']}",
            headers=headers,
        )
        artifact.raise_for_status()
        result_path = runtime / "downloaded-bilingual.pdf"
        result_path.write_bytes(artifact.content)
        with pymupdf.open(result_path) as document:
            if document.page_count != 14 or document[0].rect.width != 1236:
                raise RuntimeError("Unexpected bilingual PDF geometry")
        print(json.dumps(job, ensure_ascii=False, indent=2))
        print(result_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
