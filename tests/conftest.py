"""Shared fixtures: an isolated in-memory database and one actor per role."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from restaurant.core import Actor, Role, utc_now  # noqa: E402
from restaurant.db.connection import connect  # noqa: E402


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = connect(":memory:")
    yield connection
    connection.close()


def _add_staff(conn: sqlite3.Connection, username: str, role: Role) -> Actor:
    # The placeholder hash can never verify, so these accounts can't sign in.
    cursor = conn.execute(
        "INSERT INTO staff (username, full_name, password_hash, role, created_at) "
        "VALUES (?, ?, 'x', ?, ?)",
        (username, username.title(), role.value, utc_now()),
    )
    return Actor(cursor.lastrowid, username, username.title(), role)


@pytest.fixture
def manager(conn: sqlite3.Connection) -> Actor:
    return _add_staff(conn, "manager", Role.MANAGER)


@pytest.fixture
def cashier(conn: sqlite3.Connection) -> Actor:
    return _add_staff(conn, "cashier", Role.CASHIER)


@pytest.fixture
def stock_clerk(conn: sqlite3.Connection) -> Actor:
    return _add_staff(conn, "clerk", Role.STOCK_CLERK)
