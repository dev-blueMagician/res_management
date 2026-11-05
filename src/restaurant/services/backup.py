"""Database backups (NFR-6). A backup is a standalone copy of the whole database."""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
from contextlib import closing, suppress
from datetime import datetime
from pathlib import Path

from restaurant.core import Actor, ConflictError, Permission, ValidationError, require
from restaurant.db.connection import connect, migrate, transaction
from restaurant.services import audit, auth, settings

DEFAULT_KEEP = 7
_BACKUP_PREFIX = "restaurant-backup"
_SAFETY_PREFIX = "restaurant-pre-restore"
_BACKUP_NAME = re.compile(rf"{_BACKUP_PREFIX}-(\d{{8}}-\d{{6}})(?:-(\d+))?\.db")
_SIDECAR_SUFFIXES = ("-wal", "-shm", "-journal")
_NOT_INTERNAL = "WHERE name NOT LIKE 'sqlite~_%' ESCAPE '~'"  # sqlite_stat1 and auto-indexes
_INVALID_BACKUP = "That file is not a valid restaurant backup."
_NO_MANAGER = (
    "That backup has no active manager who could sign in, so the app could not be "
    "administered after restoring it."
)


def create_backup(
    conn: sqlite3.Connection, actor: Actor, backup_dir: str | Path, keep: int = DEFAULT_KEEP
) -> dict:
    """Back up on demand, then keep only the newest ``keep`` backups in the folder."""
    require(actor, Permission.MANAGE_SETTINGS)
    path = _write_backup(conn, Path(backup_dir), keep)
    with transaction(conn):
        audit.record(conn, actor.id, "backup.created", path.name)
    return {"path": str(path)}


def auto_backup(
    conn: sqlite3.Connection, backup_dir: str | Path, keep: int = DEFAULT_KEEP
) -> dict:
    """Back up on launch, before anyone has signed in."""
    return {"path": str(_write_backup(conn, Path(backup_dir), keep))}


def restore_backup(db_path: str | Path, backup_path: str | Path, actor: Actor) -> dict:
    """Replace the database with a backup. The caller has already closed its connection.

    All the work happens on a private copy of the backup, so what was validated
    is exactly what gets installed, and any failure leaves the database as it was.
    The caller should reconnect and sign everyone out: the staff list has changed.
    """
    require(actor, Permission.MANAGE_SETTINGS)
    db = Path(db_path)
    source = Path(backup_path)
    temp = db.with_name(db.name + ".restore-tmp")
    try:
        _remove_sidecars(temp)
        try:
            shutil.copyfile(source, temp)
        except OSError as exc:
            raise ValidationError("That backup file could not be read.") from exc
        _prepare_restored_copy(temp, source.name, actor)
        safety = _make_safety_copy(db)
        _install(temp, db)
    finally:
        temp.unlink(missing_ok=True)
        _remove_sidecars(temp)
    return {"restored_from": str(source), "safety_copy": str(safety) if safety else None}


def _write_backup(conn: sqlite3.Connection, directory: Path, keep: int) -> Path:
    temp = directory / f".{_BACKUP_PREFIX}.tmp"  # no partial file can ever match the pattern
    try:
        directory.mkdir(parents=True, exist_ok=True)
        temp.unlink(missing_ok=True)
        _copy_database(conn, temp)
        path = _unused_name(directory, _BACKUP_PREFIX)
        os.replace(temp, path)
    except (OSError, sqlite3.Error) as exc:
        with suppress(OSError):
            temp.unlink(missing_ok=True)
        raise ValidationError(f"The backup could not be saved in {directory}.") from exc
    _prune(directory, keep, path)
    return path


def _copy_database(source: sqlite3.Connection, destination: Path) -> None:
    # The backup API reads through the connection, so WAL contents are included.
    with closing(sqlite3.connect(destination)) as copy:
        source.backup(copy)
        copy.execute("PRAGMA journal_mode = DELETE")  # one self-contained file


def _unused_name(directory: Path, prefix: str) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")  # local time: staff read these names
    path = directory / f"{prefix}-{stamp}.db"
    number = 0
    while path.exists():
        number += 1
        path = directory / f"{prefix}-{stamp}-{number}.db"
    return path


def _prune(directory: Path, keep: int, newest: Path) -> None:
    """Delete old backups, only ever files named like ours, and never the one just made.

    ``newest`` is excluded by name because a clock set back would otherwise sort
    it behind older files and delete it straight away.
    """
    backups = []
    for path in directory.iterdir():
        match = _BACKUP_NAME.fullmatch(path.name)
        if match and path != newest and path.is_file():
            backups.append((match[1], int(match[2] or 0), path))
    backups.sort(reverse=True)
    for _, _, path in backups[max(keep - 1, 0) :]:
        with suppress(OSError):  # in use, perhaps: the backup succeeded, the next prune retries
            path.unlink()


def _latest_schema_version() -> int:
    with closing(connect(":memory:")) as probe:
        return probe.execute("PRAGMA user_version").fetchone()[0]


