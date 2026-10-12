"""A lifetime OS lock prevents a surviving worker and a restarted one overlapping."""
from contextlib import contextmanager
from pathlib import Path
import os

@contextmanager
def worker_lock(working: Path):
    working.mkdir(parents=True, exist_ok=True)
    with (working / ".worker.lock").open("a+b") as stream:
        if stream.seek(0, 2) == 0:
            stream.write(b"0"); stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("该任务已有工作进程，暂不重启，避免重复消耗额度。请等待旧进程结束后手动恢复。") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
