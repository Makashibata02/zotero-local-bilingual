"""Cross-process request start spacing. Only profile IDs and times are stored."""
import sqlite3
import time

def acquire(path: str | None, profile: str, qps: int) -> None:
    if not path:
        return
    while True:
        with sqlite3.connect(path, timeout=30) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS request_rate (profile TEXT PRIMARY KEY, next_at REAL NOT NULL)")
            row = db.execute("SELECT next_at FROM request_rate WHERE profile=?", (profile,)).fetchone()
            now = time.time()
            wait = max(0, (row[0] if row else 0) - now)
            if wait <= 0:
                db.execute("INSERT INTO request_rate VALUES (?,?) ON CONFLICT(profile) DO UPDATE SET next_at=excluded.next_at",
                           (profile, now + 1 / max(1, qps)))
                return now
        time.sleep(min(wait, 0.25))
