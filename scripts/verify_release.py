"""Verify platform archives, resources and the shared XPI without translation."""
from pathlib import Path
import hashlib
import json
import re
import stat
import zipfile
from repository_files import source_files

ROOT=Path(__file__).resolve().parents[1]

def digest(path):
    with path.open("rb") as stream:return hashlib.file_digest(stream,"sha256").hexdigest()

def main():
    requirements=(ROOT/"runtime-requirements.txt").read_text("utf8")
    assert not re.search(r"--output-file\s+[A-Za-z]:[\\/]",requirements)
    catalog=json.loads((ROOT/"plugin/addon/content/engine-manifest.json").read_text("utf8"))
    version=catalog["version"];release=ROOT/"dist"/f"release-{version}"
    results=[]
    for manifest in catalog.get("engines",{catalog.get("platform"):catalog}).values():
        engine=release/manifest["filename"];platform=manifest["platform"]
        assert engine.stat().st_size==manifest["size"] and digest(engine)==manifest["sha256"]
        site="python/Lib/site-packages/" if platform=="windows-x64" else "python/lib/python3.12/site-packages/"
        with zipfile.ZipFile(engine) as archive:
            assert archive.testzip() is None
            runtime=json.loads(archive.read("runtime.json"))
            assert runtime["version"]==version and runtime["platform"]==platform and runtime["protocol_version"]==2
            resources=json.loads(archive.read("assets/resources.json"))["files"]
            for row in resources:
                data=archive.read("assets/"+row["path"])
                assert len(data)==row["size"] and hashlib.sha256(data).hexdigest()==row["sha256"]
            for source in (ROOT/"service/src/zotero_local_bilingual").rglob("*"):
                if source.is_file() and source.suffix in {".py",".json"}:
                    assert archive.read(site+source.relative_to(ROOT/"service/src").as_posix())==source.read_bytes()
            for name in ("uv.lock","runtime-requirements.txt","LICENSE","THIRD_PARTY.md"):
                assert archive.read("licenses/"+name)==(ROOT/name).read_bytes()
            assert not any(n.endswith((".pyc",".db",".db-wal",".db-shm")) for n in archive.namelist())
            assert not any(stat.S_ISLNK(i.external_attr>>16) for i in archive.infolist())
            if platform.startswith("macos-"):
                interpreter=archive.read("python/bin/python3.12")
                assert interpreter[:4]==b"\xcf\xfa\xed\xfe"
                cpu=int.from_bytes(interpreter[4:8],"little")
                assert cpu=={"macos-arm64":0x100000c,"macos-x64":0x1000007}[platform]
                assert (archive.getinfo("python/bin/python3.12").external_attr>>16)&0o100
            results.append({"platform":platform,"size":manifest["size"],"sha256":manifest["sha256"],"resources":len(resources),"native_verified":runtime.get("native_verified",platform=="windows-x64")})
    xpi=release/f"zotero-local-bilingual-{version}.xpi"
    with zipfile.ZipFile(xpi) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read("manifest.json"))["version"]==version
        assert json.loads(archive.read("content/engine-manifest.json"))==catalog
        assert not any(n.endswith(".map") for n in archive.namelist())
    with zipfile.ZipFile(release/f"local-bilingual-{version}-source.zip") as archive:
        assert archive.testzip() is None
        files={p.relative_to(ROOT).as_posix():p for p in source_files()}
        assert set(archive.namelist())==set(files)
        for name,path in files.items(): assert archive.read(name)==path.read_bytes(),name
    for row in (release/"SHA256SUMS").read_text("utf8").splitlines():
        expected,name=row.split("  ",1);assert digest(release/name)==expected,name
    result={"ok":True,"version":version,"engines":results,"xpi_size":xpi.stat().st_size,"xpi_sha256":digest(xpi)}
    output=ROOT/"output"/f"acceptance-{version.replace('.','')}"/"release-verification.json"
    output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(result,indent=2),"utf8");print(json.dumps(result))

if __name__=="__main__":main()
