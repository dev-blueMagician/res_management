"""Menu categories, items and recipes (FR-M1 to FR-M4)."""

from __future__ import annotations

import sqlite3

from restaurant.core import (
    Actor,
    ConflictError,
    NotFoundError,
    Permission,
    ValidationError,
    format_money,
    format_quantity,
    require,
)
from restaurant.db.connection import transaction
from restaurant.services import audit

MAX_PRICE_CENTS = 99_999_999
MAX_RECIPE_QTY_MILLI = 1_000_000_000
MAX_SORT_ORDER = 1_000_000

_ITEM_SELECT = (
    "SELECT i.id, i.category_id, c.name AS category_name, c.active AS category_active, "
    "i.name, i.price_cents, i.active, "
    "EXISTS (SELECT 1 FROM recipe_items r WHERE r.menu_item_id = i.id) AS has_recipe "
    "FROM menu_items i JOIN menu_categories c ON c.id = i.category_id "
)
_ITEM_ORDER = "ORDER BY c.sort_order, c.name, i.name"


def _clean_name(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"Enter a {label}.")
    return value.strip()


def _check_int(value: object, label: str, minimum: int, maximum: int) -> int:
    # bool is an int subclass and floats must never reach money or quantity columns.
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError(f"Enter a valid {label}.")
    return value


def _price(value: int) -> str:
    return format_money(value, symbol="")


def _category_dict(row: sqlite3.Row) -> dict:
    return {**dict(row), "active": bool(row["active"])}


def _item_dict(row: sqlite3.Row) -> dict:
    return {
        **dict(row),
        "active": bool(row["active"]),
        "category_active": bool(row["category_active"]),
        "has_recipe": bool(row["has_recipe"]),
    }


