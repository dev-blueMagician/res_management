"""Ingredients and stock movements (FR-I1 to FR-I5).

Stock changes only ever insert movements: the database trigger maintains
``ingredients.on_hand_milli`` from them, so nothing here updates it directly.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, time, timedelta, timezone

from restaurant.core import (
    Actor,
    ConflictError,
    NotFoundError,
    Permission,
    PermissionDeniedError,
    ValidationError,
    format_money,
    format_quantity,
    require,
    utc_now,
)
from restaurant.db.connection import transaction
from restaurant.services import audit

MAX_UNIT_COST_CENTS = 99_999_999
MAX_QTY_MILLI = 1_000_000_000_000
MAX_PAGE_SIZE = 1000

_INGREDIENT_COLUMNS = "id, name, unit, unit_cost_cents, on_hand_milli, low_milli, active"


def _require_read(actor: Actor) -> None:
    if not (actor.can(Permission.MANAGE_STOCK) or actor.can(Permission.VIEW_STOCK_REPORTS)):
        raise PermissionDeniedError("You don't have permission to do that.")


def _clean_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"Enter a {label}.")
    return value.strip()


def _check_int(value: object, label: str, minimum: int, maximum: int) -> int:
    # bool is an int subclass and floats must never reach money or quantity columns.
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValidationError(f"Enter a valid {label}.")
    return value


def _clean_note(note: object) -> str:
    if note is None:
        return ""
    if not isinstance(note, str):
        raise ValidationError("Enter a valid note.")
    return note.strip()


def _ingredient_dict(row: sqlite3.Row) -> dict:
    return {**dict(row), "active": bool(row["active"])}


def _get_ingredient(conn: sqlite3.Connection, ingredient_id: int) -> sqlite3.Row:
    row = conn.execute(
        f"SELECT {_INGREDIENT_COLUMNS} FROM ingredients WHERE id = ?", (ingredient_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError("That ingredient no longer exists.")
    return row


def _get_active_ingredient(conn: sqlite3.Connection, ingredient_id: int) -> sqlite3.Row:
    row = _get_ingredient(conn, ingredient_id)
    if not row["active"]:
        raise ConflictError(f'"{row["name"]}" is inactive. Reactivate it to change its stock.')
    return row


def _reraise_duplicate(exc: sqlite3.IntegrityError, name: str) -> None:
    """Turn a duplicate-name failure into a ConflictError; anything else is a bug."""
    if "UNIQUE" not in str(exc):
        raise exc
    raise ConflictError(f'An ingredient named "{name}" already exists.') from None


def list_ingredients(
    conn: sqlite3.Connection, actor: Actor, include_inactive: bool = True
) -> list[dict]:
    _require_read(actor)
    where = "" if include_inactive else "WHERE active = 1 "
    rows = conn.execute(
        f"SELECT {_INGREDIENT_COLUMNS} FROM ingredients {where}ORDER BY name"
    ).fetchall()
    return [_ingredient_dict(row) for row in rows]


def get_ingredient(conn: sqlite3.Connection, actor: Actor, ingredient_id: int) -> dict:
    _require_read(actor)
    return _ingredient_dict(_get_ingredient(conn, ingredient_id))


def create_ingredient(
    conn: sqlite3.Connection,
    actor: Actor,
    name: str,
    unit: str,
    unit_cost_cents: int = 0,
    low_milli: int = 0,
) -> dict:
    """New ingredients start with no stock; receive_stock adds the first delivery."""
    require(actor, Permission.MANAGE_STOCK)
    name = _clean_text(name, "ingredient name")
    unit = _clean_text(unit, "unit")
    _check_int(unit_cost_cents, "unit cost", 0, MAX_UNIT_COST_CENTS)
    _check_int(low_milli, "low-stock threshold", 0, MAX_QTY_MILLI)
    try:
        with transaction(conn):
            ingredient_id = conn.execute(
                "INSERT INTO ingredients (name, unit, unit_cost_cents, low_milli) "
                "VALUES (?, ?, ?, ?)",
                (name, unit, unit_cost_cents, low_milli),
            ).lastrowid
            audit.record(
                conn,
                actor.id,
                "inventory.ingredient_created",
                f"{name} (ingredient {ingredient_id}): unit {unit}, "
                f"cost {format_money(unit_cost_cents, symbol='')}, "
                f"low-stock {format_quantity(low_milli)}",
            )
            return _ingredient_dict(_get_ingredient(conn, ingredient_id))
    except sqlite3.IntegrityError as exc:
        _reraise_duplicate(exc, name)


def update_ingredient(
    conn: sqlite3.Connection,
    actor: Actor,
    ingredient_id: int,
    name: str | None = None,
    unit: str | None = None,
    unit_cost_cents: int | None = None,
    low_milli: int | None = None,
) -> dict:
    """Change the given fields; a field left as None keeps its current value."""
    require(actor, Permission.MANAGE_STOCK)
    if name is not None:
        name = _clean_text(name, "ingredient name")
    if unit is not None:
        unit = _clean_text(unit, "unit")
    if unit_cost_cents is not None:
        _check_int(unit_cost_cents, "unit cost", 0, MAX_UNIT_COST_CENTS)
    if low_milli is not None:
        _check_int(low_milli, "low-stock threshold", 0, MAX_QTY_MILLI)
    try:
        with transaction(conn):
            old = _get_ingredient(conn, ingredient_id)
            new_name = old["name"] if name is None else name
            new_unit = old["unit"] if unit is None else unit
            new_cost = old["unit_cost_cents"] if unit_cost_cents is None else unit_cost_cents
            new_low = old["low_milli"] if low_milli is None else low_milli
            conn.execute(
                "UPDATE ingredients SET name = ?, unit = ?, unit_cost_cents = ?, low_milli = ? "
                "WHERE id = ?",
                (new_name, new_unit, new_cost, new_low, ingredient_id),
            )
            changes = []
            if new_name != old["name"]:
                changes.append(f'renamed "{old["name"]}" to "{new_name}"')
            if new_unit != old["unit"]:
                changes.append(f'unit {old["unit"]} -> {new_unit}')
            if new_cost != old["unit_cost_cents"]:
                changes.append(
                    f'cost {format_money(old["unit_cost_cents"], symbol="")} '
                    f'-> {format_money(new_cost, symbol="")}'
                )
            if new_low != old["low_milli"]:
                changes.append(
                    f'low-stock {format_quantity(old["low_milli"])} '
                    f"-> {format_quantity(new_low)}"
                )
            if changes:
                audit.record(
                    conn,
                    actor.id,
                    "inventory.ingredient_updated",
                    f"Ingredient {ingredient_id}: {', '.join(changes)}",
                )
            return _ingredient_dict(_get_ingredient(conn, ingredient_id))
    except sqlite3.IntegrityError as exc:
        _reraise_duplicate(exc, new_name)


def set_ingredient_active(
    conn: sqlite3.Connection, actor: Actor, ingredient_id: int, active: bool
) -> dict:
    """Refuses to deactivate an ingredient that an active item's recipe still uses."""
    require(actor, Permission.MANAGE_STOCK)
    with transaction(conn):
        old = _get_ingredient(conn, ingredient_id)
        if bool(old["active"]) != bool(active):
            if not active:
                _refuse_if_used(conn, ingredient_id, old["name"])
            conn.execute(
                "UPDATE ingredients SET active = ? WHERE id = ?", (int(active), ingredient_id)
            )
            action = "inventory.ingredient_activated" if active else (
                "inventory.ingredient_deactivated"
            )
            audit.record(conn, actor.id, action, f'{old["name"]} (ingredient {ingredient_id})')
        return _ingredient_dict(_get_ingredient(conn, ingredient_id))


