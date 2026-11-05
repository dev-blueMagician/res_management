"""Ingredients and stock movements (FR-I1 to FR-I5)."""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import pytest

from restaurant.core import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from restaurant.services import audit, inventory, menu


def audit_rows(conn, action):
    return conn.execute(
        "SELECT staff_id, details FROM audit_log WHERE action = ? ORDER BY id", (action,)
    ).fetchall()


def movement_rows(conn):
    return conn.execute("SELECT * FROM stock_movements ORDER BY id").fetchall()


def on_hand(conn, ingredient_id):
    return conn.execute(
        "SELECT on_hand_milli FROM ingredients WHERE id = ?", (ingredient_id,)
    ).fetchone()[0]


def fail_audit(*args, **kwargs):
    raise RuntimeError("audit failed")


def local_utc(year, month, day, hour=0, minute=0, second=0):
    """The stored UTC text for a restaurant-local wall-clock time."""
    local = datetime(year, month, day, hour, minute, second)
    return local.astimezone(timezone.utc).isoformat(timespec="seconds")


def insert_movement(conn, ingredient_id, staff_id, created_at, delta=1000):
    conn.execute(
        "INSERT INTO stock_movements "
        "(ingredient_id, movement_type, delta_milli, staff_id, created_at) "
        "VALUES (?, 'received', ?, ?, ?)",
        (ingredient_id, delta, staff_id, created_at),
    )


@pytest.fixture
def flour(conn, manager):
    return inventory.create_ingredient(conn, manager, "Flour", "kg", 250, 2000)


# Permissions


def test_cashier_cannot_read_or_write_stock(conn, cashier, flour):
    # The cashier has RECORD_SALES, which is not enough to see stock.
    calls = [
        lambda: inventory.list_ingredients(conn, cashier),
        lambda: inventory.get_ingredient(conn, cashier, flour["id"]),
        lambda: inventory.list_low_stock(conn, cashier),
        lambda: inventory.list_movements(conn, cashier),
        lambda: inventory.create_ingredient(conn, cashier, "Salt", "kg"),
        lambda: inventory.update_ingredient(conn, cashier, flour["id"], unit_cost_cents=1),
        lambda: inventory.set_ingredient_active(conn, cashier, flour["id"], False),
        lambda: inventory.receive_stock(conn, cashier, flour["id"], 1000),
        lambda: inventory.record_waste(conn, cashier, flour["id"], 1000),
        lambda: inventory.adjust_stock(conn, cashier, flour["id"], 1000),
        lambda: inventory.record_count(conn, cashier, flour["id"], 1000, ""),
    ]
    for call in calls:
        with pytest.raises(PermissionDeniedError):
            call()
    assert movement_rows(conn) == []
    assert on_hand(conn, flour["id"]) == 0


@pytest.mark.parametrize("role_fixture", ["manager", "stock_clerk"])
def test_managers_and_clerks_can_read_and_write_stock(conn, request, role_fixture):
    actor = request.getfixturevalue(role_fixture)
    created = inventory.create_ingredient(conn, actor, "Salt", "kg", 10, 500)
    updated = inventory.update_ingredient(conn, actor, created["id"], low_milli=600)
    assert updated["low_milli"] == 600
    assert inventory.receive_stock(conn, actor, created["id"], 2000)["staff_id"] == actor.id
    assert inventory.record_waste(conn, actor, created["id"], 100)["delta_milli"] == -100
    assert inventory.adjust_stock(conn, actor, created["id"], -100)["delta_milli"] == -100
    assert inventory.record_count(conn, actor, created["id"], 1500, "")["delta_milli"] == -300
    assert inventory.set_ingredient_active(conn, actor, created["id"], False)["active"] is False
    assert [row["name"] for row in inventory.list_ingredients(conn, actor)] == ["Salt"]
    assert inventory.get_ingredient(conn, actor, created["id"])["on_hand_milli"] == 1500
    assert inventory.list_low_stock(conn, actor) == []
    assert len(inventory.list_movements(conn, actor)) == 4


def test_permission_is_checked_before_validation(conn, cashier):
    with pytest.raises(PermissionDeniedError):
        inventory.create_ingredient(conn, cashier, "", "")


# Ingredients


