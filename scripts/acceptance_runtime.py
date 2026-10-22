"""Local public-PDF smoke test using the portable runtime and free providers only."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
import uuid
import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--case", choices=["all","rydberg"], default="all")
    parser.add_argument("--paper", type=Path, help="Explicit public PDF input")
    parser.add_argument("--lecture", type=Path, help="Optional lecture PDF, physical page 9")
    args=parser.parse_args()
    if args.case == "rydberg" and not args.paper:
        parser.error("--case rydberg requires --paper")
    version = json.loads((ROOT/"plugin/addon/manifest.json").read_text("utf8"))["version"]
    runtime = ROOT / f"output/runtime-build/{version}"
    data = ROOT / f"output/acceptance-{version.replace(".","")}" / ("portable-author-final-state" if args.case == "rydberg" else "portable-state")
    data.mkdir(parents=True, exist_ok=True)
    env = {k:v for k,v in os.environ.items() if k not in {"PYTHONPATH","PYTHONHOME","VIRTUAL_ENV"}}
    env["PATH"] = str(runtime / "python")
    env["PYTHONUTF8"] = "1"
    process = subprocess.Popen([str(runtime / ("python/python.exe" if os.name == "nt" else "python/bin/python3.12")), "-I", "-m", "zotero_local_bilingual.cli",
        "--managed", "--runtime-root", str(runtime), "--data-dir", str(data), "--idle-seconds", "20"],
        env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        stdout=(data / "stdout.log").open("wb"), stderr=(data / "stderr.log").open("wb"))
    summary = {"paid_api_calls":0,"tests":[]}
    summary_path=ROOT/f'output/acceptance-{version.replace(".","")}'/('author-results.json' if args.case=='rydberg' else 'results.json')
    try:
        descriptor = data / "managed-service.json"
        for _ in range(100):
            if descriptor.exists(): break
            if process.poll() is not None:raise RuntimeError("portable service exited")
            time.sleep(.3)
        connection = json.loads(descriptor.read_text("utf8"))
        client = httpx.Client(base_url=connection["url"],trust_env=False,timeout=30,
            headers={"X-Local-Bilingual-Token":(data/"service-token.txt").read_text().strip()})

        def run(label, provider, source=None, pages=None):
            response=client.post('/v1/profiles',json={"id":provider,"name":provider,"provider":provider})
            response.raise_for_status()
            started=time.monotonic()
            if source is None:
                response=client.post(f'/v1/profiles/{provider}/selftest',json={"client_request_id":uuid.uuid4().hex})
            else:
                with Path(source).open('rb') as stream:
                    response=client.post('/v1/jobs',data={"profile_id":provider,"source_title":label,"pages":pages or "1",
                        "client_request_id":uuid.uuid4().hex},files={"file":("source.pdf",stream,"application/pdf")})
            response.raise_for_status();job=response.json()
            print(label,job["id"],flush=True)
            for _ in range(900):
                if job["status"] not in {"queued","running","cancelling"}:break
                time.sleep(1)
                response=client.get('/v1/jobs/'+job['id']);response.raise_for_status();job=response.json()
            result={"label":label,"provider":provider,"job":job,"seconds":round(time.monotonic()-started,3)}
            if job.get("report_name"):
                result["report"]=client.get(f"/v1/jobs/{job['id']}/artifacts/{job['report_name']}").json()
            summary['tests'].append(result)
            summary_path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),'utf8')
            print(label,job['status'],job.get('metrics'),flush=True)

        if args.case == "all":
            run("builtin-bing","bing")
            run("builtin-tencent","tencent")
        if args.paper:
            run("Public paper page 1","tencent",args.paper,"1")
        if args.case == "all" and args.lecture:
            run("Lecture physical page 9","tencent",args.lecture,"9")
        # Polling must not prevent idle shutdown.
        idle_started=time.monotonic()
        for _ in range(30):
            if process.poll() is not None:break
            try:client.get('/v1/jobs')
            except httpx.HTTPError:pass
            time.sleep(1)
        summary['idle_shutdown_seconds']=round(time.monotonic()-idle_started,2)
        summary['idle_shutdown_passed']=process.poll() is not None
        summary_path.write_text(json.dumps(summary,ensure_ascii=False,indent=2),'utf8')
    finally:
        if process.poll() is None:
            process.terminate();process.wait(timeout=10)


if __name__ == '__main__':main()
