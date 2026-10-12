"""Configure bundled resources before importing BabelDOC's heavy modules."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path


def configure_assets() -> Path | None:
    root = os.environ.get("ZLB_RUNTIME_ROOT")
    if not root:
        return None  # Developer environment retains its existing asset cache.
    assets = Path(root) / "assets"
    if not (assets / "resources.json").is_file():
        raise RuntimeError("引擎资源清单缺失，请重新导入完整引擎包")
    import babeldoc.const as const
    mutable_cache = Path(os.environ["ZLB_DATA_DIR"]) / "cache"
    mutable_cache.mkdir(parents=True, exist_ok=True)
    # cache.py eagerly opens its database at import. Set the data location first.
    const.CACHE_FOLDER = mutable_cache
    const.TIKTOKEN_CACHE_FOLDER = assets / "tiktoken"
    os.environ["TIKTOKEN_CACHE_DIR"] = str(const.TIKTOKEN_CACHE_FOLDER)
    # Translation cache is mutable user data, never stored in signed resources.
    import babeldoc.translator.cache as cache
    cache.db.close()
    cache.CACHE_FOLDER = mutable_cache
    cache.init_db()
    const.CACHE_FOLDER = assets
    import babeldoc.assets.assets as resource_loader
    async def prevent_download(*args, **kwargs):
        raise RuntimeError("随包资源缺失或损坏，请重新导入完整引擎；不会运行时下载模型")
    resource_loader.download_file = prevent_download
    return assets


def verify_resources(root: Path) -> dict:
    manifest = json.loads((root / "assets/resources.json").read_text("utf8"))
    for row in manifest["files"]:
        path = root / "assets" / row["path"]
        if not path.resolve().is_relative_to((root / "assets").resolve()):
            raise RuntimeError("非法资源路径")
        if not path.is_file() or path.stat().st_size != row["size"]:
            raise RuntimeError(f"资源缺失或大小错误：{row['path']}")
        if hashlib.file_digest(path.open("rb"), "sha256").hexdigest() != row["sha256"]:
            raise RuntimeError(f"资源校验失败：{row['path']}")
    return {"ok": True, "resources": len(manifest["files"])}