def test_create_ingredient_starts_with_no_stock(conn, stock_clerk):
    created = inventory.create_ingredient(conn, stock_clerk, " Salt ", " kg ", 150, 500)
    assert created == {
        "id": created["id"],
        "name": "Salt",
        "unit": "kg",
        "unit_cost_cents": 150,
        "on_hand_milli": 0,
        "low_milli": 500,
        "active": True,
    }
    assert audit_rows(conn, "inventory.ingredient_created")[0]["staff_id"] == stock_clerk.id


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": ""},
        {"name": "  "},
        {"name": None},
        {"unit": ""},
        {"unit": "   "},
        {"unit_cost_cents": -1},
        {"unit_cost_cents": 1.5},
        {"unit_cost_cents": "10"},
        {"unit_cost_cents": True},
        {"unit_cost_cents": 2**70},
        {"low_milli": -1},
        {"low_milli": 0.5},
        {"low_milli": 2**70},
    ],
)
def test_invalid_ingredient_fields_are_rejected(conn, manager, kwargs):
    with pytest.raises(ValidationError):
        inventory.create_ingredient(conn, manager, **{"name": "Salt", "unit": "kg", **kwargs})
    assert inventory.list_ingredients(conn, manager) == []


def test_duplicate_ingredient_name_conflicts_ignoring_case(conn, manager, flour):
    with pytest.raises(ConflictError, match="FLOUR"):
        inventory.create_ingredient(conn, manager, "FLOUR", "kg")
    salt = inventory.create_ingredient(conn, manager, "Salt", "kg")
    with pytest.raises(ConflictError):
        inventory.update_ingredient(conn, manager, salt["id"], name="flour")
    assert inventory.get_ingredient(conn, manager, salt["id"])["name"] == "Salt"
    assert not conn.in_transaction


def test_update_ingredient_changes_only_given_fields_and_audits_cost(conn, manager, flour):
    inventory.receive_stock(conn, manager, flour["id"], 3000)
    updated = inventory.update_ingredient(conn, manager, flour["id"], unit_cost_cents=300)
    assert updated["unit_cost_cents"] == 300
    assert (updated["name"], updated["unit"], updated["low_milli"]) == ("Flour", "kg", 2000)
    assert updated["on_hand_milli"] == 3000
    details = audit_rows(conn, "inventory.ingredient_updated")[0]["details"]
    assert "2.50" in details and "3.00" in details


def test_update_ingredient_with_no_change_writes_no_audit(conn, manager, flour):
    inventory.update_ingredient(conn, manager, flour["id"], name="Flour", low_milli=2000)
    assert audit_rows(conn, "inventory.ingredient_updated") == []


def test_create_rolls_back_when_audit_fails(conn, manager, monkeypatch):
    monkeypatch.setattr(audit, "record", fail_audit)
    with pytest.raises(RuntimeError):
        inventory.create_ingredient(conn, manager, "Salt", "kg")
    monkeypatch.undo()
    assert inventory.list_ingredients(conn, manager) == []
    assert not conn.in_transaction


def test_missing_ingredient_raises_not_found(conn, manager):
    calls = [
        lambda: inventory.get_ingredient(conn, manager, 999),
        lambda: inventory.update_ingredient(conn, manager, 999, name="X"),
        lambda: inventory.set_ingredient_active(conn, manager, 999, False),
        lambda: inventory.receive_stock(conn, manager, 999, 1),
        lambda: inventory.record_waste(conn, manager, 999, 1),
        lambda: inventory.adjust_stock(conn, manager, 999, 1),
        lambda: inventory.record_count(conn, manager, 999, 1, ""),
    ]
    for call in calls:
        with pytest.raises(NotFoundError):
            call()
    assert movement_rows(conn) == []


def test_list_ingredients_sorted_and_can_exclude_inactive(conn, manager, flour):
    apple = inventory.create_ingredient(conn, manager, "apple", "kg")
    assert [i["id"] for i in inventory.list_ingredients(conn, manager)] == [apple["id"], flour["id"]]
    inventory.set_ingredient_active(conn, manager, apple["id"], False)
    assert [i["id"] for i in inventory.list_ingredients(conn, manager, include_inactive=False)] == [
        flour["id"]
    ]


# Deactivation against recipes


