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
from restaurant.db.connection import SCHEMA_DIR, connect, migrate, transaction


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
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(list(SCHEMA_DIR.glob("*.sql")))
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


# --- whole numbers only (NFR-3) ------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "0.1000000000000000000000000000001",
        "12.34000000000000000000000000000001",
        "9999999999999999999999999999.991",
    ],
)
def test_parse_money_refuses_fractions_of_a_cent_however_long_the_input(text):
    with pytest.raises(ValidationError):
        parse_money(text)


def test_parse_quantity_refuses_fractions_of_a_thousandth_however_long_the_input():
    with pytest.raises(ValidationError):
        parse_quantity("1.0000000000000000000000000000000001")


def test_parse_money_is_exact_for_long_input_that_is_a_whole_number_of_cents():
    assert parse_money("12.3400000000000000000000000000000000") == 1234
    assert parse_money("1234567890123456789012345678901234.56") == 123456789012345678901234567890123456


def _rows_for_every_typed_column(conn, staff_id):
    category = conn.execute("INSERT INTO menu_categories (name) VALUES ('C')").lastrowid
    item = conn.execute(
        "INSERT INTO menu_items (category_id, name, price_cents) VALUES (?, 'I', 100)", (category,)
    ).lastrowid
    ingredient = _ingredient(conn)
    conn.execute(
        "INSERT INTO recipe_items (menu_item_id, ingredient_id, qty_milli) VALUES (?, ?, 5)",
        (item, ingredient),
    )
    sale = conn.execute(
        "INSERT INTO sales (staff_id, payment_method, subtotal_cents, tax_rate_bp, tax_cents, "
        "total_cents, created_at) VALUES (?, 'cash', 100, 0, 0, 100, ?)",
        (staff_id, utc_now()),
    ).lastrowid
    conn.execute(
        "INSERT INTO sale_items (sale_id, menu_item_id, name_as_sold, category_as_sold, "
        "unit_price_cents, quantity, line_total_cents) VALUES (?, ?, 'I', 'C', 100, 1, 100)",
        (sale, item),
    )
    return category, ingredient, item, sale


@pytest.mark.parametrize("value", [12.5, "abc", b"blob"], ids=["float", "text", "blob"])
@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE menu_items SET price_cents = ?",
        "UPDATE ingredients SET unit_cost_cents = ?",
        "UPDATE ingredients SET on_hand_milli = ?",
        "UPDATE ingredients SET low_milli = ?",
        "UPDATE recipe_items SET qty_milli = ?",
        "UPDATE sales SET tax_rate_bp = ?",
    ],
)
def test_money_and_quantity_columns_refuse_anything_but_integers_on_update(
    conn, manager, statement, value
):
    _rows_for_every_typed_column(conn, manager.id)
    with pytest.raises(sqlite3.IntegrityError, match="whole number"):
        conn.execute(statement, (value,))


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE sales SET subtotal_cents = 100.5, total_cents = 100.5",
        "UPDATE sales SET tax_cents = 0.5, total_cents = 100.5",
        "UPDATE sale_items SET unit_price_cents = 100.5, line_total_cents = 100.5",
        "UPDATE sale_items SET quantity = 0.5, line_total_cents = 50",
    ],
)
def test_floats_that_still_add_up_are_refused_on_sales(conn, manager, statement):
    _rows_for_every_typed_column(conn, manager.id)
    with pytest.raises(sqlite3.IntegrityError, match="whole number"):
        conn.execute(statement)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE sales SET subtotal_cents = 'abc'",
        "UPDATE sales SET total_cents = 'abc'",
        "UPDATE sales SET total_cents = 100.5",
        "UPDATE sale_items SET quantity = 'abc'",
        "UPDATE sale_items SET line_total_cents = 100.5",
    ],
)
def test_sale_amounts_that_do_not_add_up_are_refused_by_their_check(conn, manager, statement):
    _rows_for_every_typed_column(conn, manager.id)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(statement)


@pytest.mark.parametrize("value", [12.5, "abc"], ids=["float", "text"])
def test_money_and_quantity_columns_refuse_anything_but_integers_on_insert(conn, manager, value):
    category, ingredient, item, sale = _rows_for_every_typed_column(conn, manager.id)
    now = utc_now()
    for statement, params in (
        (
            "INSERT INTO menu_items (category_id, name, price_cents) VALUES (?, 'J', ?)",
            (category, value),
        ),
        ("INSERT INTO ingredients (name, unit, unit_cost_cents) VALUES ('K', 'kg', ?)", (value,)),
        ("INSERT INTO ingredients (name, unit, on_hand_milli) VALUES ('L', 'kg', ?)", (value,)),
        ("INSERT INTO ingredients (name, unit, low_milli) VALUES ('M', 'kg', ?)", (value,)),
        (
            "INSERT INTO recipe_items (menu_item_id, ingredient_id, qty_milli) VALUES (?, ?, ?)",
            (item, _ingredient_named(conn, "N"), value),
        ),
        (
            "INSERT INTO stock_movements (ingredient_id, movement_type, delta_milli, staff_id, "
            "created_at) VALUES (?, 'adjusted', ?, ?, ?)",
            (ingredient, value, manager.id, now),
        ),
    ):
        with pytest.raises(sqlite3.IntegrityError, match="whole number"):
            conn.execute(statement, params)


def _ingredient_named(conn, name):
    return conn.execute("INSERT INTO ingredients (name, unit) VALUES (?, 'kg')", (name,)).lastrowid


def test_integer_like_values_become_integers_and_defaults_still_work(conn, manager):
    category = conn.execute("INSERT INTO menu_categories (name) VALUES ('C')").lastrowid
    conn.execute(
        "INSERT INTO menu_items (category_id, name, price_cents) VALUES (?, 'A', '12')", (category,)
    )
    row = conn.execute("SELECT price_cents, typeof(price_cents) FROM menu_items").fetchone()
    assert tuple(row) == (12, "integer")
    assert _ingredient(conn)  # all the defaulted stock columns pass


def test_upgrading_a_version_1_database_keeps_its_data_and_adds_the_checks(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path, isolation_level=None)
    migrate(old, target=1)
    assert old.execute("PRAGMA user_version").fetchone()[0] == 1
    old.execute("INSERT INTO ingredients (name, unit, low_milli) VALUES ('Flour', 'kg', 7)")
    old.execute("UPDATE ingredients SET low_milli = 1.5")  # allowed by the old schema
    old.close()

    conn = connect(path)
    assert conn.execute("SELECT low_milli FROM ingredients").fetchone()["low_milli"] == 1.5
    with pytest.raises(sqlite3.IntegrityError, match="whole number"):
        conn.execute("UPDATE ingredients SET low_milli = 2.5")
    conn.close()
