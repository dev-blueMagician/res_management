"""Audit log (FR-S7). Other services call record() inside their own transaction."""

from __future__ import annotations

import sqlite3

from restaurant.core import Actor, Permission, require, utc_now


def record(
    conn: sqlite3.Connection, staff_id: int | None, action: str, details: str = ""
) -> None:
    """Add an audit entry. Never include passwords or password hashes in ``details``.

    This does not open a transaction, so it joins the caller's and rolls back with it.
    """
    conn.execute(
        "INSERT INTO audit_log (staff_id, action, details, created_at) VALUES (?, ?, ?, ?)",
        (staff_id, action, details, utc_now()),
    )


def list_entries(
    conn: sqlite3.Connection, actor: Actor, limit: int = 200, offset: int = 0
) -> list[dict]:
    """Newest entries first, with the staff member's username where known."""
    require(actor, Permission.VIEW_AUDIT_LOG)
    rows = conn.execute(
        "SELECT a.id, a.action, a.details, a.created_at, a.staff_id, s.username "
        "FROM audit_log a LEFT JOIN staff s ON s.id = a.staff_id "
        "ORDER BY a.id DESC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]
