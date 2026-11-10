"""Music-Doctor entry point."""

from __future__ import annotations

import argparse
import getpass
import os
import signal
import sys
import time


def _reset_password(db, username: str, password: str) -> int:
    """Offline password reset (run inside the container or with --data-dir)."""
    from app.security import hash_password, subsonic_password
    from app.secretbox import encrypt

    row = db.one("SELECT id FROM users WHERE lower(username) = lower(?)", (username,))
    if not row:
        print(f"user not found: {username}")
        return 2
    if len(password) < 6:
        print("password must be at least 6 characters")
        return 2
    db.execute(
        "UPDATE users SET password_hash = ?, password_md5 = ?, password_cipher = ? WHERE id = ?",
        (hash_password(password), subsonic_password(password), encrypt(password), row["id"]),
    )
    db.execute("DELETE FROM sessions WHERE user_id = ?", (row["id"],))
    print(f"password updated for {username}; all sessions revoked")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Music-Doctor - music library tagger")
    parser.add_argument("--host", default=None, help="bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=None, help="port (default 8318)")
    parser.add_argument("--data-dir", default=None, help="data directory")
    parser.add_argument("--check", action="store_true", help="initialise and exit")
    parser.add_argument("--reset-password", metavar="USER", default=None,
                        help="reset a user's password, then exit (asks for the new one)")
    parser.add_argument("--password", default=None,
                        help="new password for --reset-password (avoid: shows in ps history)")
    args = parser.parse_args()

    # Must happen before app.config is imported: Config() resolves the path once.
    if args.data_dir:
        os.environ["MTW_DATA_DIR"] = args.data_dir

    from app import db
    from app.config import config

    if args.reset_password:
        if not config.db_path.exists():
            print(f"database not found at {config.db_path}; start the app once first")
            return 2
        password = args.password or os.environ.get("MTW_NEW_PASSWORD")
        if not password:
            password = getpass.getpass(f"new password for {args.reset_password}: ")
            confirm = getpass.getpass("repeat new password: ")
            if password != confirm:
                print("passwords do not match")
                return 2
        return _reset_password(db, args.reset_password, password)

    db.init_db()

    if args.check:
        print(f"database ready at {config.db_path}")
        return 0

    from app.server import serve

    httpd = serve(args.host, args.port)
    stopping = False

    def _stop(*_args):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _stop)
    try:
        signal.signal(signal.SIGTERM, _stop)
    except (AttributeError, ValueError):
        pass
    while not stopping:
        time.sleep(0.5)
    httpd.shutdown()
    from app.tasks import tasks
    tasks.shutdown()
    print("bye")
    return 0


if __name__ == "__main__":
    sys.exit(main())
