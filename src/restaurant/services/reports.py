"""Sales and stock reports and CSV export (FR-R1 to FR-R3).

Sales fall in a date range by when they were made, and a voided sale is counted
as a void in the period of the sale, not of the void. Report values are as sold.
"""

from __future__ import annotations

import csv
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, tzinfo
from decimal import Decimal
from pathlib import Path

from restaurant.core import Actor, ConflictError, Permission, require
from restaurant.services.sales import local_dates_to_utc

DateInput = date | str

_LIVE = "s.voided_at IS NULL"
_VOIDED = "s.voided_at IS NOT NULL"
_IN_RANGE = "s.created_at >= ? AND s.created_at < ?"

# Shared totals for reports grouped over whole sales.
_SALE_TOTALS = (
    f"COALESCE(SUM(CASE WHEN {_LIVE} THEN 1 END), 0) AS sale_count, "
    f"COALESCE(SUM(CASE WHEN {_LIVE} THEN s.subtotal_cents END), 0) AS subtotal_cents, "
    f"COALESCE(SUM(CASE WHEN {_LIVE} THEN s.tax_cents END), 0) AS tax_cents, "
    f"COALESCE(SUM(CASE WHEN {_LIVE} THEN s.total_cents END), 0) AS total_cents, "
    f"COALESCE(SUM(CASE WHEN {_VOIDED} THEN 1 END), 0) AS void_count, "
    f"COALESCE(SUM(CASE WHEN {_VOIDED} THEN s.total_cents END), 0) AS voided_total_cents"
)

# Shared totals for reports grouped over sale lines. These exclude tax.
_LINE_TOTALS = (
    f"COALESCE(SUM(CASE WHEN {_LIVE} THEN si.quantity END), 0) AS quantity, "
    f"COALESCE(SUM(CASE WHEN {_LIVE} THEN si.line_total_cents END), 0) AS total_cents, "
    f"COALESCE(SUM(CASE WHEN {_VOIDED} THEN si.quantity END), 0) AS voided_quantity, "
    f"COALESCE(SUM(CASE WHEN {_VOIDED} THEN si.line_total_cents END), 0) AS voided_total_cents"
)
_FROM_LINES = "FROM sale_items si JOIN sales s ON s.id = si.sale_id"

_INGREDIENT_COLUMNS = "id, name, unit, unit_cost_cents, on_hand_milli, low_milli, active"

_FORMULA_PREFIXES = ("=", "+", "-", "@")
_SCALE_BY_SUFFIX = (("_cents", 2), ("_milli", 3))


def sales_summary(
    conn: sqlite3.Connection,
    actor: Actor,
    start_date: DateInput,
    end_date: DateInput,
    tz: tzinfo | None = None,
) -> dict:
    """Totals for the range, with voided sales counted separately."""
    require(actor, Permission.VIEW_SALES_REPORTS)
    row = conn.execute(
        f"SELECT {_SALE_TOTALS} FROM sales s WHERE {_IN_RANGE}",
        local_dates_to_utc(start_date, end_date, tz),
    ).fetchone()
    return dict(row)


def sales_by_item(
    conn: sqlite3.Connection,
    actor: Actor,
    start_date: DateInput,
    end_date: DateInput,
    tz: tzinfo | None = None,
) -> list[dict]:
    """One row per item and category as sold. ``total_cents`` excludes tax."""
    require(actor, Permission.VIEW_SALES_REPORTS)
    return _rows(
        conn,
        f"SELECT si.name_as_sold AS name, si.category_as_sold AS category, {_LINE_TOTALS} "
        f"{_FROM_LINES} WHERE {_IN_RANGE} GROUP BY si.name_as_sold, si.category_as_sold "
        "ORDER BY total_cents DESC, name, category",
        local_dates_to_utc(start_date, end_date, tz),
    )


def sales_by_category(
    conn: sqlite3.Connection,
    actor: Actor,
    start_date: DateInput,
    end_date: DateInput,
    tz: tzinfo | None = None,
) -> list[dict]:
    """One row per category as sold. ``total_cents`` excludes tax."""
    require(actor, Permission.VIEW_SALES_REPORTS)
    return _rows(
        conn,
        f"SELECT si.category_as_sold AS category, {_LINE_TOTALS} "
        f"{_FROM_LINES} WHERE {_IN_RANGE} GROUP BY si.category_as_sold "
        "ORDER BY total_cents DESC, category",
        local_dates_to_utc(start_date, end_date, tz),
    )


def sales_by_payment_method(
    conn: sqlite3.Connection,
    actor: Actor,
    start_date: DateInput,
    end_date: DateInput,
    tz: tzinfo | None = None,
) -> list[dict]:
    """One row per payment method. ``total_cents`` includes tax."""
    require(actor, Permission.VIEW_SALES_REPORTS)
    return _rows(
        conn,
        f"SELECT s.payment_method, {_SALE_TOTALS} FROM sales s WHERE {_IN_RANGE} "
        "GROUP BY s.payment_method ORDER BY total_cents DESC, s.payment_method",
        local_dates_to_utc(start_date, end_date, tz),
    )


