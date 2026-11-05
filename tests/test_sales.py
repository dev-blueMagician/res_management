"""Sales: recording, voiding, lookups. Test data is inserted directly with SQL."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from restaurant.core import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from restaurant.services import audit, sales


def add_category(conn, name="Mains", active=1):
    return conn.execute(
        "INSERT INTO menu_categories (name, active) VALUES (?, ?)", (name, active)
    ).lastrowid


def add_item(conn, category_id, name="Burger", price=1000, active=1):
    return conn.execute(
        "INSERT INTO menu_items (category_id, name, price_cents, active) VALUES (?, ?, ?, ?)",
        (category_id, name, price, active),
    ).lastrowid


def stock_ingredient(conn, staff_id, name="Beef", on_hand=10_000):
    """An ingredient with opening stock, added through the ledger as the schema requires."""
    ingredient_id = conn.execute(
        "INSERT INTO ingredients (name, unit, unit_cost_cents, low_milli) "
        "VALUES (?, 'kg', 500, 1000)",
        (name,),
    ).lastrowid
    conn.execute(
        "INSERT INTO stock_movements (ingredient_id, movement_type, delta_milli, staff_id, "
        "created_at) VALUES (?, 'received', ?, ?, '2026-01-01T00:00:00+00:00')",
        (ingredient_id, on_hand, staff_id),
    )
    return ingredient_id


def add_recipe(conn, item_id, ingredient_id, qty_milli):
    conn.execute(
        "INSERT INTO recipe_items (menu_item_id, ingredient_id, qty_milli) VALUES (?, ?, ?)",
        (item_id, ingredient_id, qty_milli),
    )


def set_tax(conn, rate_bp):
    conn.execute("UPDATE settings SET value = ? WHERE key = 'tax_rate_bp'", (str(rate_bp),))


def on_hand(conn, ingredient_id):
    return conn.execute(
        "SELECT on_hand_milli FROM ingredients WHERE id = ?", (ingredient_id,)
    ).fetchone()[0]


def movements(conn, sale_id, movement_type=None):
    sql = "SELECT * FROM stock_movements WHERE sale_id = ?"
    params = [sale_id]
    if movement_type:
        sql += " AND movement_type = ?"
        params.append(movement_type)
    return [dict(row) for row in conn.execute(sql + " ORDER BY id", params)]


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def line(item_id, quantity=1):
    return {"menu_item_id": item_id, "quantity": quantity}


def set_sale_time(conn, sale_id, moment):
    conn.execute(
        "UPDATE sales SET created_at = ? WHERE id = ?",
        (moment.astimezone(timezone.utc).isoformat(timespec="seconds"), sale_id),
    )


@pytest.fixture
def item(conn):
    return add_item(conn, add_category(conn))


# record_sale: totals, snapshots, stock


def test_record_sale_saves_totals_cashier_and_method(conn, cashier):
    set_tax(conn, 825)
    category = add_category(conn)
    burger = add_item(conn, category, "Burger", 1250)
    fries = add_item(conn, category, "Fries", 399)

    sale = sales.record_sale(conn, cashier, [line(burger, 2), line(fries, 3)], "card")

    assert sale["subtotal_cents"] == 3697
    assert sale["tax_rate_bp"] == 825
    assert sale["tax_cents"] == 305  # 305.0025 rounds to 305
    assert sale["total_cents"] == 4002
    assert sale["payment_method"] == "card"
    assert sale["staff_id"] == cashier.id
    assert sale["cashier_name"] == cashier.full_name
    assert sale["voided_at"] is None
    assert datetime.fromisoformat(sale["created_at"]).utcoffset() == timedelta(0)
    assert [(i["name_as_sold"], i["quantity"], i["line_total_cents"]) for i in sale["items"]] == [
        ("Burger", 2, 2500),
        ("Fries", 3, 1197),
    ]


def test_tax_is_computed_on_the_subtotal_not_per_line(conn, cashier):
    set_tax(conn, 1000)
    category = add_category(conn)
    # Each line's tax is 0.5 cents; per-line half-up would give 2 cents, not 1.
    first = add_item(conn, category, "A", 5)
    second = add_item(conn, category, "B", 5)

    sale = sales.record_sale(conn, cashier, [line(first), line(second)], "cash")

    assert sale["tax_cents"] == 1


def test_tax_rounds_half_up(conn, cashier):
    set_tax(conn, 1000)
    sale = sales.record_sale(conn, cashier, [line(add_item(conn, add_category(conn), price=5))], "cash")
    assert sale["tax_cents"] == 1  # 0.5 cents rounds up


def test_caller_cannot_supply_price_or_tax(conn, cashier, item):
    set_tax(conn, 1000)
    sale = sales.record_sale(
        conn,
        cashier,
        [{"menu_item_id": item, "quantity": 1, "price_cents": 1, "tax_rate_bp": 0}],
        "cash",
    )
    assert sale["subtotal_cents"] == 1000
    assert sale["tax_cents"] == 100


def test_free_item_can_be_sold(conn, cashier):
    sale = sales.record_sale(conn, cashier, [line(add_item(conn, add_category(conn), price=0))], "other")
    assert sale["total_cents"] == 0


def test_sale_keeps_as_sold_values_after_menu_changes(conn, manager, cashier):
    category = add_category(conn, "Mains")
    item_id = add_item(conn, category, "Burger", 1000)
    sale = sales.record_sale(conn, cashier, [line(item_id)], "cash")

    conn.execute("UPDATE menu_items SET name = 'Deluxe', price_cents = 9999 WHERE id = ?", (item_id,))
    conn.execute("UPDATE menu_categories SET name = 'Specials' WHERE id = ?", (category,))

    saved = sales.get_sale(conn, manager, sale["id"])["items"][0]
    assert saved["name_as_sold"] == "Burger"
    assert saved["category_as_sold"] == "Mains"
    assert saved["unit_price_cents"] == 1000


def test_duplicate_lines_are_merged(conn, cashier, item):
    sale = sales.record_sale(conn, cashier, [line(item, 2), line(item, 3)], "cash")

    assert len(sale["items"]) == 1
    assert sale["items"][0]["quantity"] == 5
    assert sale["items"][0]["line_total_cents"] == 5000


def test_one_sold_movement_per_ingredient_across_the_whole_sale(conn, cashier):
    category = add_category(conn)
    burger = add_item(conn, category, "Burger")
    cheeseburger = add_item(conn, category, "Cheeseburger")
    beef = stock_ingredient(conn, cashier.id, "Beef", 10_000)
    cheese = stock_ingredient(conn, cashier.id, "Cheese", 5_000)
    add_recipe(conn, burger, beef, 150)
    add_recipe(conn, cheeseburger, beef, 200)
    add_recipe(conn, cheeseburger, cheese, 25)

    sale = sales.record_sale(conn, cashier, [line(burger, 2), line(cheeseburger, 3)], "cash")

    sold = movements(conn, sale["id"], "sold")
    assert [(m["ingredient_id"], m["delta_milli"]) for m in sold] == [(beef, -900), (cheese, -75)]
    assert all(m["staff_id"] == cashier.id for m in sold)
    assert on_hand(conn, beef) == 9_100
    assert on_hand(conn, cheese) == 4_925


def test_item_without_recipe_deducts_nothing(conn, cashier, item):
    beef = stock_ingredient(conn, cashier.id)
    sale = sales.record_sale(conn, cashier, [line(item, 4)], "cash")

    assert movements(conn, sale["id"]) == []
    assert on_hand(conn, beef) == 10_000


def test_low_stock_never_blocks_a_sale_and_may_go_negative(conn, cashier, item):
    beef = stock_ingredient(conn, cashier.id, on_hand=100)
    add_recipe(conn, item, beef, 150)

    sales.record_sale(conn, cashier, [line(item, 2)], "cash")

    assert on_hand(conn, beef) == -200


# record_sale: rejected input


@pytest.mark.parametrize("lines", [[], (), None, "abc", {"menu_item_id": 1, "quantity": 1}])
def test_empty_or_malformed_sale_is_rejected(conn, cashier, lines):
    with pytest.raises(ValidationError):
        sales.record_sale(conn, cashier, lines, "cash")
    assert count(conn, "sales") == 0


@pytest.mark.parametrize(
    "quantity", [0, -1, 1.5, 2.0, "2", None, True, Decimal("2")]
)
def test_invalid_quantity_is_rejected(conn, cashier, item, quantity):
    with pytest.raises(ValidationError):
        sales.record_sale(conn, cashier, [line(item, quantity)], "cash")
    assert count(conn, "sales") == 0


def test_line_that_is_not_a_mapping_is_rejected(conn, cashier):
    with pytest.raises(ValidationError):
        sales.record_sale(conn, cashier, [42], "cash")


def test_missing_quantity_is_rejected(conn, cashier, item):
    with pytest.raises(ValidationError):
        sales.record_sale(conn, cashier, [{"menu_item_id": item}], "cash")


def test_quantity_cap_applies_after_merging(conn, cashier, item):
    half = sales.MAX_LINE_QUANTITY // 2 + 1
    with pytest.raises(ValidationError):
        sales.record_sale(conn, cashier, [line(item, half), line(item, half)], "cash")
    with pytest.raises(ValidationError):
        sales.record_sale(conn, cashier, [line(item, 10**30)], "cash")
    assert count(conn, "sales") == 0


@pytest.mark.parametrize("item_id", [999_999, 0, -1, None, "1", 1.0, True, 10**30])
def test_unknown_menu_item_is_rejected(conn, cashier, item, item_id):
    with pytest.raises(NotFoundError):
        sales.record_sale(conn, cashier, [line(item_id)], "cash")
    assert count(conn, "sales") == 0


@pytest.mark.parametrize("method", ["cheque", "CASH", "", None, ["cash"]])
def test_invalid_payment_method_is_rejected(conn, cashier, item, method):
    with pytest.raises(ValidationError):
        sales.record_sale(conn, cashier, [line(item)], method)
    assert count(conn, "sales") == 0


@pytest.mark.parametrize("method", ["cash", "card", "other"])
def test_every_payment_method_is_accepted(conn, cashier, item, method):
    assert sales.record_sale(conn, cashier, [line(item)], method)["payment_method"] == method


def test_inactive_item_is_not_sellable(conn, cashier):
    item_id = add_item(conn, add_category(conn), active=0)
    with pytest.raises(ValidationError, match="Burger"):
        sales.record_sale(conn, cashier, [line(item_id)], "cash")
    assert count(conn, "sales") == 0


def test_active_item_in_inactive_category_is_not_sellable(conn, cashier):
    item_id = add_item(conn, add_category(conn, active=0), active=1)
    with pytest.raises(ValidationError, match="Burger"):
        sales.record_sale(conn, cashier, [line(item_id)], "cash")
    assert count(conn, "sales") == 0


@pytest.mark.parametrize("value", ["abc", "", "-5"])
def test_bad_tax_setting_stops_the_sale_instead_of_charging_no_tax(conn, cashier, item, value):
    conn.execute("UPDATE settings SET value = ? WHERE key = 'tax_rate_bp'", (value,))
    with pytest.raises(ConflictError):
        sales.record_sale(conn, cashier, [line(item)], "cash")
    assert count(conn, "sales") == 0
    assert not conn.in_transaction


def test_missing_tax_setting_stops_the_sale(conn, cashier, item):
    conn.execute("DELETE FROM settings WHERE key = 'tax_rate_bp'")
    with pytest.raises(ConflictError):
        sales.record_sale(conn, cashier, [line(item)], "cash")
    assert not conn.in_transaction


# record_sale: atomicity


def test_failure_midway_leaves_no_sale_no_lines_and_no_stock_change(conn, cashier):
    category = add_category(conn)
    good = add_item(conn, category, "Good")
    bad = add_item(conn, category, "Bad")
    first = stock_ingredient(conn, cashier.id, "First", 1_000)
    second = stock_ingredient(conn, cashier.id, "Second", 1_000)
    add_recipe(conn, good, first, 100)
    add_recipe(conn, bad, second, 100)
    # The second movement fails, after the sale, its lines and the first movement exist.
    conn.execute(
        "CREATE TEMP TRIGGER fail_second BEFORE INSERT ON stock_movements "
        f"WHEN NEW.ingredient_id = {second} AND NEW.movement_type = 'sold' "
        "BEGIN SELECT RAISE(ABORT, 'disk trouble'); END"
    )
    movements_before = count(conn, "stock_movements")

    with pytest.raises(sqlite3.IntegrityError):
        sales.record_sale(conn, cashier, [line(good), line(bad)], "cash")

    assert not conn.in_transaction
    assert count(conn, "sales") == 0
    assert count(conn, "sale_items") == 0
    assert count(conn, "stock_movements") == movements_before
    assert on_hand(conn, first) == 1_000
    assert on_hand(conn, second) == 1_000


def test_unsellable_line_rejects_the_whole_sale(conn, cashier):
    category = add_category(conn)
    good = add_item(conn, category, "Good")
    gone = add_item(conn, category, "Gone", active=0)
    beef = stock_ingredient(conn, cashier.id)
    add_recipe(conn, good, beef, 100)

    with pytest.raises(ValidationError):
        sales.record_sale(conn, cashier, [line(good), line(gone)], "cash")

    assert count(conn, "sales") == 0
    assert on_hand(conn, beef) == 10_000


# Permissions


def test_stock_clerk_cannot_record_a_sale(conn, stock_clerk, item):
    with pytest.raises(PermissionDeniedError):
        sales.record_sale(conn, stock_clerk, [line(item)], "cash")
    assert count(conn, "sales") == 0


def test_manager_can_record_a_sale(conn, manager, item):
    assert sales.record_sale(conn, manager, [line(item)], "cash")["staff_id"] == manager.id


def test_permission_is_checked_before_validation(conn, stock_clerk):
    with pytest.raises(PermissionDeniedError):
        sales.record_sale(conn, stock_clerk, [], "nonsense")


@pytest.mark.parametrize("role", ["cashier", "stock_clerk"])
def test_only_managers_can_void(conn, manager, item, request, role):
    actor = request.getfixturevalue(role)
    sale = sales.record_sale(conn, manager, [line(item)], "cash")

    with pytest.raises(PermissionDeniedError):
        sales.void_sale(conn, actor, sale["id"], "mistake")

    assert sales.get_sale(conn, manager, sale["id"])["voided_at"] is None


def test_stock_clerk_cannot_read_sales(conn, manager, stock_clerk, item):
    sale = sales.record_sale(conn, manager, [line(item)], "cash")
    with pytest.raises(PermissionDeniedError):
        sales.get_sale(conn, stock_clerk, sale["id"])
    with pytest.raises(PermissionDeniedError):
        sales.list_sales(conn, stock_clerk)


# void_sale


def test_void_marks_sale_and_returns_stock(conn, manager, cashier, item):
    beef = stock_ingredient(conn, manager.id)
    add_recipe(conn, item, beef, 150)
    sale = sales.record_sale(conn, cashier, [line(item, 2)], "cash")

    voided = sales.void_sale(conn, manager, sale["id"], "  Wrong order  ")

    assert voided["voided_by"] == manager.id
    assert voided["voided_by_name"] == manager.full_name
    assert voided["void_reason"] == "Wrong order"
    assert voided["voided_at"] is not None
    assert voided["total_cents"] == sale["total_cents"]  # stays on record
    assert len(voided["items"]) == 1
    assert on_hand(conn, beef) == 10_000


def test_void_mirrors_actual_movements_not_the_changed_recipe(conn, manager, cashier, item):
    beef = stock_ingredient(conn, manager.id, "Beef", 10_000)
    cheese = stock_ingredient(conn, manager.id, "Cheese", 10_000)
    lettuce = stock_ingredient(conn, manager.id, "Lettuce", 10_000)
    add_recipe(conn, item, beef, 150)
    add_recipe(conn, item, cheese, 20)
    sale = sales.record_sale(conn, cashier, [line(item, 2)], "cash")

    # Change a quantity, drop an ingredient and add a new one before voiding.
    conn.execute("UPDATE recipe_items SET qty_milli = 999 WHERE ingredient_id = ?", (beef,))
    conn.execute("DELETE FROM recipe_items WHERE ingredient_id = ?", (cheese,))
    add_recipe(conn, item, lettuce, 50)

    sales.void_sale(conn, manager, sale["id"], "Customer left")

    reversals = movements(conn, sale["id"], "void_reversal")
    assert [(m["ingredient_id"], m["delta_milli"]) for m in reversals] == [(beef, 300), (cheese, 40)]
    assert all(m["staff_id"] == manager.id for m in reversals)
    assert on_hand(conn, beef) == 10_000
    assert on_hand(conn, cheese) == 10_000
    assert on_hand(conn, lettuce) == 10_000


def test_void_of_sale_without_recipe_writes_no_movements(conn, manager, cashier, item):
    sale = sales.record_sale(conn, cashier, [line(item)], "cash")
    sales.void_sale(conn, manager, sale["id"], "Mistake")
    assert movements(conn, sale["id"]) == []


def test_second_void_is_a_conflict_and_changes_nothing(conn, manager, cashier, item):
    beef = stock_ingredient(conn, manager.id)
    add_recipe(conn, item, beef, 150)
    sale = sales.record_sale(conn, cashier, [line(item)], "cash")
    first = sales.void_sale(conn, manager, sale["id"], "First reason")

    with pytest.raises(ConflictError):
        sales.void_sale(conn, manager, sale["id"], "Second reason")

    assert not conn.in_transaction
    assert sales.get_sale(conn, manager, sale["id"])["void_reason"] == first["void_reason"]
    assert len(movements(conn, sale["id"], "void_reversal")) == 1
    assert on_hand(conn, beef) == 10_000
    assert len([e for e in audit.list_entries(conn, manager) if e["action"] == "sale_voided"]) == 1


@pytest.mark.parametrize("reason", ["", "   ", "\n\t", None, 5])
def test_void_requires_a_reason(conn, manager, item, reason):
    sale = sales.record_sale(conn, manager, [line(item)], "cash")
    with pytest.raises(ValidationError):
        sales.void_sale(conn, manager, sale["id"], reason)
    assert sales.get_sale(conn, manager, sale["id"])["voided_at"] is None


@pytest.mark.parametrize("sale_id", [999_999, 0, None, "1", 1.0, True, 10**30])
def test_void_of_unknown_sale_is_not_found(conn, manager, item, sale_id):
    sales.record_sale(conn, manager, [line(item)], "cash")
    with pytest.raises(NotFoundError):
        sales.void_sale(conn, manager, sale_id, "Mistake")
    assert not conn.in_transaction


def test_void_is_audited_in_the_same_transaction(conn, manager, cashier, item):
    sale = sales.record_sale(conn, cashier, [line(item)], "cash")
    sales.void_sale(conn, manager, sale["id"], "Wrong table")

    entry = audit.list_entries(conn, manager)[0]
    assert entry["action"] == "sale_voided"
    assert entry["staff_id"] == manager.id
    assert f"#{sale['id']}" in entry["details"]
    assert "Wrong table" in entry["details"]


def test_void_audit_entry_reads_as_money_not_as_a_field_name(conn, manager, cashier, item):
    sale = sales.record_sale(conn, cashier, [line(item)], "cash")
    sales.void_sale(conn, manager, sale["id"], "Wrong table")
    total = f"{sale['total_cents'] // 100}.{sale['total_cents'] % 100:02d}"

    details = audit.list_entries(conn, manager)[0]["details"]

    assert details == f"Sale #{sale['id']}, total {total}, reason: Wrong table"


def test_failed_void_rolls_everything_back(conn, manager, cashier):
    item_id = add_item(conn, add_category(conn))
    first = stock_ingredient(conn, manager.id, "First", 1_000)
    second = stock_ingredient(conn, manager.id, "Second", 1_000)
    add_recipe(conn, item_id, first, 100)
    add_recipe(conn, item_id, second, 100)
    sale = sales.record_sale(conn, cashier, [line(item_id)], "cash")
    conn.execute(
        "CREATE TEMP TRIGGER fail_reversal BEFORE INSERT ON stock_movements "
        f"WHEN NEW.ingredient_id = {second} AND NEW.movement_type = 'void_reversal' "
        "BEGIN SELECT RAISE(ABORT, 'disk trouble'); END"
    )

    with pytest.raises(sqlite3.IntegrityError):
        sales.void_sale(conn, manager, sale["id"], "Mistake")

    assert not conn.in_transaction
    assert sales.get_sale(conn, manager, sale["id"])["voided_at"] is None
    assert movements(conn, sale["id"], "void_reversal") == []
    assert on_hand(conn, first) == 900
    assert count(conn, "audit_log") == 0


def test_failed_audit_rolls_the_void_back(conn, manager, cashier, item):
    beef = stock_ingredient(conn, manager.id)
    add_recipe(conn, item, beef, 150)
    sale = sales.record_sale(conn, cashier, [line(item)], "cash")
    conn.execute(
        "CREATE TEMP TRIGGER fail_audit BEFORE INSERT ON audit_log "
        "BEGIN SELECT RAISE(ABORT, 'disk trouble'); END"
    )

    with pytest.raises(sqlite3.IntegrityError):
        sales.void_sale(conn, manager, sale["id"], "Mistake")

    assert sales.get_sale(conn, manager, sale["id"])["voided_at"] is None
    assert on_hand(conn, beef) == 9_850


# get_sale and list_sales


def test_get_sale_unknown_is_not_found(conn, manager):
    with pytest.raises(NotFoundError):
        sales.get_sale(conn, manager, 123)


def test_cashier_can_get_own_recent_sale_only(conn, manager, cashier, item):
    mine = sales.record_sale(conn, cashier, [line(item)], "cash")
    theirs = sales.record_sale(conn, manager, [line(item)], "cash")
    old = sales.record_sale(conn, cashier, [line(item)], "cash")
    set_sale_time(
        conn, old["id"], datetime.now(timezone.utc) - timedelta(days=sales.RECENT_SALES_DAYS + 1)
    )

    assert sales.get_sale(conn, cashier, mine["id"])["id"] == mine["id"]
    with pytest.raises(PermissionDeniedError):
        sales.get_sale(conn, cashier, theirs["id"])
    with pytest.raises(PermissionDeniedError):
        sales.get_sale(conn, cashier, old["id"])
    assert sales.get_sale(conn, manager, old["id"])["id"] == old["id"]


def test_manager_lists_all_newest_first_including_voided(conn, manager, cashier, item):
    first = sales.record_sale(conn, cashier, [line(item)], "cash")
    second = sales.record_sale(conn, manager, [line(item)], "card")
    sales.void_sale(conn, manager, first["id"], "Mistake")

    listed = sales.list_sales(conn, manager)

    assert [s["id"] for s in listed] == [second["id"], first["id"]]
    assert listed[1]["voided_at"] is not None
    assert listed[0]["cashier_name"] == manager.full_name


def test_cashier_lists_only_own_recent_sales(conn, manager, cashier, item):
    mine = sales.record_sale(conn, cashier, [line(item)], "cash")
    sales.record_sale(conn, manager, [line(item)], "cash")
    old = sales.record_sale(conn, cashier, [line(item)], "cash")
    moment = datetime.now(timezone.utc) - timedelta(days=sales.RECENT_SALES_DAYS + 1)
    set_sale_time(conn, old["id"], moment)

    assert [s["id"] for s in sales.list_sales(conn, cashier)] == [mine["id"]]
    # An explicit date range can't reach past the recent window.
    wide = sales.list_sales(conn, cashier, moment.date() - timedelta(days=1), date.today())
    assert [s["id"] for s in wide] == [mine["id"]]


def test_list_sales_pagination(conn, manager, item):
    ids = [sales.record_sale(conn, manager, [line(item)], "cash")["id"] for _ in range(5)]
    newest_first = ids[::-1]

    assert [s["id"] for s in sales.list_sales(conn, manager, limit=2)] == newest_first[:2]
    assert [s["id"] for s in sales.list_sales(conn, manager, limit=2, offset=2)] == newest_first[2:4]
    assert [s["id"] for s in sales.list_sales(conn, manager, limit=2, offset=4)] == newest_first[4:]
    assert sales.list_sales(conn, manager, limit=2, offset=5) == []


@pytest.mark.parametrize(
    "page",
    [
        {"limit": 0},
        {"limit": -1},
        {"limit": sales.MAX_PAGE_SIZE + 1},
        {"limit": 1.5},
        {"limit": True},
        {"offset": -1},
        {"offset": "1"},
    ],
)
def test_list_sales_rejects_bad_pages(conn, manager, page):
    with pytest.raises(ValidationError):
        sales.list_sales(conn, manager, **page)


def test_list_sales_date_range_is_inclusive_of_the_end_date(conn, manager, item):
    tz = timezone(timedelta(hours=-5))
    before = sales.record_sale(conn, manager, [line(item)], "cash")
    first_second = sales.record_sale(conn, manager, [line(item)], "cash")
    last_second = sales.record_sale(conn, manager, [line(item)], "cash")
    after = sales.record_sale(conn, manager, [line(item)], "cash")
    set_sale_time(conn, before["id"], datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz))
    set_sale_time(conn, first_second["id"], datetime(2026, 6, 1, 0, 0, 0, tzinfo=tz))
    set_sale_time(conn, last_second["id"], datetime(2026, 6, 2, 23, 59, 59, tzinfo=tz))
    set_sale_time(conn, after["id"], datetime(2026, 6, 3, 0, 0, 0, tzinfo=tz))

    listed = sales.list_sales(conn, manager, date(2026, 6, 1), "2026-06-02", tz)

    assert [s["id"] for s in listed] == [last_second["id"], first_second["id"]]


def test_list_sales_needs_both_dates_or_neither(conn, manager):
    with pytest.raises(ValidationError):
        sales.list_sales(conn, manager, start_date="2026-06-01")
    with pytest.raises(ValidationError):
        sales.list_sales(conn, manager, end_date="2026-06-01")