def test_deactivating_ingredient_in_active_recipes_names_the_items(conn, manager, flour):
    category = menu.create_category(conn, manager, "Mains")
    burger = menu.create_item(conn, manager, category["id"], "Burger", 100)
    pizza = menu.create_item(conn, manager, category["id"], "Pizza", 100)
    line = [{"ingredient_id": flour["id"], "qty_milli": 100}]
    menu.set_recipe(conn, manager, burger["id"], line)
    menu.set_recipe(conn, manager, pizza["id"], line)

    with pytest.raises(ConflictError) as raised:
        inventory.set_ingredient_active(conn, manager, flour["id"], False)
    assert "Burger" in str(raised.value) and "Pizza" in str(raised.value)
    assert inventory.get_ingredient(conn, manager, flour["id"])["active"] is True
    assert audit_rows(conn, "inventory.ingredient_deactivated") == []
    assert not conn.in_transaction

    menu.set_item_active(conn, manager, burger["id"], False)
    with pytest.raises(ConflictError) as raised:
        inventory.set_ingredient_active(conn, manager, flour["id"], False)
    assert "Pizza" in str(raised.value) and "Burger" not in str(raised.value)

    menu.set_recipe(conn, manager, pizza["id"], [])
    assert inventory.set_ingredient_active(conn, manager, flour["id"], False)["active"] is False
    assert len(audit_rows(conn, "inventory.ingredient_deactivated")) == 1


def test_active_item_in_inactive_category_still_blocks_deactivation(conn, manager, flour):
    category = menu.create_category(conn, manager, "Mains")
    burger = menu.create_item(conn, manager, category["id"], "Burger", 100)
    menu.set_recipe(conn, manager, burger["id"], [{"ingredient_id": flour["id"], "qty_milli": 1}])
    menu.set_category_active(conn, manager, category["id"], False)
    with pytest.raises(ConflictError, match="Burger"):
        inventory.set_ingredient_active(conn, manager, flour["id"], False)


def test_activate_is_idempotent_and_audits_real_changes(conn, manager, flour):
    inventory.set_ingredient_active(conn, manager, flour["id"], True)
    assert audit_rows(conn, "inventory.ingredient_activated") == []
    inventory.set_ingredient_active(conn, manager, flour["id"], False)
    inventory.set_ingredient_active(conn, manager, flour["id"], True)
    assert len(audit_rows(conn, "inventory.ingredient_activated")) == 1


# Movements


def test_receive_adds_a_movement_and_the_trigger_updates_on_hand(conn, stock_clerk, flour):
    movement = inventory.receive_stock(conn, stock_clerk, flour["id"], 1500, "  Delivery 12 ")
    assert movement["movement_type"] == "received"
    assert movement["delta_milli"] == 1500
    assert movement["staff_id"] == stock_clerk.id
    assert movement["note"] == "Delivery 12"
    assert movement["created_at"]
    assert movement["sale_id"] is None
    assert on_hand(conn, flour["id"]) == 1500


def test_waste_is_entered_positive_and_stored_negative(conn, stock_clerk, flour):
    inventory.receive_stock(conn, stock_clerk, flour["id"], 1500)
    movement = inventory.record_waste(conn, stock_clerk, flour["id"], 400, "Dropped")
    assert movement["movement_type"] == "wasted"
    assert movement["delta_milli"] == -400
    assert on_hand(conn, flour["id"]) == 1100
    row = audit_rows(conn, "stock.wasted")[0]
    assert row["staff_id"] == stock_clerk.id
    assert "Flour" in row["details"] and "0.4" in row["details"] and "Dropped" in row["details"]


@pytest.mark.parametrize("delta", [700, -700])
def test_adjust_applies_either_direction_and_audits(conn, manager, flour, delta):
    movement = inventory.adjust_stock(conn, manager, flour["id"], delta, "Found error")
    assert (movement["movement_type"], movement["delta_milli"]) == ("adjusted", delta)
    assert on_hand(conn, flour["id"]) == delta
    details = audit_rows(conn, "stock.adjusted")[0]["details"]
    assert "Flour" in details and "0.7" in details and "Found error" in details


def test_stock_may_go_negative_through_waste_and_adjustment(conn, manager, flour):
    inventory.record_waste(conn, manager, flour["id"], 500)
    inventory.adjust_stock(conn, manager, flour["id"], -500)
    assert on_hand(conn, flour["id"]) == -1000


