"""Background library watcher.

Every ``library.watch_interval`` seconds each enabled library with auto_scan is
checked; stale ones get a scan task, and if ``scrape.auto_on_scan`` is on the
freshly discovered untagged tracks are queued for scraping. This is what makes
"drop a folder in, tags appear later" work without a cron job.
"""

from __future__ import annotations

import threading
import time

from . import db


def _scan_running(library_id: int) -> bool:
    row = db.one(
        "SELECT id FROM tasks WHERE kind = 'scan' AND status IN ('queued','running')"
        " AND payload LIKE ? LIMIT 1",
        (f'%"library_id": {library_id}%',),
    )
    return row is not None


def _due(row) -> bool:
    if not row["last_scan_at"]:
        return True
    elapsed = db.scalar(
        "SELECT strftime('%s','now') - strftime('%s', ?)", (row["last_scan_at"],), 0
    )
    return (elapsed or 0) >= int(db.get_setting("library.watch_interval", "300") or 300)


def tick() -> None:
    from . import scrape, scanner
    from .tasks import tasks

    for row in db.query("SELECT * FROM libraries WHERE enabled = 1 AND auto_scan = 1"):
        if not _due(row) or _scan_running(row["id"]):
            continue
        library_id = row["id"]

        def job(ctx, library_id=library_id):
            result = scanner.scan_library(library_id, ctx)
            if db.get_setting("scrape.auto_on_scan", "0") == "1":
                ids = [r["id"] for r in db.query(
                    "SELECT id FROM tracks WHERE library_id = ? AND missing = 0 AND tagged = 0"
                    " LIMIT 500", (library_id,)
                )]
                if ids:
                    ctx.log(f"auto-scrape queued for {len(ids)} untagged tracks")
                    result["scraped"] = scrape.batch_scrape(ids, None, ctx, only_untagged=True)
            return result

        tasks.start("scan", f"Auto scan: {row['name']}", {"library_id": library_id}, job, None)


def start() -> threading.Thread:
    def loop():
        time.sleep(5)
        while True:
            try:
                tick()
            except Exception as exc:  # noqa: BLE001 - watcher must survive
                print(f"[watcher] {exc}")
            time.sleep(30)

    thread = threading.Thread(target=loop, name="mtw-watcher", daemon=True)
    thread.start()
    return thread


def stop() -> None:
    """Marker for symmetry; daemon threads stop with the process."""
    return None


__all__ = ["start", "stop", "tick"]
