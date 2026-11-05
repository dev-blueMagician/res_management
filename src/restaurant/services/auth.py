"""Passwords and signing in (FR-S1, FR-S2, FR-S5, FR-S7, NFR-5)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sqlite3

from restaurant.core import (
    Actor,
    AuthenticationError,
    ConflictError,
    Role,
    ValidationError,
    utc_now,
)
from restaurant.db.connection import transaction
from restaurant.services import audit

MIN_PASSWORD_LENGTH = 8

# A cost from OWASP's recommended list for scrypt, about 0.3 s per hash on a desktop PC.
_SCRYPT_N = 2**15
_SCRYPT_R = 8
_SCRYPT_P = 3
_SALT_BYTES = 16
_HASH_BYTES = 32

_SIGN_IN_FAILED = "Incorrect username or password."
PUBLIC_STAFF_COLUMNS = "id, username, full_name, role, active, created_at"


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, length: int) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8", "surrogatepass"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        maxmem=128 * r * (n + p + 2),
        dklen=length,
    )


def hash_password(password: str) -> str:
    """Salted scrypt hash, with its parameters: ``scrypt$n$r$p$salt$hash``."""
    salt = os.urandom(_SALT_BYTES)
    digest = _scrypt(password, salt, _SCRYPT_N, _SCRYPT_R, _SCRYPT_P, _HASH_BYTES)
    encoded = (base64.b64encode(value).decode("ascii") for value in (salt, digest))
    return "$".join(("scrypt", str(_SCRYPT_N), str(_SCRYPT_R), str(_SCRYPT_P), *encoded))


def _parse_hash(stored: str) -> tuple[int, int, int, bytes, bytes]:
    """The parts of a stored hash. Raises ValueError (or binascii.Error) if malformed."""
    scheme, n, r, p, salt, expected = stored.split("$")
    if scheme != "scrypt":
        raise ValueError("unknown password scheme")
    salt_bytes = base64.b64decode(salt, validate=True)
    expected_bytes = base64.b64decode(expected, validate=True)
    if not expected_bytes:
        raise ValueError("empty password hash")
    return int(n), int(r), int(p), salt_bytes, expected_bytes


def is_well_formed_hash(stored: object) -> bool:
    """Could any password ever verify against this? Says nothing about which password."""
    try:
        n, r, p, _salt, _expected = _parse_hash(stored)  # type: ignore[arg-type]
    except (ValueError, AttributeError):
        return False
    return n > 1 and n & (n - 1) == 0 and r > 0 and p > 0


def verify_password(password: str, stored: str) -> bool:
    """Compare in constant time. An unusable stored hash costs the same work and fails."""
    try:
        n, r, p, salt_bytes, expected_bytes = _parse_hash(stored)
        actual = _scrypt(password, salt_bytes, n, r, p, len(expected_bytes))
    except (ValueError, OverflowError, MemoryError):  # binascii.Error is a ValueError
        hash_password(password)
        return False
    return hmac.compare_digest(actual, expected_bytes)


def validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise ValidationError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")


def clean_text(value: str, label: str, max_length: int) -> str:
    """Trim a required single-line text field such as a username or full name."""
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise ValidationError(f"{label} is required.")
    if len(text) > max_length:
        raise ValidationError(f"{label} must be at most {max_length} characters.")
    if not text.isprintable():
        raise ValidationError(f"{label} contains characters that aren't allowed.")
    return text


def clean_username(username: str) -> str:
    return clean_text(username, "Username", 50)


def clean_full_name(full_name: str) -> str:
    return clean_text(full_name, "Full name", 100)


def has_accounts(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT EXISTS (SELECT 1 FROM staff)").fetchone()[0] == 1


def create_first_manager(
    conn: sqlite3.Connection, username: str, full_name: str, password: str
) -> dict:
    """Create the first account. Refused as soon as any staff record exists (FR-S2)."""
    username = clean_username(username)
    full_name = clean_full_name(full_name)
    validate_password(password)
    password_hash = hash_password(password)  # slow, so before taking the write lock
    with transaction(conn):
        if has_accounts(conn):
            raise ConflictError("The first manager account has already been created.")
        staff_id = conn.execute(
            "INSERT INTO staff (username, full_name, password_hash, role, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, full_name, password_hash, Role.MANAGER.value, utc_now()),
        ).lastrowid
        audit.record(conn, staff_id, "staff.first_manager_created", username)
        row = conn.execute(
            f"SELECT {PUBLIC_STAFF_COLUMNS} FROM staff WHERE id = ?", (staff_id,)
        ).fetchone()
    return dict(row)


def authenticate(conn: sqlite3.Connection, username: str, password: str) -> Actor:
    """Return the signed-in Actor, or raise the same error whatever went wrong.

    Each failure path does one scrypt hash, so timing doesn't reveal which
    usernames exist. An unknown username is not written to the audit log,
    because people sometimes type their password into the username box.
    """
    name = username.strip()
    row = None
    if name.isprintable():  # anything else can't be stored, or even bound to a query
        row = conn.execute(
            "SELECT id, username, full_name, password_hash, role, active "
            "FROM staff WHERE username = ?",
            (name,),
        ).fetchone()
    if row is None:
        hash_password(password)
        _record_failed_sign_in(conn, None, "unknown username")
        raise AuthenticationError(_SIGN_IN_FAILED)
    password_ok = verify_password(password, row["password_hash"])  # also for inactive accounts
    if not password_ok or not row["active"]:
        reason = "inactive account" if password_ok else "wrong password"
        _record_failed_sign_in(conn, row["id"], f'{row["username"]}: {reason}')
        raise AuthenticationError(_SIGN_IN_FAILED)
    with transaction(conn):
        audit.record(conn, row["id"], "auth.sign_in", row["username"])
    return Actor(row["id"], row["username"], row["full_name"], Role(row["role"]))


def _record_failed_sign_in(conn: sqlite3.Connection, staff_id: int | None, details: str) -> None:
    # Committed before the caller raises, so the failure stays in the log.
    with transaction(conn):
        audit.record(conn, staff_id, "auth.sign_in_failed", details)


def change_own_password(
    conn: sqlite3.Connection, actor: Actor, current_password: str, new_password: str
) -> None:
    """Any signed-in user may change their own password, given the current one (FR-S5)."""
    validate_password(new_password)
    row = conn.execute(
        "SELECT password_hash, active FROM staff WHERE id = ?", (actor.id,)
    ).fetchone()
    if row is None or not row["active"]:
        raise AuthenticationError("Your account is no longer active.")
    if not verify_password(current_password, row["password_hash"]):
        with transaction(conn):
            audit.record(conn, actor.id, "auth.password_change_failed", actor.username)
        raise AuthenticationError("Your current password is incorrect.")
    password_hash = hash_password(new_password)
    with transaction(conn):
        conn.execute("UPDATE staff SET password_hash = ? WHERE id = ?", (password_hash, actor.id))
        audit.record(conn, actor.id, "auth.password_changed", actor.username)
