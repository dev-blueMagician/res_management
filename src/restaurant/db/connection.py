"""SQLite connection handling and schema migrations."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_DIR = Path(__file__).parent / "schema"
_MIGRATION_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open the database, enforce foreign keys and bring the schema up to date.

    Pass ``":memory:"`` for an isolated database in tests.
    """
    conn = sqlite3.connect(str(path), isolation_level=None)  # explicit transactions only
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if str(path) != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection, target: int | None = None) -> None:
    """Apply every numbered migration newer than the database's user_version.

    ``target`` stops at that version, to see what an older database should look like.
    """
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    migrations = sorted(
        (int(match.group(1)), file)
        for file in SCHEMA_DIR.glob("*.sql")
        if (match := _MIGRATION_NAME.match(file.name))
    )
    latest = migrations[-1][0] if migrations else 0
    if current > latest:
        raise RuntimeError(
            f"Database schema version {current} is newer than this app supports ({latest})."
        )
    for version, file in migrations:
        if version <= current or (target is not None and version > target):
            continue
        script = file.read_text(encoding="utf-8")
        # executescript commits first, so the BEGIN/COMMIT live in the script
        # itself. A failing statement leaves the transaction open: roll it back.
        try:
            conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {version};\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block atomically: commit on success, roll back on any exception.

    Services use this for every write. Nesting is not supported, so a service
    that opens a transaction must not call another service that also opens one.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