def _refuse_if_used(conn: sqlite3.Connection, ingredient_id: int, name: str) -> None:
    items = [
        row["name"]
        for row in conn.execute(
            "SELECT i.name FROM recipe_items r JOIN menu_items i ON i.id = r.menu_item_id "
            "WHERE r.ingredient_id = ? AND i.active = 1 ORDER BY i.name",
            (ingredient_id,),
        )
    ]
    if items:
        raise ConflictError(
            f'"{name}" can\'t be deactivated because these active menu items use it: '
            f"{', '.join(items)}."
        )


def _insert_movement(
    conn: sqlite3.Connection,
    actor: Actor,
    ingredient_id: int,
    movement_type: str,
    delta_milli: int,
    note: str,
) -> dict:
    movement_id = conn.execute(
        "INSERT INTO stock_movements "
        "(ingredient_id, movement_type, delta_milli, staff_id, note, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (ingredient_id, movement_type, delta_milli, actor.id, note, utc_now()),
    ).lastrowid
    return dict(
        conn.execute("SELECT * FROM stock_movements WHERE id = ?", (movement_id,)).fetchone()
    )


def _signed(milli: int) -> str:
    return f"{'+' if milli > 0 else ''}{format_quantity(milli)}"


def _with_note(text: str, note: str) -> str:
    return f"{text}; note: {note}" if note else text


