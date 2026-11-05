"""Menu categories, items and recipes (FR-M1 to FR-M4)."""

from __future__ import annotations

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


def fail_audit(*args, **kwargs):
    raise RuntimeError("audit failed")


@pytest.fixture
def category(conn, manager):
    return menu.create_category(conn, manager, "Mains")


@pytest.fixture
def item(conn, manager, category):
    return menu.create_item(conn, manager, category["id"], "Burger", 1250)


@pytest.fixture
def flour(conn, manager):
    return inventory.create_ingredient(conn, manager, "Flour", "kg", 200, 1000)


@pytest.fixture
def beef(conn, manager):
    return inventory.create_ingredient(conn, manager, "Beef", "kg", 900, 1000)


# Permissions


@pytest.mark.parametrize("role_fixture", ["cashier", "stock_clerk"])
def test_only_managers_can_write_the_menu(conn, request, role_fixture, category, item, flour):
    actor = request.getfixturevalue(role_fixture)
    writes = [
        lambda: menu.create_category(conn, actor, "Drinks"),
        lambda: menu.update_category(conn, actor, category["id"], name="X"),
        lambda: menu.set_category_active(conn, actor, category["id"], False),
        lambda: menu.create_item(conn, actor, category["id"], "Fries", 300),
        lambda: menu.update_item(conn, actor, item["id"], price_cents=1),
        lambda: menu.set_item_active(conn, actor, item["id"], False),
        lambda: menu.set_recipe(
            conn, actor, item["id"], [{"ingredient_id": flour["id"], "qty_milli": 1}]
        ),
    ]
    for write in writes:
        with pytest.raises(PermissionDeniedError):
            write()
    assert menu.get_item(conn, actor, item["id"])["price_cents"] == 1250
    assert menu.get_recipe(conn, actor, item["id"]) == []
    assert [row["name"] for row in menu.list_categories(conn, actor)] == ["Mains"]


def test_permission_is_checked_before_validation(conn, cashier):
    with pytest.raises(PermissionDeniedError):
        menu.create_category(conn, cashier, "   ")


@pytest.mark.parametrize("role_fixture", ["manager", "cashier", "stock_clerk"])
def test_every_role_can_read_the_menu(conn, request, role_fixture, item):
    actor = request.getfixturevalue(role_fixture)
    assert [row["name"] for row in menu.list_categories(conn, actor)] == ["Mains"]
    assert [row["name"] for row in menu.list_items(conn, actor)] == ["Burger"]
    assert [row["name"] for row in menu.list_sellable_items(conn, actor)] == ["Burger"]
    assert menu.get_item(conn, actor, item["id"])["name"] == "Burger"
    assert menu.get_recipe(conn, actor, item["id"]) == []


# Categories


def test_create_category_trims_name_and_audits(conn, manager):
    created = menu.create_category(conn, manager, "  Drinks ", sort_order=3)
    assert created == {"id": created["id"], "name": "Drinks", "sort_order": 3, "active": True}
    rows = audit_rows(conn, "menu.category_created")
    assert rows[0]["staff_id"] == manager.id
    assert "Drinks" in rows[0]["details"]


@pytest.mark.parametrize("name", ["", "   ", None, 5])
def test_category_name_is_required(conn, manager, name):
    with pytest.raises(ValidationError):
        menu.create_category(conn, manager, name)


@pytest.mark.parametrize("sort_order", [1.5, "1", True, None, 10**30])
def test_category_sort_order_must_be_an_int(conn, manager, sort_order):
    with pytest.raises(ValidationError):
        menu.create_category(conn, manager, "Drinks", sort_order=sort_order)


def test_duplicate_category_name_conflicts_ignoring_case(conn, manager, category):
    with pytest.raises(ConflictError, match="MAINS"):
        menu.create_category(conn, manager, "MAINS")
    assert len(menu.list_categories(conn, manager)) == 1
    assert not conn.in_transaction


