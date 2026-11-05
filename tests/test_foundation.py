"""Tests for the schema rules and shared helpers every service relies on."""

from __future__ import annotations

import sqlite3

import pytest

from restaurant.core import (
    Permission,
    PermissionDeniedError,
    ValidationError,
    calculate_tax_cents,
    format_money,
    format_quantity,
    parse_money,
    parse_quantity,
    require,
    utc_now,
)
from restaurant.db.connection import connect, transaction


def _ingredient(conn: sqlite3.Connection) -> int:
    return conn.execute("INSERT INTO ingredients (name, unit) VALUES ('Flour', 'kg')").lastrowid


def _move(conn, ingredient_id, staff_id, kind, delta, sale_id=None):
    conn.execute(
        "INSERT INTO stock_movements "
        "(ingredient_id, movement_type, delta_milli, staff_id, sale_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ingredient_id, kind, delta, staff_id, sale_id, utc_now()),
    )


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "test.db"
    connect(path).close()
    conn = connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()


def test_trigger_keeps_on_hand_equal_to_ledger(conn, manager):
    ingredient = _ingredient(conn)
    _move(conn, ingredient, manager.id, "received", 5000)
    _move(conn, ingredient, manager.id, "wasted", -1500)
    _move(conn, ingredient, manager.id, "adjusted", -4000)
    on_hand = conn.execute(
        "SELECT on_hand_milli FROM ingredients WHERE id = ?", (ingredient,)
    ).fetchone()[0]
    ledger = conn.execute(
        "SELECT SUM(delta_milli) FROM stock_movements WHERE ingredient_id = ?", (ingredient,)
    ).fetchone()[0]
    assert on_hand == ledger == -500  # negative stock is allowed (FR-I5)


@pytest.mark.parametrize("statement", [
    "UPDATE stock_movements SET delta_milli = 1",
    "DELETE FROM stock_movements",
])
def test_ledger_is_insert_only(conn, manager, statement):
    _move(conn, _ingredient(conn), manager.id, "received", 1000)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(statement)


@pytest.mark.parametrize("kind, delta", [("received", -1), ("wasted", 1), ("sold", -1)])
def test_movement_direction_and_sale_link_are_enforced(conn, manager, kind, delta):
    with pytest.raises(sqlite3.IntegrityError):
        _move(conn, _ingredient(conn), manager.id, kind, delta)


def test_sale_totals_must_add_up(conn, manager):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO sales (staff_id, payment_method, subtotal_cents, tax_rate_bp, "
            "tax_cents, total_cents, created_at) VALUES (?, 'cash', 100, 0, 0, 999, ?)",
            (manager.id, utc_now()),
        )


def test_transaction_rolls_back_on_error(conn):
    with pytest.raises(RuntimeError):
        with transaction(conn):
            _ingredient(conn)
            raise RuntimeError
    assert conn.execute("SELECT COUNT(*) FROM ingredients").fetchone()[0] == 0


def test_role_permissions(manager, cashier, stock_clerk):
    assert all(manager.can(permission) for permission in Permission)
    assert cashier.can(Permission.RECORD_SALES)
    assert not cashier.can(Permission.VOID_SALES)
    assert stock_clerk.can(Permission.MANAGE_STOCK)
    assert not stock_clerk.can(Permission.RECORD_SALES)
    with pytest.raises(PermissionDeniedError):
        require(cashier, Permission.MANAGE_STAFF)


@pytest.mark.parametrize("subtotal, rate, expected", [
    (1000, 825, 83),  # 82.5 rounds half up
    (1000, 0, 0),
    (199, 1000, 20),
])
def test_tax_rounding(subtotal, rate, expected):
    assert calculate_tax_cents(subtotal, rate) == expected


def test_parsing_and_formatting():
    assert parse_money("12.50") == 1250
    assert parse_quantity("1.5") == 1500
    assert parse_money(" 3 ") == 300
    for bad in ("abc", "", "1.234", "NaN", "Infinity"):
        with pytest.raises(ValidationError):
            parse_money(bad)
    assert format_money(123456) == "$1,234.56"
    assert format_money(-50) == "-$0.50"
    assert format_quantity(1500) == "1.5"
    assert format_quantity(-2000) == "-2"
    assert format_quantity(0) == "0"