def receive_stock(
    conn: sqlite3.Connection, actor: Actor, ingredient_id: int, qty_milli: int, note: str = ""
) -> dict:
    """Record a delivery. Returns the new movement."""
    require(actor, Permission.MANAGE_STOCK)
    _check_int(qty_milli, "quantity", 1, MAX_QTY_MILLI)
    note = _clean_note(note)
    with transaction(conn):
        _get_active_ingredient(conn, ingredient_id)
        return _insert_movement(conn, actor, ingredient_id, "received", qty_milli, note)


def record_waste(
    conn: sqlite3.Connection, actor: Actor, ingredient_id: int, qty_milli: int, note: str = ""
) -> dict:
    """Record wasted stock, given as a positive quantity. Returns the new movement."""
    require(actor, Permission.MANAGE_STOCK)
    _check_int(qty_milli, "quantity", 1, MAX_QTY_MILLI)
    note = _clean_note(note)
    with transaction(conn):
        ingredient = _get_active_ingredient(conn, ingredient_id)
        movement = _insert_movement(conn, actor, ingredient_id, "wasted", -qty_milli, note)
        audit.record(
            conn,
            actor.id,
            "stock.wasted",
            _with_note(
                f'{ingredient["name"]} (ingredient {ingredient_id}): wasted '
                f'{format_quantity(qty_milli)} {ingredient["unit"]}',
                note,
            ),
        )
        return movement


def adjust_stock(
    conn: sqlite3.Connection, actor: Actor, ingredient_id: int, delta_milli: int, note: str = ""
) -> dict:
    """Apply a signed correction to on-hand stock. Returns the new movement."""
    require(actor, Permission.MANAGE_STOCK)
    _check_int(delta_milli, "quantity", -MAX_QTY_MILLI, MAX_QTY_MILLI)
    if delta_milli == 0:
        raise ValidationError("Enter a quantity other than zero.")
    note = _clean_note(note)
    with transaction(conn):
        ingredient = _get_active_ingredient(conn, ingredient_id)
        movement = _insert_movement(conn, actor, ingredient_id, "adjusted", delta_milli, note)
        audit.record(
            conn,
            actor.id,
            "stock.adjusted",
            _with_note(
                f'{ingredient["name"]} (ingredient {ingredient_id}): '
                f'{_signed(delta_milli)} {ingredient["unit"]}',
                note,
            ),
        )
        return movement


