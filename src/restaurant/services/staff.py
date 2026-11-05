"""Staff accounts (FR-S3, FR-S4, FR-S5). Results never include password_hash.

A manager may change their own account, so the caller must refresh or end the
session when the returned ``id`` is the actor's: an Actor keeps the role it had
at sign-in.
"""

from __future__ import annotations

import sqlite3

from restaurant.core import (
    Actor,
    ConflictError,
    NotFoundError,
    Permission,
    Role,
    ValidationError,
    require,
    utc_now,
)
from restaurant.db.connection import transaction
from restaurant.services import audit
from restaurant.services.auth import (
    PUBLIC_STAFF_COLUMNS,
    clean_full_name,
    clean_username,
    hash_password,
    validate_password,
)


def list_staff(conn: sqlite3.Connection, actor: Actor) -> list[dict]:
    """Everyone, including deactivated staff, ordered by name."""
    require(actor, Permission.MANAGE_STAFF)
    rows = conn.execute(
        f"SELECT {PUBLIC_STAFF_COLUMNS} FROM staff ORDER BY full_name COLLATE NOCASE, id"
    ).fetchall()
    return [dict(row) for row in rows]


def create_staff(
    conn: sqlite3.Connection,
    actor: Actor,
    username: str,
    full_name: str,
    role: Role | str,
    password: str,
) -> dict:
    require(actor, Permission.MANAGE_STAFF)
    username = clean_username(username)
    full_name = clean_full_name(full_name)
    role = _parse_role(role)
    validate_password(password)
    password_hash = hash_password(password)  # slow, so before taking the write lock
    with transaction(conn):
        try:
            staff_id = conn.execute(
                "INSERT INTO staff (username, full_name, password_hash, role, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (username, full_name, password_hash, role.value, utc_now()),
            ).lastrowid
        except sqlite3.IntegrityError:
            raise ConflictError(f'The username "{username}" is already taken.') from None
        audit.record(conn, actor.id, "staff.created", f"{username}, role {role.value}")
        return _get(conn, staff_id)


def update_staff(
    conn: sqlite3.Connection,
    actor: Actor,
    staff_id: int,
    full_name: str | None = None,
    role: Role | str | None = None,
) -> dict:
    """Change the full name and/or role. ``None`` leaves that field as it is."""
    require(actor, Permission.MANAGE_STAFF)
    new_name = None if full_name is None else clean_full_name(full_name)
    new_role = None if role is None else _parse_role(role)
    with transaction(conn):
        old = _get(conn, staff_id)
        changes = []
        if new_name is not None and new_name != old["full_name"]:
            conn.execute("UPDATE staff SET full_name = ? WHERE id = ?", (new_name, staff_id))
            changes.append(f'name "{old["full_name"]}" to "{new_name}"')
        if new_role is not None and new_role.value != old["role"]:
            if old["active"]:
                _require_another_manager_if_manager(conn, old)
            conn.execute("UPDATE staff SET role = ? WHERE id = ?", (new_role.value, staff_id))
            changes.append(f'role {old["role"]} to {new_role.value}')
        if changes:
            audit.record(
                conn, actor.id, "staff.updated", f'{old["username"]}: ' + ", ".join(changes)
            )
        return _get(conn, staff_id)


def deactivate_staff(conn: sqlite3.Connection, actor: Actor, staff_id: int) -> dict:
    """Deactivate an account. Staff are never deleted (FR-S3). Repeating it changes nothing."""
    require(actor, Permission.MANAGE_STAFF)
    with transaction(conn):
        old = _get(conn, staff_id)
        if old["active"]:
            _require_another_manager_if_manager(conn, old)
            conn.execute("UPDATE staff SET active = 0 WHERE id = ?", (staff_id,))
            audit.record(conn, actor.id, "staff.deactivated", old["username"])
        return _get(conn, staff_id)


def reactivate_staff(conn: sqlite3.Connection, actor: Actor, staff_id: int) -> dict:
    require(actor, Permission.MANAGE_STAFF)
    with transaction(conn):
        old = _get(conn, staff_id)
        if not old["active"]:
            conn.execute("UPDATE staff SET active = 1 WHERE id = ?", (staff_id,))
            audit.record(conn, actor.id, "staff.reactivated", old["username"])
        return _get(conn, staff_id)


def reset_password(
    conn: sqlite3.Connection, actor: Actor, staff_id: int, new_password: str
) -> None:
    """Set anyone's password without knowing the current one (FR-S5)."""
    require(actor, Permission.MANAGE_STAFF)
    validate_password(new_password)
    password_hash = hash_password(new_password)  # slow, so before taking the write lock
    with transaction(conn):
        old = _get(conn, staff_id)
        conn.execute("UPDATE staff SET password_hash = ? WHERE id = ?", (password_hash, staff_id))
        audit.record(conn, actor.id, "staff.password_reset", old["username"])


def _get(conn: sqlite3.Connection, staff_id: int) -> dict:
    row = conn.execute(
        f"SELECT {PUBLIC_STAFF_COLUMNS} FROM staff WHERE id = ?", (staff_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("That staff member no longer exists.")
    return dict(row)


def _parse_role(role: Role | str) -> Role:
    try:
        return Role(role)
    except ValueError:
        raise ValidationError("Choose a valid role.") from None


def _require_another_manager_if_manager(conn: sqlite3.Connection, staff: dict) -> None:
    """FR-S4. Call inside the transaction, for an active account about to lose access."""
    if staff["role"] != Role.MANAGER.value:
        return
    others = conn.execute(
        "SELECT COUNT(*) FROM staff WHERE role = ? AND active = 1 AND id <> ?",
        (Role.MANAGER.value, staff["id"]),
    ).fetchone()[0]
    if others == 0:
        raise ConflictError("This is the last active manager. Make another manager first.")