def test_rename_to_existing_category_conflicts(conn, manager, category):
    other = menu.create_category(conn, manager, "Drinks")
    with pytest.raises(ConflictError):
        menu.update_category(conn, manager, other["id"], name="mains")
    assert menu.list_categories(conn, manager)[0]["name"] == "Drinks"


def test_update_category_changes_only_given_fields(conn, manager, category):
    updated = menu.update_category(conn, manager, category["id"], sort_order=7)
    assert (updated["name"], updated["sort_order"]) == ("Mains", 7)
    updated = menu.update_category(conn, manager, category["id"], name="Main dishes")
    assert (updated["name"], updated["sort_order"]) == ("Main dishes", 7)
    assert len(audit_rows(conn, "menu.category_updated")) == 2


def test_categories_are_sorted_and_can_exclude_inactive(conn, manager):
    late = menu.create_category(conn, manager, "A late", sort_order=2)
    menu.create_category(conn, manager, "Early", sort_order=1)
    menu.set_category_active(conn, manager, late["id"], False)
    assert [c["name"] for c in menu.list_categories(conn, manager)] == ["Early", "A late"]
    assert [c["name"] for c in menu.list_categories(conn, manager, include_inactive=False)] == [
        "Early"
    ]


def test_missing_category_raises_not_found(conn, manager):
    with pytest.raises(NotFoundError):
        menu.update_category(conn, manager, 999, name="X")
    with pytest.raises(NotFoundError):
        menu.set_category_active(conn, manager, 999, False)
    with pytest.raises(NotFoundError):
        menu.create_item(conn, manager, 999, "Burger", 100)


def test_category_active_toggle_audits_only_real_changes(conn, manager, category):
    assert menu.set_category_active(conn, manager, category["id"], True)["active"] is True
    assert audit_rows(conn, "menu.category_activated") == []
    assert menu.set_category_active(conn, manager, category["id"], False)["active"] is False
    assert menu.set_category_active(conn, manager, category["id"], False)["active"] is False
    assert len(audit_rows(conn, "menu.category_deactivated")) == 1
    assert menu.set_category_active(conn, manager, category["id"], True)["active"] is True
    assert len(audit_rows(conn, "menu.category_activated")) == 1


# Items


def test_create_item_returns_full_result_and_audits_price(conn, manager, category):
    created = menu.create_item(conn, manager, category["id"], " Fries ", 350)
    assert created == {
        "id": created["id"],
        "category_id": category["id"],
        "category_name": "Mains",
        "category_active": True,
        "name": "Fries",
        "price_cents": 350,
        "active": True,
        "has_recipe": False,
    }
    assert "3.50" in audit_rows(conn, "menu.item_created")[0]["details"]


def test_free_item_is_allowed(conn, manager, category):
    assert menu.create_item(conn, manager, category["id"], "Water", 0)["price_cents"] == 0


@pytest.mark.parametrize(
    "price", [-1, 12.5, "1250", None, True, menu.MAX_PRICE_CENTS + 1, 2**70]
)
def test_invalid_price_is_rejected(conn, manager, category, item, price):
    with pytest.raises(ValidationError):
        menu.create_item(conn, manager, category["id"], "Fries", price)
    if price is not None:
        with pytest.raises(ValidationError):
            menu.update_item(conn, manager, item["id"], price_cents=price)
    assert menu.get_item(conn, manager, item["id"])["price_cents"] == 1250


def test_same_item_name_allowed_only_in_different_categories(conn, manager, category, item):
    with pytest.raises(ConflictError, match="burger"):
        menu.create_item(conn, manager, category["id"], "burger", 100)
    drinks = menu.create_category(conn, manager, "Drinks")
    other = menu.create_item(conn, manager, drinks["id"], "Burger", 100)
    with pytest.raises(ConflictError):
        menu.update_item(conn, manager, other["id"], category_id=category["id"])
    assert menu.get_item(conn, manager, other["id"])["category_id"] == drinks["id"]
    assert not conn.in_transaction