def test_note_is_optional(conn, manager, flour):
    assert inventory.receive_stock(conn, manager, flour["id"], 1)["note"] == ""
    assert inventory.receive_stock(conn, manager, flour["id"], 1, None)["note"] == ""
    with pytest.raises(ValidationError):
        inventory.receive_stock(conn, manager, flour["id"], 1, 5)


@pytest.mark.parametrize(
    "qty", [0, -1, 1.5, "100", None, True, inventory.MAX_QTY_MILLI + 1, 2**70]
)
def test_invalid_quantities_are_rejected(conn, manager, flour, qty):
    with pytest.raises(ValidationError):
        inventory.receive_stock(conn, manager, flour["id"], qty)
    with pytest.raises(ValidationError):
        inventory.record_waste(conn, manager, flour["id"], qty)
    assert movement_rows(conn) == []


@pytest.mark.parametrize("delta", [0, 1.5, "1", None, True, -inventory.MAX_QTY_MILLI - 1, 2**70])
def test_invalid_adjustments_are_rejected(conn, manager, flour, delta):
    with pytest.raises(ValidationError):
        inventory.adjust_stock(conn, manager, flour["id"], delta)
    assert movement_rows(conn) == []
    assert audit_rows(conn, "stock.adjusted") == []


def test_inactive_ingredient_stock_cannot_change(conn, manager, flour):
    inventory.receive_stock(conn, manager, flour["id"], 1000)
    inventory.set_ingredient_active(conn, manager, flour["id"], False)
    calls = [
        lambda: inventory.receive_stock(conn, manager, flour["id"], 1),
        lambda: inventory.record_waste(conn, manager, flour["id"], 1),
        lambda: inventory.adjust_stock(conn, manager, flour["id"], 1),
        lambda: inventory.record_count(conn, manager, flour["id"], 1, ""),
    ]
    for call in calls:
        with pytest.raises(ConflictError, match="Flour"):
            call()
    assert on_hand(conn, flour["id"]) == 1000
    assert len(movement_rows(conn)) == 1


@pytest.mark.parametrize(
    "write",
    [
        lambda conn, actor, ingredient_id: inventory.record_waste(conn, actor, ingredient_id, 100),
        lambda conn, actor, ingredient_id: inventory.adjust_stock(conn, actor, ingredient_id, 100),
        lambda conn, actor, ingredient_id: inventory.record_count(conn, actor, ingredient_id, 100, ""),
    ],
)
def test_movement_rolls_back_when_audit_fails(conn, manager, flour, monkeypatch, write):
    monkeypatch.setattr(audit, "record", fail_audit)
    with pytest.raises(RuntimeError):
        write(conn, manager, flour["id"])
    monkeypatch.undo()
    assert movement_rows(conn) == []
    assert on_hand(conn, flour["id"]) == 0
    assert not conn.in_transaction


def test_updating_on_hand_comes_only_from_the_ledger(conn, manager, flour):
    inventory.receive_stock(conn, manager, flour["id"], 1000)
    inventory.record_waste(conn, manager, flour["id"], 300)
    inventory.adjust_stock(conn, manager, flour["id"], 50)
    inventory.record_count(conn, manager, flour["id"], 2000, "")
    total = conn.execute("SELECT SUM(delta_milli) FROM stock_movements").fetchone()[0]
    assert on_hand(conn, flour["id"]) == total == 2000


# Counts


@pytest.mark.parametrize("counted, delta", [(800, -200), (1300, 300), (0, -1000)])
def test_count_writes_the_difference_as_an_adjustment(conn, stock_clerk, flour, counted, delta):
    inventory.receive_stock(conn, stock_clerk, flour["id"], 1000)
    movement = inventory.record_count(conn, stock_clerk, flour["id"], counted, "Weekly")
    assert movement["movement_type"] == "adjusted"
    assert movement["delta_milli"] == delta
    assert on_hand(conn, flour["id"]) == counted
    assert "Weekly" in movement["note"]
    assert "counted" in movement["note"]
    row = audit_rows(conn, "stock.counted")[0]
    assert row["staff_id"] == stock_clerk.id
    assert "Flour" in row["details"] and "Weekly" in row["details"]


