from __future__ import annotations

import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 17865

    @classmethod
    def load(cls) -> Settings:
        if sys.platform == "darwin":
            default_root = Path.home() / "Library" / "Application Support" / "ZoteroLocalBilingual"
        elif sys.platform == "win32":
            default_root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "ZoteroLocalBilingual"
        else:
            default_root = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "ZoteroLocalBilingual"
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
            if os.name != "nt":
                path.chmod(0o700)

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
            if os.name != "nt":
                self.token_path.chmod(0o600)
            token = self.token_path.read_text(encoding="utf-8").strip()
            if token:
                return token
        token = secrets.token_urlsafe(32)
        # Exclusive creation also avoids overwriting a concurrently created token.
        try:
            fd = os.open(self.token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            existing = self.token_path.read_text(encoding="utf8").strip()
            if existing:
                return existing
            raise RuntimeError("本机服务令牌为空，请检查应用数据目录") from None
        with os.fdopen(fd, "w", encoding="utf8") as stream:
            stream.write(token + "\n")
        return token