def sales_by_cashier(
    conn: sqlite3.Connection,
    actor: Actor,
    start_date: DateInput,
    end_date: DateInput,
    tz: tzinfo | None = None,
) -> list[dict]:
    """One row per cashier. ``total_cents`` includes tax."""
    require(actor, Permission.VIEW_SALES_REPORTS)
    return _rows(
        conn,
        f"SELECT s.staff_id, c.username, c.full_name, {_SALE_TOTALS} "
        f"FROM sales s JOIN staff c ON c.id = s.staff_id WHERE {_IN_RANGE} "
        "GROUP BY s.staff_id ORDER BY total_cents DESC, c.full_name",
        local_dates_to_utc(start_date, end_date, tz),
    )


def stock_levels(conn: sqlite3.Connection, actor: Actor) -> list[dict]:
    """Active ingredients, plus inactive ones still holding stock, by name."""
    require(actor, Permission.VIEW_STOCK_REPORTS)
    return [_with_flags(row) for row in _reportable_ingredients(conn)]


def stock_value(conn: sqlite3.Connection, actor: Actor) -> dict:
    """Stock value at unit cost: ``{"items": [...], "total_value_cents": int}``.

    The total is the sum of the rounded rows, so it matches what staff see.
    """
    require(actor, Permission.VIEW_STOCK_REPORTS)
    items = [
        {**dict(row), "value_cents": _value_cents(row["on_hand_milli"], row["unit_cost_cents"])}
        for row in _reportable_ingredients(conn)
    ]
    return {"items": items, "total_value_cents": sum(item["value_cents"] for item in items)}


def low_stock(conn: sqlite3.Connection, actor: Actor) -> list[dict]:
    """Active ingredients at or below threshold, with negative levels flagged."""
    require(actor, Permission.VIEW_STOCK_REPORTS)
    rows = conn.execute(
        f"SELECT {_INGREDIENT_COLUMNS} FROM ingredients "
        "WHERE active = 1 AND on_hand_milli <= low_milli ORDER BY on_hand_milli, name"
    ).fetchall()
    return [_with_flags(row) for row in rows]


def movement_history(
    conn: sqlite3.Connection,
    actor: Actor,
    start_date: DateInput,
    end_date: DateInput,
    tz: tzinfo | None = None,
    ingredient_id: int | None = None,
) -> list[dict]:
    """Stock movements in the range, newest first, optionally for one ingredient."""
    require(actor, Permission.VIEW_STOCK_REPORTS)
    start, end = local_dates_to_utc(start_date, end_date, tz)
    params: list[object] = [start, end]
    only_ingredient = ""
    if ingredient_id is not None:
        only_ingredient = "AND m.ingredient_id = ? "
        params.append(ingredient_id)
    return _rows(
        conn,
        "SELECT m.id, m.ingredient_id, i.name AS ingredient, i.unit, m.movement_type, "
        "m.delta_milli, m.staff_id, s.full_name AS staff_name, m.sale_id, m.note, m.created_at "
        "FROM stock_movements m JOIN ingredients i ON i.id = m.ingredient_id "
        "JOIN staff s ON s.id = m.staff_id "
        f"WHERE m.created_at >= ? AND m.created_at < ? {only_ingredient}"
        "ORDER BY m.created_at DESC, m.id DESC",
        params,
    )


def write_csv(
    path: str | Path,
    columns: Sequence[str | tuple[str, str]],
    rows: Iterable[Mapping[str, object]],
) -> None:
    """Export report rows to a CSV file that Excel opens correctly (FR-R3).

    Each column is a row key, or ``(key, header)`` for a friendlier heading. Values
    under keys ending ``_cents`` or ``_milli`` come out as plain decimals (``12.50``,
    ``-1.500``). Text cells that a spreadsheet would run as a formula get a leading
    single quote; numbers are never touched, so a negative stays a number.
    """
    specs = [(column, column) if isinstance(column, str) else column for column in columns]
    table = [[_text_cell(header) for _, header in specs]]
    table += [[_cell(key, row[key]) for key, _ in specs] for row in rows]
    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as file:
            csv.writer(file).writerows(table)
    except OSError as error:
        raise ConflictError(
            f"Could not write {path}. If it is open in another program, close it and "
            f"try again. ({error.strerror})"
        ) from error


def _rows(conn: sqlite3.Connection, sql: str, params: Sequence[object]) -> list[dict]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _reportable_ingredients(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        f"SELECT {_INGREDIENT_COLUMNS} FROM ingredients "
        "WHERE active = 1 OR on_hand_milli <> 0 ORDER BY name"
    ).fetchall()


def _with_flags(row: sqlite3.Row) -> dict:
    return {
        **dict(row),
        "is_low": bool(row["active"]) and row["on_hand_milli"] <= row["low_milli"],
        "is_negative": row["on_hand_milli"] < 0,
    }


def _value_cents(on_hand_milli: int, unit_cost_cents: int) -> int:
    """Value in cents, half up. Negative on-hand is worth nothing, not a negative."""
    return (max(on_hand_milli, 0) * unit_cost_cents + 500) // 1000


def _cell(key: str, value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        raise TypeError(f"Column {key} holds a float; money and quantities must be exact.")
    if isinstance(value, str):
        return _text_cell(value)
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        for suffix, digits in _SCALE_BY_SUFFIX:
            if key.endswith(suffix):
                return format(Decimal(value).scaleb(-digits), "f")
    return format(value, "f") if isinstance(value, Decimal) else str(value)


def _text_cell(text: str) -> str:
    # Leading whitespace is skipped first: spreadsheets can trim it on import.
    return f"'{text}" if text.lstrip().startswith(_FORMULA_PREFIXES) else text