def test_zero_difference_count_writes_no_movement_but_is_audited(conn, manager, flour):
    inventory.receive_stock(conn, manager, flour["id"], 1000)
    assert inventory.record_count(conn, manager, flour["id"], 1000, "") is None
    assert len(movement_rows(conn)) == 1
    assert on_hand(conn, flour["id"]) == 1000
    assert "counted 1" in audit_rows(conn, "stock.counted")[0]["details"]


def test_count_corrects_negative_stock(conn, manager, flour):
    inventory.adjust_stock(conn, manager, flour["id"], -500)
    assert inventory.record_count(conn, manager, flour["id"], 200, "")["delta_milli"] == 700
    assert on_hand(conn, flour["id"]) == 200


@pytest.mark.parametrize("counted", [-1, 1.5, "1", None, True, 2**70])
def test_invalid_counted_quantity_is_rejected(conn, manager, flour, counted):
    with pytest.raises(ValidationError):
        inventory.record_count(conn, manager, flour["id"], counted, "")
    assert movement_rows(conn) == []
    assert audit_rows(conn, "stock.counted") == []


# Low stock


def test_low_stock_includes_at_threshold_and_below(conn, manager, flour):
    # flour: threshold 2000.
    inventory.receive_stock(conn, manager, flour["id"], 2001)
    assert inventory.list_low_stock(conn, manager) == []
    assert inventory.low_stock_count(conn) == 0

    inventory.record_waste(conn, manager, flour["id"], 1)
    low = inventory.list_low_stock(conn, manager)
    assert [row["id"] for row in low] == [flour["id"]]
    assert low[0]["is_negative"] is False
    assert inventory.low_stock_count(conn) == 1


def test_negative_stock_is_flagged_and_listed_first(conn, manager, flour):
    salt = inventory.create_ingredient(conn, manager, "Salt", "kg", 0, 100)
    inventory.adjust_stock(conn, manager, salt["id"], -1)
    low = inventory.list_low_stock(conn, manager)
    assert [(row["name"], row["is_negative"]) for row in low] == [("Salt", True), ("Flour", False)]
    assert low[0]["on_hand_milli"] == -1


def test_zero_threshold_at_zero_stock_counts_as_low(conn, manager):
    inventory.create_ingredient(conn, manager, "Salt", "kg", 0, 0)
    assert inventory.low_stock_count(conn) == 1


def test_inactive_ingredients_are_not_low_stock(conn, manager, flour):
    assert inventory.low_stock_count(conn) == 1
    inventory.set_ingredient_active(conn, manager, flour["id"], False)
    assert inventory.list_low_stock(conn, manager) == []
    assert inventory.low_stock_count(conn) == 0


def test_low_stock_count_takes_only_the_connection(conn, flour):
    # No actor argument, so the cashier's home screen can use it too.
    assert inventory.low_stock_count(conn) == 1


# Movement history


def test_movements_are_newest_first_with_names(conn, stock_clerk, flour):
    first = inventory.receive_stock(conn, stock_clerk, flour["id"], 1000)
    second = inventory.record_waste(conn, stock_clerk, flour["id"], 100, "Spill")
    rows = inventory.list_movements(conn, stock_clerk)
    assert [row["id"] for row in rows] == [second["id"], first["id"]]
    assert rows[0]["ingredient_name"] == "Flour"
    assert rows[0]["unit"] == "kg"
    assert rows[0]["username"] == "clerk"
    assert rows[0]["full_name"] == "Clerk"
    assert rows[0]["note"] == "Spill"
    assert rows[0]["movement_type"] == "wasted"


def test_movements_order_by_time_before_id(conn, manager, flour):
    insert_movement(conn, flour["id"], manager.id, "2026-03-02T00:00:00+00:00", delta=2)
    insert_movement(conn, flour["id"], manager.id, "2026-03-03T00:00:00+00:00", delta=3)
    insert_movement(conn, flour["id"], manager.id, "2026-03-01T00:00:00+00:00", delta=1)
    assert [r["delta_milli"] for r in inventory.list_movements(conn, manager)] == [3, 2, 1]