def _get_category(conn: sqlite3.Connection, category_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, name, sort_order, active FROM menu_categories WHERE id = ?", (category_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("That category no longer exists.")
    return row


def _get_item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    row = conn.execute(_ITEM_SELECT + "WHERE i.id = ?", (item_id,)).fetchone()
    if row is None:
        raise NotFoundError("That menu item no longer exists.")
    return row


def _reraise_duplicate(exc: sqlite3.IntegrityError, message: str) -> None:
    """Turn a duplicate-name failure into a ConflictError; anything else is a bug."""
    if "UNIQUE" not in str(exc):
        raise exc
    raise ConflictError(message) from None


def list_categories(
    conn: sqlite3.Connection, actor: Actor, include_inactive: bool = True
) -> list[dict]:
    require(actor, Permission.VIEW_MENU)
    where = "" if include_inactive else "WHERE active = 1 "
    rows = conn.execute(
        f"SELECT id, name, sort_order, active FROM menu_categories {where}"
        "ORDER BY sort_order, name"
    ).fetchall()
    return [_category_dict(row) for row in rows]


def create_category(
    conn: sqlite3.Connection, actor: Actor, name: str, sort_order: int = 0
) -> dict:
    require(actor, Permission.EDIT_MENU)
    name = _clean_name(name, "category name")
    _check_int(sort_order, "sort order", -MAX_SORT_ORDER, MAX_SORT_ORDER)
    try:
        with transaction(conn):
            category_id = conn.execute(
                "INSERT INTO menu_categories (name, sort_order) VALUES (?, ?)",
                (name, sort_order),
            ).lastrowid
            audit.record(conn, actor.id, "menu.category_created", f"{name} (category {category_id})")
            return _category_dict(_get_category(conn, category_id))
    except sqlite3.IntegrityError as exc:
        _reraise_duplicate(exc, f'A category named "{name}" already exists.')


def update_category(
    conn: sqlite3.Connection,
    actor: Actor,
    category_id: int,
    name: str | None = None,
    sort_order: int | None = None,
) -> dict:
    """Change the given fields; a field left as None keeps its current value."""
    require(actor, Permission.EDIT_MENU)
    if name is not None:
        name = _clean_name(name, "category name")
    if sort_order is not None:
        _check_int(sort_order, "sort order", -MAX_SORT_ORDER, MAX_SORT_ORDER)
    try:
        with transaction(conn):
            old = _get_category(conn, category_id)
            new_name = old["name"] if name is None else name
            new_order = old["sort_order"] if sort_order is None else sort_order
            conn.execute(
                "UPDATE menu_categories SET name = ?, sort_order = ? WHERE id = ?",
                (new_name, new_order, category_id),
            )
            changes = []
            if new_name != old["name"]:
                changes.append(f'renamed "{old["name"]}" to "{new_name}"')
            if new_order != old["sort_order"]:
                changes.append(f'sort order {old["sort_order"]} -> {new_order}')
            if changes:
                audit.record(
                    conn,
                    actor.id,
                    "menu.category_updated",
                    f"Category {category_id}: {', '.join(changes)}",
                )
            return _category_dict(_get_category(conn, category_id))
    except sqlite3.IntegrityError as exc:
        _reraise_duplicate(exc, f'A category named "{new_name}" already exists.')


def set_category_active(
    conn: sqlite3.Connection, actor: Actor, category_id: int, active: bool
) -> dict:
    """Items in an inactive category keep their own flag but can't be sold (FR-M2)."""
    require(actor, Permission.EDIT_MENU)
    with transaction(conn):
        old = _get_category(conn, category_id)
        if bool(old["active"]) != bool(active):
            conn.execute(
                "UPDATE menu_categories SET active = ? WHERE id = ?", (int(active), category_id)
            )
            action = "menu.category_activated" if active else "menu.category_deactivated"
            audit.record(conn, actor.id, action, f'{old["name"]} (category {category_id})')
        return _category_dict(_get_category(conn, category_id))


def list_items(
    conn: sqlite3.Connection,
    actor: Actor,
    category_id: int | None = None,
    include_inactive: bool = True,
) -> list[dict]:
    """Items for the menu screen, inactive ones included unless asked otherwise."""
    require(actor, Permission.VIEW_MENU)
    conditions, params = [], []
    if category_id is not None:
        conditions.append("i.category_id = ?")
        params.append(category_id)
    if not include_inactive:
        conditions.append("i.active = 1")
    where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
    rows = conn.execute(_ITEM_SELECT + where + _ITEM_ORDER, params).fetchall()
    return [_item_dict(row) for row in rows]


def list_sellable_items(conn: sqlite3.Connection, actor: Actor) -> list[dict]:
    """Active items in active categories: the only items a sale may use (FR-M2)."""
    require(actor, Permission.VIEW_MENU)
    rows = conn.execute(
        _ITEM_SELECT + "WHERE i.active = 1 AND c.active = 1 " + _ITEM_ORDER
    ).fetchall()
    return [_item_dict(row) for row in rows]


def get_item(conn: sqlite3.Connection, actor: Actor, item_id: int) -> dict:
    require(actor, Permission.VIEW_MENU)
    return _item_dict(_get_item(conn, item_id))


def create_item(
    conn: sqlite3.Connection, actor: Actor, category_id: int, name: str, price_cents: int
) -> dict:
    require(actor, Permission.EDIT_MENU)
    name = _clean_name(name, "item name")
    _check_int(price_cents, "price", 0, MAX_PRICE_CENTS)
    try:
        with transaction(conn):
            category = _get_category(conn, category_id)
            item_id = conn.execute(
                "INSERT INTO menu_items (category_id, name, price_cents) VALUES (?, ?, ?)",
                (category_id, name, price_cents),
            ).lastrowid
            audit.record(
                conn,
                actor.id,
                "menu.item_created",
                f'{name} (item {item_id}) in {category["name"]}, price {_price(price_cents)}',
            )
            return _item_dict(_get_item(conn, item_id))
    except sqlite3.IntegrityError as exc:
        _reraise_duplicate(exc, f'"{category["name"]}" already has an item named "{name}".')


def update_item(
    conn: sqlite3.Connection,
    actor: Actor,
    item_id: int,
    name: str | None = None,
    category_id: int | None = None,
    price_cents: int | None = None,
) -> dict:
    """Change the given fields; a field left as None keeps its current value."""
    require(actor, Permission.EDIT_MENU)
    if name is not None:
        name = _clean_name(name, "item name")
    if price_cents is not None:
        _check_int(price_cents, "price", 0, MAX_PRICE_CENTS)
    try:
        with transaction(conn):
            old = _get_item(conn, item_id)
            new_name = old["name"] if name is None else name
            new_price = old["price_cents"] if price_cents is None else price_cents
            new_category = old["category_name"]
            if category_id is not None and category_id != old["category_id"]:
                new_category = _get_category(conn, category_id)["name"]
            conn.execute(
                "UPDATE menu_items SET name = ?, category_id = ?, price_cents = ? WHERE id = ?",
                (
                    new_name,
                    old["category_id"] if category_id is None else category_id,
                    new_price,
                    item_id,
                ),
            )
            if new_price != old["price_cents"]:
                audit.record(
                    conn,
                    actor.id,
                    "menu.price_changed",
                    f'{new_name} (item {item_id}): price {_price(old["price_cents"])} '
                    f"-> {_price(new_price)}",
                )
            changes = []
            if new_name != old["name"]:
                changes.append(f'renamed "{old["name"]}" to "{new_name}"')
            if new_category != old["category_name"]:
                changes.append(f'moved from "{old["category_name"]}" to "{new_category}"')
            if changes:
                audit.record(
                    conn, actor.id, "menu.item_updated", f"Item {item_id}: {', '.join(changes)}"
                )
            return _item_dict(_get_item(conn, item_id))
    except sqlite3.IntegrityError as exc:
        _reraise_duplicate(exc, f'"{new_category}" already has an item named "{new_name}".')


def set_item_active(conn: sqlite3.Connection, actor: Actor, item_id: int, active: bool) -> dict:
    require(actor, Permission.EDIT_MENU)
    with transaction(conn):
        old = _get_item(conn, item_id)
        if bool(old["active"]) != bool(active):
            if active:
                _refuse_inactive_ingredients(conn, item_id, old["name"])
            conn.execute("UPDATE menu_items SET active = ? WHERE id = ?", (int(active), item_id))
            action = "menu.item_activated" if active else "menu.item_deactivated"
            audit.record(conn, actor.id, action, f'{old["name"]} (item {item_id})')
        return _item_dict(_get_item(conn, item_id))


def _refuse_inactive_ingredients(conn: sqlite3.Connection, item_id: int, item_name: str) -> None:
    """Keep the rule that an active item's recipe uses only active ingredients."""
    names = [
        row["name"]
        for row in conn.execute(
            "SELECT g.name FROM recipe_items r JOIN ingredients g ON g.id = r.ingredient_id "
            "WHERE r.menu_item_id = ? AND g.active = 0 ORDER BY g.name",
            (item_id,),
        )
    ]
    if names:
        raise ConflictError(
            f'"{item_name}" can\'t be activated because its recipe uses inactive '
            f"ingredients: {', '.join(names)}."
        )


def _recipe_rows(conn: sqlite3.Connection, item_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT r.ingredient_id, g.name AS ingredient_name, g.unit, r.qty_milli, "
        "g.active AS ingredient_active "
        "FROM recipe_items r JOIN ingredients g ON g.id = r.ingredient_id "
        "WHERE r.menu_item_id = ? ORDER BY g.name",
        (item_id,),
    ).fetchall()


def _recipe_dict(row: sqlite3.Row) -> dict:
    return {**dict(row), "ingredient_active": bool(row["ingredient_active"])}


def get_recipe(conn: sqlite3.Connection, actor: Actor, menu_item_id: int) -> list[dict]:
    """Ingredient lines for an item; an empty list means the item has no recipe (FR-M4)."""
    require(actor, Permission.VIEW_MENU)
    _get_item(conn, menu_item_id)
    return [_recipe_dict(row) for row in _recipe_rows(conn, menu_item_id)]


def _clean_recipe_lines(lines: object) -> dict[int, int]:
    """Validate ``[{"ingredient_id": 1, "qty_milli": 250}, ...]`` into {id: qty}."""
    cleaned: dict[int, int] = {}
    try:
        pairs = [(line["ingredient_id"], line["qty_milli"]) for line in lines]
    except (TypeError, KeyError, IndexError):
        raise ValidationError("The recipe lines are not valid.") from None
    for ingredient_id, qty_milli in pairs:
        if type(ingredient_id) is not int:
            raise ValidationError("The recipe lines are not valid.")
        if ingredient_id in cleaned:
            raise ValidationError("Each ingredient can appear only once in a recipe.")
        cleaned[ingredient_id] = _check_int(
            qty_milli, "recipe quantity", 1, MAX_RECIPE_QTY_MILLI
        )
    return cleaned


def _describe_recipe(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "no recipe"
    return ", ".join(
        f'{row["ingredient_name"]} {format_quantity(row["qty_milli"])} {row["unit"]}'
        for row in rows
    )


def set_recipe(
    conn: sqlite3.Connection, actor: Actor, menu_item_id: int, lines: list[dict]
) -> list[dict]:
    """Replace the item's whole recipe. An empty list removes it."""
    require(actor, Permission.EDIT_MENU)
    cleaned = _clean_recipe_lines(lines)
    with transaction(conn):
        item = _get_item(conn, menu_item_id)
        for ingredient_id in cleaned:
            ingredient = conn.execute(
                "SELECT name, active FROM ingredients WHERE id = ?", (ingredient_id,)
            ).fetchone()
            if ingredient is None:
                raise NotFoundError("One of those ingredients no longer exists.")
            if not ingredient["active"]:
                raise ConflictError(
                    f'"{ingredient["name"]}" is inactive and can\'t be used in a recipe.'
                )
        old_rows = _recipe_rows(conn, menu_item_id)
        conn.execute("DELETE FROM recipe_items WHERE menu_item_id = ?", (menu_item_id,))
        conn.executemany(
            "INSERT INTO recipe_items (menu_item_id, ingredient_id, qty_milli) VALUES (?, ?, ?)",
            [(menu_item_id, ingredient_id, qty) for ingredient_id, qty in cleaned.items()],
        )
        new_rows = _recipe_rows(conn, menu_item_id)
        if _describe_recipe(old_rows) != _describe_recipe(new_rows):
            audit.record(
                conn,
                actor.id,
                "menu.recipe_changed",
                f'{item["name"]} (item {menu_item_id}): {_describe_recipe(old_rows)} '
                f"-> {_describe_recipe(new_rows)}",
            )
        return [_recipe_dict(row) for row in new_rows]
