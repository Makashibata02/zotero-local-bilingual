"""Merge hash-verified platform manifests before building the shared XPI."""
from pathlib import Path
import argparse
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]
PLATFORMS={"windows-x64", "macos-arm64", "macos-x64"}

def collect(release, version, required):
    engines={}
    for path in sorted(release.glob("engine-manifest-*.json")):
        manifest=json.loads(path.read_text("utf8")); platform=manifest["platform"]
        assert platform in PLATFORMS and platform not in engines
        assert manifest["version"]==version and manifest["protocol_version"]==2
        assert manifest["filename"]==f"zlb-engine-{version}-{platform}.zip"
        archive=release/manifest["filename"]
        assert archive.stat().st_size==manifest["size"]
        with archive.open("rb") as stream: assert hashlib.file_digest(stream,"sha256").hexdigest()==manifest["sha256"]
        engines[platform]=manifest
    if not required.issubset(engines): raise ValueError("Missing required platforms: "+str(required-set(engines)))
    return {"version":version,"protocol_version":2,"engines":engines}

def main():
    parser=argparse.ArgumentParser();parser.add_argument("--require",nargs="+",default=sorted(PLATFORMS));args=parser.parse_args()
    version=json.loads((ROOT/"plugin/addon/manifest.json").read_text("utf8"))["version"]
    release=ROOT/"dist"/f"release-{version}"
    catalog=collect(release,version,set(args.require))
    for path in (release/"engine-manifest.json",ROOT/"plugin/addon/content/engine-manifest.json"):
        path.write_text(json.dumps(catalog,indent=2)+"\n","utf8")
    print(json.dumps({"version":version,"platforms":list(catalog["engines"])}))

if __name__=="__main__":main()
