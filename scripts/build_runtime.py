"""Maintainer-only builder. End users never run pip or this script."""
from __future__ import annotations
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile
from sanitize_runtime import sanitize_examples

ROOT = Path(__file__).resolve().parents[1]
VERSION = json.loads((ROOT / "plugin/addon/manifest.json").read_text("utf8"))["version"]


def reuse_engine(base: Path, repository: str) -> None:
    base = base.resolve()
    original = json.loads((base.parent / "engine-manifest.json").read_text("utf8"))
    if "engines" in original:
        original = original["engines"]["windows-x64"]
    if base.name != original["filename"] or base.stat().st_size != original["size"]:
        raise ValueError("Base engine does not match its manifest")
    with base.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != original["sha256"]:
            raise ValueError("Base engine hash mismatch")
    stage = ROOT / "output/runtime-build" / VERSION
    stage.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(base) as archive:
        for entry in archive.infolist():
            if not (stage / entry.filename).resolve().is_relative_to(stage.resolve()):
                raise ValueError("Unsafe archive path")
        if archive.testzip() is not None:
            raise ValueError("Corrupt base engine")
        archive.extractall(stage)
    source = stage / "python/Lib/site-packages/zotero_local_bilingual"
    shutil.copytree(ROOT / "service/src/zotero_local_bilingual", source, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ("LICENSE", "THIRD_PARTY.md", "runtime-requirements.txt", "uv.lock"):
        shutil.copy2(ROOT / name, stage / "licenses" / name)
    for path in stage.rglob("__pycache__"):
        assert path.resolve().is_relative_to(stage.resolve())
        shutil.rmtree(path)
    runtime = json.loads((stage / "runtime.json").read_text("utf8"))
    runtime["version"] = VERSION
    runtime["installed_size"] = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file() and p != stage / "runtime.json")
    (stage / "runtime.json").write_text(json.dumps(runtime, indent=2), "utf8")
    if repository and not __import__("re").fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("repository must be OWNER/REPO")
    filename = f"zlb-engine-{VERSION}-windows-x64.zip"
    target = ROOT / "dist" / f"release-{VERSION}" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    print("Compressing verified reused engine", flush=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(stage).as_posix())
    with target.open("rb") as stream:
        digest = hashlib.file_digest(stream,"sha256").hexdigest()
    manifest = {"version":VERSION,"protocol_version":2,"platform":"windows-x64","filename":filename,
        "url":f"https://github.com/{repository}/releases/download/v{VERSION}/{filename}" if repository else None,
        "size":target.stat().st_size,"installed_size":runtime["installed_size"],"sha256":digest}
    for path in (target.parent / "engine-manifest-windows-x64.json",target.parent / "engine-manifest.json",ROOT / "plugin/addon/content/engine-manifest.json"):
        path.write_text(json.dumps(manifest,indent=2),"utf8")
    print(json.dumps(manifest),flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--reuse-engine", type=Path, help="Repackage a verified previous release without dependency or resource downloads")
    parser.add_argument("--uv", default=str(Path.home() / ".local/bin/uv.exe"))
    parser.add_argument("--skip-dependencies", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    if args.reuse_engine:
        reuse_engine(args.reuse_engine, args.repository)
        return
    if sys.platform != "win32" or sys.maxsize <= 2**32 or sys.version_info[:2] != (3, 12):
        raise RuntimeError("Windows 引擎必须使用 x64 Python 3.12 构建")
    stage = ROOT / "output/runtime-build" / VERSION
    python = stage / "python/python.exe"
    assert stage.resolve().is_relative_to((ROOT / "output/runtime-build").resolve())
    stage.mkdir(parents=True, exist_ok=True)
    if not python.exists():
        print("Copying relocatable Python", flush=True)
        shutil.copytree(sys.base_prefix, stage / "python", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if not args.skip_dependencies:
        subprocess.run([args.uv, "export", "--locked", "--no-dev", "--no-emit-project", "--format", "requirements-txt",
            "--output-file", "runtime-requirements.txt", "--quiet"], cwd=ROOT, check=True, stdout=subprocess.DEVNULL)
        print("Installing hash-locked production wheels in staging", flush=True)
        subprocess.run([args.uv, "pip", "sync", "--python", str(python), "--break-system-packages", "--require-hashes",
            str(ROOT / "runtime-requirements.txt")], cwd=ROOT, check=True)
    site = stage / "python/Lib/site-packages"
    shutil.copytree(ROOT / "service/src/zotero_local_bilingual", site / "zotero_local_bilingual", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    sanitize_examples(site)
    from babeldoc.assets.assets import warmup
    print("Preparing all BabelDOC assets", flush=True)
    warmup()
    # Tokenizer files are downloaded only by their first use.
    import tiktoken
    tiktoken.encoding_for_model("gpt-4o")
    assets = stage / "assets"
    from babeldoc.const import CACHE_FOLDER
    for name in ("fonts", "models", "cmap", "tiktoken"):
        shutil.copytree(CACHE_FOLDER / name, assets / name, dirs_exist_ok=True)
    # Never ship translation/user cache produced during staging smoke tests.
    for cache_file in assets.glob("cache*.db*"):
        assert cache_file.resolve().is_relative_to(stage.resolve())
        cache_file.unlink()
    rows = [{"path": str(path.relative_to(assets)).replace("\\", "/"), "size": path.stat().st_size,
        "sha256": hashlib.file_digest(path.open("rb"), "sha256").hexdigest()}
        for path in sorted(assets.rglob("*")) if path.is_file() and path.name != "resources.json"]
    (assets / "resources.json").write_text(json.dumps({"files": rows}, indent=2), "utf8")
    licenses = stage / "licenses"
    licenses.mkdir(exist_ok=True)
    for name in ("LICENSE", "THIRD_PARTY.md", "runtime-requirements.txt", "uv.lock"):
        if (ROOT / name).is_file():
            shutil.copy2(ROOT / name, licenses / name)
    shutil.copy2(ROOT / "docs/font-license-inventory.json", licenses / "font-license-inventory.json")
    # Console launchers and dependency unit-test suites are not used by the worker.
    prune = [p for p in site.rglob("*") if p.is_dir() and p.name in {"tests", "test"}]
    for path in sorted(prune, key=lambda p: len(str(p)), reverse=True):
        assert path.resolve().is_relative_to(stage.resolve())
        if path.exists():
            shutil.rmtree(path)
    scripts = stage / "python/Scripts"
    if scripts.exists():
        assert scripts.resolve().is_relative_to(stage.resolve())
        shutil.rmtree(scripts)
    # Preserve dependency dist-info/licenses. Drop only development interpreter tools.
    for path in sorted(stage.rglob("__pycache__"), reverse=True):
        shutil.rmtree(path)
    for path in stage.rglob("*.pyc"):
        path.unlink()
    size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file())
    inventory = subprocess.check_output([str(python), "-B", "-c",
        "import importlib.metadata,json; print(json.dumps(sorted([(d.metadata['Name'],d.version) for d in importlib.metadata.distributions()])))"], text=True)
    (stage / "runtime.json").write_text(json.dumps({"version": VERSION, "protocol_version": 2,
        "platform": "windows-x64", "python": sys.version.split()[0], "babeldoc": "0.6.4",
        "installed_size": size, "dependencies": json.loads(inventory)}, indent=2), "utf8")
    if args.prepare_only:
        return
    if args.repository:
        import re
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
            raise ValueError("repository must be OWNER/REPO")
        addon = ROOT / "plugin/addon/manifest.json"
        metadata = json.loads(addon.read_text("utf8"))
        metadata["homepage_url"] = f"https://github.com/{args.repository}"
        metadata["applications"]["zotero"]["update_url"] = f"https://github.com/{args.repository}/releases/latest/download/updates.json"
        addon.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", "utf8")
    filename = f"zlb-engine-{VERSION}-windows-x64.zip"
    target = ROOT / "dist" / f"release-{VERSION}" / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    print("Compressing engine", flush=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                output.write(path, str(path.relative_to(stage)))
    manifest = {"version": VERSION, "protocol_version": 2, "platform": "windows-x64", "filename": filename,
        "url": f"https://github.com/{args.repository}/releases/download/v{VERSION}/{filename}" if args.repository else None,
        "size": target.stat().st_size, "installed_size": size,
        "sha256": hashlib.file_digest(target.open("rb"), "sha256").hexdigest()}
    for output in (target.parent / "engine-manifest-windows-x64.json", target.parent / "engine-manifest.json", ROOT / "plugin/addon/content/engine-manifest.json"):
        output.write_text(json.dumps(manifest, indent=2), "utf8")
    print(json.dumps(manifest), flush=True)


if __name__ == "__main__":
    main()