def test_price_change_audits_old_and_new_price(conn, manager, item):
    updated = menu.update_item(conn, manager, item["id"], price_cents=1399)
    assert updated["price_cents"] == 1399
    rows = audit_rows(conn, "menu.price_changed")
    assert len(rows) == 1
    assert rows[0]["staff_id"] == manager.id
    assert "12.50" in rows[0]["details"] and "13.99" in rows[0]["details"]
    assert "Burger" in rows[0]["details"]
    assert audit_rows(conn, "menu.item_updated") == []


def test_same_price_writes_no_price_audit(conn, manager, item):
    menu.update_item(conn, manager, item["id"], price_cents=1250)
    assert audit_rows(conn, "menu.price_changed") == []
    assert audit_rows(conn, "menu.item_updated") == []


def test_rename_and_move_are_audited_without_a_price_entry(conn, manager, item):
    drinks = menu.create_category(conn, manager, "Drinks")
    updated = menu.update_item(conn, manager, item["id"], name="Cola", category_id=drinks["id"])
    assert (updated["name"], updated["category_name"]) == ("Cola", "Drinks")
    assert updated["price_cents"] == 1250
    details = audit_rows(conn, "menu.item_updated")[0]["details"]
    assert "Burger" in details and "Cola" in details and "Mains" in details and "Drinks" in details
    assert audit_rows(conn, "menu.price_changed") == []


def test_update_item_can_change_only_letter_case(conn, manager, item):
    assert menu.update_item(conn, manager, item["id"], name="BURGER")["name"] == "BURGER"


def test_update_item_rejects_missing_targets(conn, manager, item):
    with pytest.raises(NotFoundError):
        menu.update_item(conn, manager, 999, name="X")
    with pytest.raises(NotFoundError):
        menu.update_item(conn, manager, item["id"], category_id=999)
    with pytest.raises(NotFoundError):
        menu.get_item(conn, manager, 999)
    with pytest.raises(NotFoundError):
        menu.set_item_active(conn, manager, 999, False)


def test_update_item_rolls_back_when_audit_fails(conn, manager, item, monkeypatch):
    monkeypatch.setattr(audit, "record", fail_audit)
    with pytest.raises(RuntimeError):
        menu.update_item(conn, manager, item["id"], price_cents=1, name="Changed")
    monkeypatch.undo()
    current = menu.get_item(conn, manager, item["id"])
    assert (current["name"], current["price_cents"]) == ("Burger", 1250)
    assert not conn.in_transaction


def test_create_rolls_back_when_audit_fails(conn, manager, category, monkeypatch):
    monkeypatch.setattr(audit, "record", fail_audit)
    with pytest.raises(RuntimeError):
        menu.create_item(conn, manager, category["id"], "Fries", 100)
    with pytest.raises(RuntimeError):
        menu.create_category(conn, manager, "Drinks")
    monkeypatch.undo()
    assert [row["name"] for row in menu.list_items(conn, manager)] == []
    assert [row["name"] for row in menu.list_categories(conn, manager)] == ["Mains"]


def test_only_active_items_in_active_categories_are_sellable(conn, manager, category, item):
    fries = menu.create_item(conn, manager, category["id"], "Fries", 300)
    drinks = menu.create_category(conn, manager, "Drinks")
    cola = menu.create_item(conn, manager, drinks["id"], "Cola", 200)
    assert {row["id"] for row in menu.list_sellable_items(conn, manager)} == {
        item["id"],
        fries["id"],
        cola["id"],
    }

    menu.set_item_active(conn, manager, fries["id"], False)
    menu.set_category_active(conn, manager, drinks["id"], False)
    assert [row["id"] for row in menu.list_sellable_items(conn, manager)] == [item["id"]]

    # The inactive category's item keeps its own flag and returns with the category.
    assert menu.get_item(conn, manager, cola["id"])["active"] is True
    assert menu.get_item(conn, manager, cola["id"])["category_active"] is False
    menu.set_category_active(conn, manager, drinks["id"], True)
    assert {row["id"] for row in menu.list_sellable_items(conn, manager)} == {
        item["id"],
        cola["id"],
    }