def test_movements_filter_by_ingredient(conn, manager, flour):
    salt = inventory.create_ingredient(conn, manager, "Salt", "kg")
    inventory.receive_stock(conn, manager, flour["id"], 1)
    inventory.receive_stock(conn, manager, salt["id"], 2)
    rows = inventory.list_movements(conn, manager, ingredient_id=salt["id"])
    assert [row["delta_milli"] for row in rows] == [2]


def test_movements_date_range_covers_whole_local_days(conn, manager, flour):
    insert_movement(conn, flour["id"], manager.id, local_utc(2026, 3, 9, 23, 59, 59), delta=1)
    insert_movement(conn, flour["id"], manager.id, local_utc(2026, 3, 10), delta=2)
    insert_movement(conn, flour["id"], manager.id, local_utc(2026, 3, 11, 23, 59, 59), delta=3)
    insert_movement(conn, flour["id"], manager.id, local_utc(2026, 3, 12), delta=4)

    def deltas(**kwargs):
        return [row["delta_milli"] for row in inventory.list_movements(conn, manager, **kwargs)]

    assert deltas(date_from="2026-03-10", date_to="2026-03-11") == [3, 2]
    assert deltas(date_from="2026-03-11") == [4, 3]
    assert deltas(date_to="2026-03-09") == [1]
    assert deltas(date_from="2026-03-10", date_to="2026-03-10") == [2]
    assert deltas(date_from=date(2026, 3, 10), date_to=date(2026, 3, 10)) == [2]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"date_from": "yesterday"},
        {"date_to": "2026-13-01"},
        {"date_from": 20260101},
        {"date_from": "2026-03-11", "date_to": "2026-03-10"},
        {"date_to": "9999-12-31"},
    ],
)
def test_invalid_date_ranges_are_rejected(conn, manager, kwargs):
    with pytest.raises(ValidationError):
        inventory.list_movements(conn, manager, **kwargs)


def test_extreme_early_date_never_crashes(conn, manager):
    # Whether year 1 converts depends on the platform's clock functions.
    try:
        assert inventory.list_movements(conn, manager, date_from="0001-01-01") == []
    except ValidationError:
        pass


def test_movements_paginate_without_gaps(conn, manager, flour):
    for delta in range(1, 6):
        inventory.receive_stock(conn, manager, flour["id"], delta)

    def page(limit, offset):
        rows = inventory.list_movements(conn, manager, limit=limit, offset=offset)
        return [row["delta_milli"] for row in rows]

    assert page(2, 0) == [5, 4]
    assert page(2, 2) == [3, 2]
    assert page(2, 4) == [1]
    assert page(2, 6) == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"limit": 0},
        {"limit": -1},
        {"limit": inventory.MAX_PAGE_SIZE + 1},
        {"limit": 1.5},
        {"offset": -1},
        {"offset": "1"},
    ],
)
def test_invalid_pagination_is_rejected(conn, manager, kwargs):
    with pytest.raises(ValidationError):
        inventory.list_movements(conn, manager, **kwargs)


def test_sold_movements_appear_in_history(conn, manager, flour):
    sale_id = conn.execute(
        "INSERT INTO sales (staff_id, payment_method, subtotal_cents, tax_rate_bp, tax_cents, "
        "total_cents, created_at) VALUES (?, 'cash', 0, 0, 0, 0, ?)",
        (manager.id, "2026-03-01T00:00:00+00:00"),
    ).lastrowid
    conn.execute(
        "INSERT INTO stock_movements "
        "(ingredient_id, movement_type, delta_milli, staff_id, sale_id, created_at) "
        "VALUES (?, 'sold', -5, ?, ?, ?)",
        (flour["id"], manager.id, sale_id, "2026-03-01T00:00:00+00:00"),
    )
    assert inventory.list_movements(conn, manager)[0]["sale_id"] == sale_id
    assert on_hand(conn, flour["id"]) == -5


def test_non_duplicate_integrity_errors_are_not_disguised(conn, manager, monkeypatch):
    # An unrelated constraint failure is a bug and must not read as a duplicate name.
    def fail_integrity(*args, **kwargs):
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")

    monkeypatch.setattr(audit, "record", fail_integrity)
    with pytest.raises(sqlite3.IntegrityError):
        inventory.create_ingredient(conn, manager, "Salt", "kg")
    monkeypatch.undo()
    assert inventory.list_ingredients(conn, manager) == []
