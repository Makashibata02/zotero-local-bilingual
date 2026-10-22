from __future__ import annotations
import argparse
import asyncio
import json
import os
from pathlib import Path
import socket
import sys
import time
from .config import Settings
from . import __version__


def main() -> None:
    parser = argparse.ArgumentParser(description="Zotero Local Bilingual service")
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1"])
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--show-token", action="store_true")
    parser.add_argument("--managed", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--data-dir")
    parser.add_argument("--runtime-root")
    parser.add_argument("--idle-seconds", type=int, default=600)
    args = parser.parse_args()
    if os.name != "nt":
        os.umask(0o077)
    if args.data_dir:
        os.environ["ZLB_DATA_DIR"] = args.data_dir
    if args.runtime_root:
        os.environ["ZLB_RUNTIME_ROOT"] = args.runtime_root
    settings = Settings.load()
    settings.prepare()
    if args.show_token:
        print(settings.get_or_create_service_token())
        return
    from .runtime_assets import configure_assets, verify_resources
    configure_assets()
    if args.preflight:
        if not args.runtime_root:
            raise RuntimeError("缺少引擎目录")
        result = verify_resources(Path(args.runtime_root))
        from babeldoc.docvision.doclayout import DocLayoutModel
        from .layout_compat import install_babeldoc_layout_patch
        install_babeldoc_layout_patch()
        DocLayoutModel.load_onnx()
        import tiktoken
        tiktoken.encoding_for_model("gpt-4o")
        print(json.dumps({**result, "version": __version__, "protocol_version": 2}))
        return
    lock = (settings.data_dir / "service.lock").open("a+b")
    if sys.platform == "win32":
        import msvcrt
        if lock.tell() == 0:
            lock.write(b"0"); lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit("本机服务已经启动，未打开第二个任务处理进程")
    else:
        import fcntl
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise SystemExit("本机服务已经启动，未打开第二个任务处理进程") from None
    os.environ["ZLB_MANAGED"] = "1" if args.managed else "0"
    import logging
    logging.basicConfig(filename=settings.logs_dir / "service.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    from .app import create_app
    app = create_app(settings)
    import uvicorn
    descriptor = settings.data_dir / "managed-service.json"
    sock = socket.socket()
    sock.bind(("127.0.0.1", args.port if args.port is not None else (0 if args.managed else settings.port)))
    config = uvicorn.Config(app, log_level="warning", log_config=None)
    server = uvicorn.Server(config)

    async def watch():
        while not server.started:
            await asyncio.sleep(.1)
        if args.managed:
            temp = descriptor.with_suffix(".tmp")
            temp.write_text(json.dumps({"url": f"http://127.0.0.1:{sock.getsockname()[1]}",
                "pid": os.getpid(), "protocol_version": 2, "version": __version__}), "utf8")
            temp.replace(descriptor)
        last_busy = time.monotonic()
        while not server.should_exit:
            await asyncio.sleep(2)
            if app.state.shutdown_requested:
                server.should_exit = True
                continue
            runner = app.state.runner
            if runner.active_jobs or runner.pending or not runner.queue.empty() or app.state.business_requests:
                last_busy = time.monotonic()
            if (args.managed and not runner.active_jobs and not runner.pending and runner.queue.empty() and not app.state.business_requests
                    and time.monotonic() - max(app.state.last_business, last_busy) >= args.idle_seconds):
                server.should_exit = True

    async def serve():
        watcher = asyncio.create_task(watch())
        try:
            await server.serve(sockets=[sock])
        finally:
            watcher.cancel()
            if args.managed and descriptor.exists():
                try:
                    if json.loads(descriptor.read_text("utf8")).get("pid") == os.getpid():
                        descriptor.unlink()
                except (OSError, ValueError):
                    pass
    try:
        asyncio.run(serve())
    finally:
        sock.close()
        lock.close()


if __name__ == "__main__":
    main()