def test_list_items_filters_and_orders(conn, manager, category):
    drinks = menu.create_category(conn, manager, "Aaa drinks", sort_order=1)
    zebra = menu.create_item(conn, manager, category["id"], "Zebra", 1)
    apple = menu.create_item(conn, manager, category["id"], "apple", 1)
    cola = menu.create_item(conn, manager, drinks["id"], "Cola", 1)
    assert [i["id"] for i in menu.list_items(conn, manager)] == [apple["id"], zebra["id"], cola["id"]]
    assert [i["id"] for i in menu.list_items(conn, manager, category_id=drinks["id"])] == [cola["id"]]
    menu.set_item_active(conn, manager, zebra["id"], False)
    assert [i["id"] for i in menu.list_items(conn, manager, include_inactive=False)] == [
        apple["id"],
        cola["id"],
    ]
    assert len(menu.list_items(conn, manager)) == 3


def test_item_active_toggle_audits_only_real_changes(conn, manager, item):
    menu.set_item_active(conn, manager, item["id"], True)
    assert audit_rows(conn, "menu.item_activated") == []
    assert menu.set_item_active(conn, manager, item["id"], False)["active"] is False
    menu.set_item_active(conn, manager, item["id"], False)
    assert len(audit_rows(conn, "menu.item_deactivated")) == 1


# Recipes


def test_set_recipe_stores_lines_and_marks_has_recipe(conn, manager, item, flour, beef):
    lines = menu.set_recipe(
        conn,
        manager,
        item["id"],
        [
            {"ingredient_id": flour["id"], "qty_milli": 100},
            {"ingredient_id": beef["id"], "qty_milli": 250},
        ],
    )
    assert lines == [
        {
            "ingredient_id": beef["id"],
            "ingredient_name": "Beef",
            "unit": "kg",
            "qty_milli": 250,
            "ingredient_active": True,
        },
        {
            "ingredient_id": flour["id"],
            "ingredient_name": "Flour",
            "unit": "kg",
            "qty_milli": 100,
            "ingredient_active": True,
        },
    ]
    assert menu.get_recipe(conn, manager, item["id"]) == lines
    assert menu.get_item(conn, manager, item["id"])["has_recipe"] is True
    assert menu.list_items(conn, manager)[0]["has_recipe"] is True
    assert menu.list_sellable_items(conn, manager)[0]["has_recipe"] is True
    details = audit_rows(conn, "menu.recipe_changed")[0]["details"]
    assert "Beef 0.25 kg" in details and "no recipe" in details


def test_set_recipe_replaces_the_whole_recipe(conn, manager, item, flour, beef):
    menu.set_recipe(conn, manager, item["id"], [{"ingredient_id": flour["id"], "qty_milli": 100}])
    menu.set_recipe(conn, manager, item["id"], [{"ingredient_id": beef["id"], "qty_milli": 300}])
    assert [
        (line["ingredient_id"], line["qty_milli"])
        for line in menu.get_recipe(conn, manager, item["id"])
    ] == [(beef["id"], 300)]


def test_empty_recipe_clears_it(conn, manager, item, flour):
    menu.set_recipe(conn, manager, item["id"], [{"ingredient_id": flour["id"], "qty_milli": 100}])
    assert menu.set_recipe(conn, manager, item["id"], []) == []
    assert menu.get_item(conn, manager, item["id"])["has_recipe"] is False


def test_identical_recipe_writes_no_audit(conn, manager, item, flour):
    lines = [{"ingredient_id": flour["id"], "qty_milli": 100}]
    menu.set_recipe(conn, manager, item["id"], lines)
    menu.set_recipe(conn, manager, item["id"], lines)
    assert len(audit_rows(conn, "menu.recipe_changed")) == 1


