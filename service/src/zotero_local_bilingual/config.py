from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 17865

    @classmethod
    def load(cls) -> Settings:
        default_root = (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
            / "ZoteroLocalBilingual"
        )
        return cls(
            data_dir=Path(os.environ.get("ZLB_DATA_DIR", default_root)),
            host=os.environ.get("ZLB_HOST", "127.0.0.1"),
            port=int(os.environ.get("ZLB_PORT", "17865")),
        )

    def prepare(self) -> None:
        for path in (
            self.data_dir,
            self.jobs_dir,
            self.logs_dir,
            self.cache_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "state.sqlite3"

    @property
    def token_path(self) -> Path:
        return self.data_dir / "service-token.txt"

    def get_or_create_service_token(self) -> str:
        self.prepare()
        if self.token_path.exists():
            token = self.token_path.read_text(encoding="utf-8").strip()
            if token:
                return token
        token = secrets.token_urlsafe(32)
        self.token_path.write_text(token + "\n", encoding="utf-8")
        return token
