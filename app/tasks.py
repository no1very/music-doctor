"""Background task centre: worker pool, persisted progress, append-only logs."""

from __future__ import annotations

import json
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

from . import db
from .config import config
from .events import publish


class TaskCancelled(Exception):
    pass


class TaskContext:
    def __init__(self, task_id: int) -> None:
        self.task_id = task_id
        self._last_progress = -1.0

    def log(self, message: str, level: str = "info") -> None:
        db.execute(
            "INSERT INTO task_logs(task_id, level, message) VALUES(?, ?, ?)",
            (self.task_id, level, str(message)[:8000]),
        )
        publish("task.log", task_id=self.task_id, level=level, message=str(message))

    def progress(self, done: int, total: int | None = None, message: str | None = None) -> None:
        if total is not None:
            db.execute(
                "UPDATE tasks SET done = ?, total = ?, progress = ? WHERE id = ?",
                (done, total, (done / total * 100) if total else 0, self.task_id),
            )
        else:
            db.execute("UPDATE tasks SET done = ?, progress = ? WHERE id = ?", (done, done, self.task_id))
        publish("task.progress", task_id=self.task_id, done=done, total=total)
        if message:
            self.log(message)

    def check_cancel(self) -> None:
        row = db.one("SELECT status FROM tasks WHERE id = ?", (self.task_id,))
        if row and row["status"] == "canceling":
            raise TaskCancelled()


class TaskManager:
    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max(1, config.workers), thread_name_prefix="mtw-task")
        self._lock = threading.Lock()
        self._running: dict[int, threading.Event] = {}

    def recover(self) -> None:
        db.execute(
            "UPDATE tasks SET status = 'failed', error = 'interrupted by server restart',"
            " finished_at = datetime('now') WHERE status IN ('running', 'queued', 'canceling')"
        )

    def start(self, kind: str, title: str, payload: dict, fn, user_id: int | None = None) -> int:
        cur = db.execute(
            "INSERT INTO tasks(kind, title, status, payload, created_by) VALUES(?, ?, 'queued', ?, ?)",
            (kind, title[:180], json.dumps(payload, ensure_ascii=False), user_id),
        )
        task_id = int(cur.lastrowid)
        publish("task.new", task_id=task_id, kind=kind, title=title)
        self._pool.submit(self._run, task_id, fn)
        return task_id

    def _run(self, task_id: int, fn) -> None:
        ctx = TaskContext(task_id)
        db.execute(
            "UPDATE tasks SET status = 'running', started_at = datetime('now') WHERE id = ?", (task_id,)
        )
        publish("task.status", task_id=task_id, status="running")
        try:
            result = fn(ctx) or {}
            db.execute(
                "UPDATE tasks SET status = 'done', progress = 100, result = ?, finished_at = datetime('now')"
                " WHERE id = ?",
                (json.dumps(result, ensure_ascii=False, default=str), task_id),
            )
            publish("task.status", task_id=task_id, status="done")
        except TaskCancelled:
            db.execute(
                "UPDATE tasks SET status = 'canceled', finished_at = datetime('now') WHERE id = ?",
                (task_id,),
            )
            ctx.log("task canceled by user", "warn")
            publish("task.status", task_id=task_id, status="canceled")
        except Exception as exc:  # noqa: BLE001 - task boundary must not leak
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            db.execute(
                "UPDATE tasks SET status = 'failed', error = ?, finished_at = datetime('now') WHERE id = ?",
                (detail[:2000], task_id),
            )
            ctx.log(detail, "error")
            publish("task.status", task_id=task_id, status="failed", error=detail[:400])

    def cancel(self, task_id: int) -> bool:
        row = db.one("SELECT status FROM tasks WHERE id = ?", (task_id,))
        if not row or row["status"] not in ("queued", "running"):
            return False
        db.execute("UPDATE tasks SET status = 'canceling' WHERE id = ?", (task_id,))
        publish("task.status", task_id=task_id, status="canceling")
        return True

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


tasks = TaskManager()


def task_rows(limit: int = 50, status: str | None = None) -> list[dict]:
    sql = "SELECT id, kind, title, status, progress, total, done, error, created_by, created_at, started_at, finished_at FROM tasks"
    params: tuple = ()
    if status:
        sql += " WHERE status = ?"
        params = (status,)
    sql += " ORDER BY id DESC LIMIT ?"
    rows = db.query(sql, params + (limit,))
    return [dict(row) for row in rows]