@pytest.mark.parametrize(
    "qty", [0, -1, 1.5, "100", None, True, menu.MAX_RECIPE_QTY_MILLI + 1, 2**70]
)
def test_recipe_quantity_must_be_a_positive_int(conn, manager, item, flour, qty):
    with pytest.raises(ValidationError):
        menu.set_recipe(conn, manager, item["id"], [{"ingredient_id": flour["id"], "qty_milli": qty}])
    assert menu.get_recipe(conn, manager, item["id"]) == []


@pytest.mark.parametrize(
    "lines",
    [None, 5, "abc", [5], [{}], [{"ingredient_id": 1}], [{"qty_milli": 1}], [{"ingredient_id": "1", "qty_milli": 1}]],
)
def test_malformed_recipe_lines_are_rejected(conn, manager, item, flour, lines):
    with pytest.raises(ValidationError):
        menu.set_recipe(conn, manager, item["id"], lines)


def test_recipe_rejects_duplicate_ingredient(conn, manager, item, flour):
    line = {"ingredient_id": flour["id"], "qty_milli": 100}
    with pytest.raises(ValidationError, match="once"):
        menu.set_recipe(conn, manager, item["id"], [line, line])


def test_failed_set_recipe_keeps_the_previous_recipe(conn, manager, item, flour, beef):
    menu.set_recipe(conn, manager, item["id"], [{"ingredient_id": flour["id"], "qty_milli": 100}])
    inventory.set_ingredient_active(conn, manager, beef["id"], False)
    previous = menu.get_recipe(conn, manager, item["id"])

    with pytest.raises(NotFoundError):
        menu.set_recipe(conn, manager, item["id"], [{"ingredient_id": 999, "qty_milli": 1}])
    with pytest.raises(ConflictError, match="Beef"):
        menu.set_recipe(conn, manager, item["id"], [{"ingredient_id": beef["id"], "qty_milli": 1}])
    assert menu.get_recipe(conn, manager, item["id"]) == previous
    assert not conn.in_transaction


def test_set_recipe_rolls_back_replacement_when_audit_fails(
    conn, manager, item, flour, beef, monkeypatch
):
    menu.set_recipe(conn, manager, item["id"], [{"ingredient_id": flour["id"], "qty_milli": 100}])
    previous = menu.get_recipe(conn, manager, item["id"])
    monkeypatch.setattr(audit, "record", fail_audit)
    with pytest.raises(RuntimeError):
        menu.set_recipe(conn, manager, item["id"], [{"ingredient_id": beef["id"], "qty_milli": 5}])
    monkeypatch.undo()
    assert menu.get_recipe(conn, manager, item["id"]) == previous


def test_recipe_for_missing_item_raises_not_found(conn, manager, flour):
    with pytest.raises(NotFoundError):
        menu.get_recipe(conn, manager, 999)
    with pytest.raises(NotFoundError):
        menu.set_recipe(conn, manager, 999, [{"ingredient_id": flour["id"], "qty_milli": 1}])


def test_item_cannot_be_reactivated_with_an_inactive_ingredient(conn, manager, item, flour):
    menu.set_recipe(conn, manager, item["id"], [{"ingredient_id": flour["id"], "qty_milli": 100}])
    menu.set_item_active(conn, manager, item["id"], False)
    inventory.set_ingredient_active(conn, manager, flour["id"], False)

    with pytest.raises(ConflictError, match="Flour"):
        menu.set_item_active(conn, manager, item["id"], True)
    assert menu.get_item(conn, manager, item["id"])["active"] is False
    assert menu.get_recipe(conn, manager, item["id"])[0]["ingredient_active"] is False

    inventory.set_ingredient_active(conn, manager, flour["id"], True)
    assert menu.set_item_active(conn, manager, item["id"], True)["active"] is True