def record_count(
    conn: sqlite3.Connection,
    actor: Actor,
    ingredient_id: int,
    counted_milli: int,
    note: str = "",
) -> dict | None:
    """Set on-hand to a physical count by adjusting by the difference (FR-I3).

    Returns the adjustment movement, or None when the count matched on-hand, in
    which case no movement is written and only the audit log records the count.
    """
    require(actor, Permission.MANAGE_STOCK)
    _check_int(counted_milli, "counted quantity", 0, MAX_QTY_MILLI)
    note = _clean_note(note)
    with transaction(conn):
        ingredient = _get_active_ingredient(conn, ingredient_id)
        on_hand = ingredient["on_hand_milli"]
        difference = counted_milli - on_hand
        movement = None
        if difference != 0:
            movement = _insert_movement(
                conn,
                actor,
                ingredient_id,
                "adjusted",
                difference,
                _with_note(f"Stock count: counted {format_quantity(counted_milli)}", note),
            )
        audit.record(
            conn,
            actor.id,
            "stock.counted",
            _with_note(
                f'{ingredient["name"]} (ingredient {ingredient_id}): counted '
                f"{format_quantity(counted_milli)}, was {format_quantity(on_hand)}, "
                f'difference {_signed(difference)} {ingredient["unit"]}',
                note,
            ),
        )
        return movement


def list_low_stock(conn: sqlite3.Connection, actor: Actor) -> list[dict]:
    """Active ingredients at or below threshold, those below zero first (FR-I4, FR-I5)."""
    _require_read(actor)
    rows = conn.execute(
        f"SELECT {_INGREDIENT_COLUMNS}, on_hand_milli < 0 AS is_negative FROM ingredients "
        "WHERE active = 1 AND on_hand_milli <= low_milli "
        "ORDER BY is_negative DESC, name"
    ).fetchall()
    return [{**_ingredient_dict(row), "is_negative": bool(row["is_negative"])} for row in rows]


def low_stock_count(conn: sqlite3.Connection) -> int:
    """Number shown on the home screen. Takes no actor because every role may see it."""
    return conn.execute(
        "SELECT COUNT(*) FROM ingredients WHERE active = 1 AND on_hand_milli <= low_milli"
    ).fetchone()[0]


def _local_day_start_utc(value: date | str, days_later: int = 0) -> str:
    """UTC timestamp of the start of a restaurant-local calendar day."""
    try:
        day = value if isinstance(value, date) else date.fromisoformat(value)
        midnight = datetime.combine(day + timedelta(days=days_later), time.min)
        # Windows can't convert dates before 1970, which raises OSError.
        return midnight.astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError, OverflowError, OSError):
        raise ValidationError("Enter a valid date as YYYY-MM-DD.") from None


def list_movements(
    conn: sqlite3.Connection,
    actor: Actor,
    ingredient_id: int | None = None,
    date_from: date | str | None = None,
    date_to: date | str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    """Movement history, newest first.

    ``date_from`` and ``date_to`` are local calendar days (a date or YYYY-MM-DD),
    both inclusive.
    """
    _require_read(actor)
    _check_int(limit, "page size", 1, MAX_PAGE_SIZE)
    _check_int(offset, "page offset", 0, 2**62)
    conditions, params = [], []
    if ingredient_id is not None:
        conditions.append("m.ingredient_id = ?")
        params.append(ingredient_id)
    start = None if date_from is None else _local_day_start_utc(date_from)
    end = None if date_to is None else _local_day_start_utc(date_to, days_later=1)
    if start is not None and end is not None and start >= end:
        raise ValidationError("The start date must not be after the end date.")
    if start is not None:
        conditions.append("m.created_at >= ?")
        params.append(start)
    if end is not None:
        conditions.append("m.created_at < ?")
        params.append(end)
    where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
    rows = conn.execute(
        "SELECT m.id, m.ingredient_id, g.name AS ingredient_name, g.unit, m.movement_type, "
        "m.delta_milli, m.staff_id, s.username, s.full_name, m.sale_id, m.note, m.created_at "
        "FROM stock_movements m "
        "JOIN ingredients g ON g.id = m.ingredient_id "
        "JOIN staff s ON s.id = m.staff_id "
        f"{where}ORDER BY m.created_at DESC, m.id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]