def _prepare_restored_copy(path: Path, source_name: str, actor: Actor) -> None:
    """Validate the private copy, bring its schema up to date and note the restore in it."""
    try:
        with closing(sqlite3.connect(path, isolation_level=None)) as conn:
            _validate(conn)
            conn.execute("PRAGMA journal_mode = DELETE")  # one self-contained file to install
            conn.execute("PRAGMA foreign_keys = ON")
            migrate(conn)
            # The signed-in user may not exist in an older backup, or be someone else there.
            same_person = conn.execute(
                "SELECT 1 FROM staff WHERE id = ? AND username = ?", (actor.id, actor.username)
            ).fetchone()
            with transaction(conn):
                audit.record(
                    conn,
                    actor.id if same_person else None,
                    "backup.restored",
                    f"{source_name}, by {actor.username}",
                )
    except sqlite3.Error as exc:
        raise ValidationError(_INVALID_BACKUP) from exc


def _validate(conn: sqlite3.Connection) -> None:
    """Refuse anything the app could not run on, or that would run someone else's rules.

    The schema must be exactly what this version's migrations create, with no other
    trigger, table or index. Only then is the file's content worth querying.
    """
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > _latest_schema_version():
        raise ValidationError("That backup was made by a newer version of this app.")
    if version < 1 or _schema_of(conn) != _expected_schema(version):
        raise ValidationError(_INVALID_BACKUP)
    # integrity_check also re-tests every CHECK and NOT NULL rule against the stored rows.
    if (
        conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
        or conn.execute("PRAGMA foreign_key_check").fetchone() is not None
        or _has_wrongly_typed_values(conn)
        or _on_hand_differs_from_ledger(conn)
    ):
        raise ValidationError(_INVALID_BACKUP)
    stored = dict(conn.execute("SELECT key, value FROM settings").fetchall())
    try:
        settings.check_stored(stored)
    except ValidationError as exc:
        raise ValidationError(_INVALID_BACKUP) from exc
    _require_a_manager_who_can_sign_in(conn)


def _schema_of(conn: sqlite3.Connection) -> set[tuple]:
    return set(conn.execute(f"SELECT type, name, tbl_name, sql FROM sqlite_master {_NOT_INTERNAL}"))


def _expected_schema(version: int) -> set[tuple]:
    with closing(sqlite3.connect(":memory:", isolation_level=None)) as reference:
        migrate(reference, target=version)
        return _schema_of(reference)


def _has_wrongly_typed_values(conn: sqlite3.Connection) -> bool:
    """SQLite stores any value in any column, so check each holds its declared type."""
    # These names were just matched against our own schema, so they are safe to quote.
    tables = conn.execute(f"SELECT name FROM sqlite_master {_NOT_INTERNAL} AND type = 'table'")
    for (table,) in tables.fetchall():
        columns = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
        for _cid, column, declared, *_rest in columns:
            wrong = conn.execute(
                f"""SELECT 1 FROM "{table}" WHERE typeof("{column}") NOT IN (?, 'null') LIMIT 1""",
                (declared.lower(),),
            ).fetchone()
            if wrong:
                return True
    return False


def _on_hand_differs_from_ledger(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM ingredients i WHERE i.on_hand_milli <> "
            "(SELECT COALESCE(SUM(m.delta_milli), 0) FROM stock_movements m "
            "WHERE m.ingredient_id = i.id) LIMIT 1"
        ).fetchone()
        is not None
    )


def _require_a_manager_who_can_sign_in(conn: sqlite3.Connection) -> None:
    hashes = conn.execute(
        "SELECT password_hash FROM staff WHERE role = 'manager' AND active = 1"
    ).fetchall()
    if not any(auth.is_well_formed_hash(stored) for (stored,) in hashes):
        raise ValidationError(_NO_MANAGER)


def _make_safety_copy(db: Path) -> Path | None:
    """Copy the current database beside it. Pruning never touches these files."""
    if not db.exists():
        return None
    safety = _unused_name(db.parent, _SAFETY_PREFIX)
    try:
        try:
            with closing(sqlite3.connect(db)) as current:
                _copy_database(current, safety)
        except sqlite3.Error:
            # A damaged database is often why someone restores: keep its raw files.
            safety.unlink(missing_ok=True)
            shutil.copy2(db, safety)
            wal = _sidecar(db, "-wal")
            if wal.exists():
                shutil.copy2(wal, _sidecar(safety, "-wal"))
    except OSError as exc:
        raise ConflictError(
            "A safety copy of the current database could not be made, so nothing was restored."
        ) from exc
    return safety


def _install(temp: Path, db: Path) -> None:
    # Sidecars go first: a -wal left beside the restored file would be replayed into it.
    # The safety copy already holds anything still in the WAL.
    try:
        with open(temp, "rb+") as file:
            os.fsync(file.fileno())  # on disk before it takes the database's place
        _remove_sidecars(db)
        os.replace(temp, db)
    except OSError as exc:
        raise ConflictError(
            "The database is still in use, so nothing was restored. Close it and try again."
        ) from exc


def _sidecar(db: Path, suffix: str) -> Path:
    return db.with_name(db.name + suffix)


def _remove_sidecars(db: Path) -> None:
    for suffix in _SIDECAR_SUFFIXES:
        _sidecar(db, suffix).unlink(missing_ok=True)
