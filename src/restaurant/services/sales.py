"""Recording, voiding and looking up sales (FR-P1 to FR-P6)."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone, tzinfo

from restaurant.core import (
    Actor,
    ConflictError,
    NotFoundError,
    Permission,
    PermissionDeniedError,
    ValidationError,
    calculate_tax_cents,
    format_money,
    require,
    utc_now,
)
from restaurant.db.connection import transaction
from restaurant.services import audit

PAYMENT_METHODS = ("cash", "card", "other")
MAX_LINE_QUANTITY = 10_000  # keeps totals far from SQLite's integer limit
MAX_PAGE_SIZE = 500
RECENT_SALES_DAYS = 7  # how far back cashiers can see their own sales
_MAX_ROW_ID = 2**63 - 1

_SALE_SELECT = (
    "SELECT s.*, c.full_name AS cashier_name, v.full_name AS voided_by_name "
    "FROM sales s JOIN staff c ON c.id = s.staff_id "
    "LEFT JOIN staff v ON v.id = s.voided_by "
)


def record_sale(
    conn: sqlite3.Connection, actor: Actor, lines: list[dict], payment_method: str
) -> dict:
    """Save a sale and deduct its stock in one transaction, returning the saved sale.

    ``lines`` is ``[{"menu_item_id": int, "quantity": int}, ...]``. Prices and the
    tax rate always come from the database, and low stock never blocks a sale.
    """
    require(actor, Permission.RECORD_SALES)
    if payment_method not in PAYMENT_METHODS:
        raise ValidationError("Choose a payment method: cash, card or other.")
    quantities = _merge_lines(lines)

    with transaction(conn):
        tax_rate_bp = _tax_rate_bp(conn)
        items = [_sellable_item(conn, item_id) for item_id in quantities]
        subtotal = sum(item["price_cents"] * quantities[item["id"]] for item in items)
        tax = calculate_tax_cents(subtotal, tax_rate_bp)
        now = utc_now()
        sale_id = conn.execute(
            "INSERT INTO sales (staff_id, payment_method, subtotal_cents, tax_rate_bp, "
            "tax_cents, total_cents, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (actor.id, payment_method, subtotal, tax_rate_bp, tax, subtotal + tax, now),
        ).lastrowid

        usage: dict[int, int] = {}
        for item in items:
            quantity = quantities[item["id"]]
            conn.execute(
                "INSERT INTO sale_items (sale_id, menu_item_id, name_as_sold, "
                "category_as_sold, unit_price_cents, quantity, line_total_cents) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    sale_id,
                    item["id"],
                    item["name"],
                    item["category"],
                    item["price_cents"],
                    quantity,
                    item["price_cents"] * quantity,
                ),
            )
            recipe = conn.execute(
                "SELECT ingredient_id, qty_milli FROM recipe_items WHERE menu_item_id = ?",
                (item["id"],),
            ).fetchall()
            for row in recipe:
                usage[row["ingredient_id"]] = (
                    usage.get(row["ingredient_id"], 0) + row["qty_milli"] * quantity
                )

        for ingredient_id in sorted(usage):
            conn.execute(
                "INSERT INTO stock_movements (ingredient_id, movement_type, delta_milli, "
                "staff_id, sale_id, note, created_at) VALUES (?, 'sold', ?, ?, ?, ?, ?)",
                (ingredient_id, -usage[ingredient_id], actor.id, sale_id, f"Sale #{sale_id}", now),
            )
    return _load_sale(conn, sale_id)


def void_sale(conn: sqlite3.Connection, actor: Actor, sale_id: int, reason: str) -> dict:
    """Void a sale and return its stock by mirroring the sale's actual movements.

    The current recipe is deliberately not consulted, since it may have changed.
    """
    require(actor, Permission.VOID_SALES)
    reason = reason.strip() if isinstance(reason, str) else ""
    if not reason:
        raise ValidationError("Enter a reason for voiding the sale.")

    with transaction(conn):
        sale = _sale_row(conn, sale_id)
        if sale["voided_at"] is not None:
            raise ConflictError(f"Sale #{sale_id} has already been voided.")
        now = utc_now()
        conn.execute(
            "UPDATE sales SET voided_at = ?, voided_by = ?, void_reason = ? WHERE id = ?",
            (now, actor.id, reason, sale_id),
        )
        movements = conn.execute(
            "SELECT ingredient_id, delta_milli FROM stock_movements "
            "WHERE sale_id = ? AND movement_type = 'sold' ORDER BY id",
            (sale_id,),
        ).fetchall()
        for movement in movements:
            conn.execute(
                "INSERT INTO stock_movements (ingredient_id, movement_type, delta_milli, "
                "staff_id, sale_id, note, created_at) "
                "VALUES (?, 'void_reversal', ?, ?, ?, ?, ?)",
                (
                    movement["ingredient_id"],
                    -movement["delta_milli"],
                    actor.id,
                    sale_id,
                    f"Void of sale #{sale_id}",
                    now,
                ),
            )
        audit.record(
            conn,
            actor.id,
            "sale_voided",
            f"Sale #{sale_id}, total {format_money(sale['total_cents'], '')}, reason: {reason}",
        )
    return _load_sale(conn, sale_id)


def get_sale(conn: sqlite3.Connection, actor: Actor, sale_id: int) -> dict:
    """One sale with its lines. Cashiers can only open their own recent sales."""
    require(actor, Permission.RECORD_SALES)
    sale = _sale_row(conn, sale_id)
    if not _sees_all_sales(actor) and (
        sale["staff_id"] != actor.id or sale["created_at"] < _recent_cutoff()
    ):
        raise PermissionDeniedError("You can only view your own recent sales.")
    return _load_sale(conn, sale_id)


def list_sales(
    conn: sqlite3.Connection,
    actor: Actor,
    start_date: date | str | None = None,
    end_date: date | str | None = None,
    tz: tzinfo | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Newest sales first, voided ones included, without their lines.

    Managers see everything. Cashiers see their own sales from the last
    RECENT_SALES_DAYS days, even if the date range asks for more. The range is
    optional local calendar dates (see local_dates_to_utc).
    """
    require(actor, Permission.RECORD_SALES)
    if not _is_int(limit) or not 1 <= limit <= MAX_PAGE_SIZE or not _is_int(offset) or offset < 0:
        raise ValidationError("Invalid page of sales requested.")
    if (start_date is None) != (end_date is None):
        raise ValidationError("Choose both a start date and an end date.")

    clauses, params = [], []
    if start_date is not None:
        start, end = local_dates_to_utc(start_date, end_date, tz)
        clauses.append("s.created_at >= ? AND s.created_at < ?")
        params += [start, end]
    if not _sees_all_sales(actor):
        clauses.append("s.staff_id = ? AND s.created_at >= ?")
        params += [actor.id, _recent_cutoff()]
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    rows = conn.execute(
        f"{_SALE_SELECT}{where}ORDER BY s.created_at DESC, s.id DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    return [dict(row) for row in rows]


def local_dates_to_utc(
    start_date: date | str, end_date: date | str, tz: tzinfo | None = None
) -> tuple[str, str]:
    """Convert an inclusive range of local dates to UTC bounds: ``start <= t < end``.

    Each local day runs from its own midnight to the next day's midnight, so a
    daylight-saving day comes out 23 or 25 hours long. ``tz`` defaults to the
    system's local timezone. The bounds compare correctly as text against stored
    timestamps because they share the format of ``core.utc_now()``.
    """
    start, end = _as_date(start_date), _as_date(end_date)
    if start > end:
        raise ValidationError("The start date must not be after the end date.")
    try:
        return _day_start_utc(start, tz), _day_start_utc(end + timedelta(days=1), tz)
    except (OverflowError, OSError, ValueError):
        raise ValidationError("Choose a valid date range.") from None


def _as_date(value: date | str) -> date:
    if isinstance(value, date):  # also takes a datetime, using its calendar date
        return date(value.year, value.month, value.day)
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValidationError("Choose a valid date range.") from None


def _day_start_utc(day: date, tz: tzinfo | None) -> str:
    # With tz None the datetime is naive, which astimezone reads as system local time.
    midnight = datetime(day.year, day.month, day.day, tzinfo=tz)
    return midnight.astimezone(timezone.utc).isoformat(timespec="seconds")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_row_id(value: object) -> bool:
    return _is_int(value) and 0 < value <= _MAX_ROW_ID


def _merge_lines(lines: object) -> dict[int, int]:
    """Validate the lines and merge duplicates into {menu_item_id: quantity}."""
    if not isinstance(lines, (list, tuple)) or not lines:
        raise ValidationError("Add at least one item to the sale.")
    quantities: dict[int, int] = {}
    for line in lines:
        if not isinstance(line, Mapping):
            raise ValidationError("A sale line is invalid.")
        item_id, quantity = line.get("menu_item_id"), line.get("quantity")
        if not _is_row_id(item_id):
            raise NotFoundError("That menu item no longer exists.")
        if not _is_int(quantity) or quantity <= 0:
            raise ValidationError("Quantities must be whole numbers above zero.")
        quantities[item_id] = quantities.get(item_id, 0) + quantity
        if quantities[item_id] > MAX_LINE_QUANTITY:
            raise ValidationError(f"Quantities can't be more than {MAX_LINE_QUANTITY:,}.")
    return quantities


def _tax_rate_bp(conn: sqlite3.Connection) -> int:
    # A bad setting must stop the sale: falling back to zero would undercharge tax.
    row = conn.execute("SELECT value FROM settings WHERE key = 'tax_rate_bp'").fetchone()
    try:
        rate = int(row["value"])
    except (TypeError, ValueError):
        rate = -1
    if rate < 0:
        raise ConflictError("The tax rate setting is invalid. Ask a manager to correct it.")
    return rate


def _sellable_item(conn: sqlite3.Connection, item_id: int) -> sqlite3.Row:
    item = conn.execute(
        "SELECT i.id, i.name, i.price_cents, i.active, c.name AS category, "
        "c.active AS category_active "
        "FROM menu_items i JOIN menu_categories c ON c.id = i.category_id WHERE i.id = ?",
        (item_id,),
    ).fetchone()
    if item is None:
        raise NotFoundError("That menu item no longer exists.")
    if not item["active"] or not item["category_active"]:
        raise ValidationError(f"{item['name']} is not available for sale.")
    return item


def _sale_row(conn: sqlite3.Connection, sale_id: int) -> sqlite3.Row:
    row = None
    if _is_row_id(sale_id):
        row = conn.execute("SELECT * FROM sales WHERE id = ?", (sale_id,)).fetchone()
    if row is None:
        raise NotFoundError(f"Sale #{sale_id} was not found.")
    return row


def _load_sale(conn: sqlite3.Connection, sale_id: int) -> dict:
    sale = dict(conn.execute(f"{_SALE_SELECT}WHERE s.id = ?", (sale_id,)).fetchone())
    items = conn.execute("SELECT * FROM sale_items WHERE sale_id = ? ORDER BY id", (sale_id,))
    sale["items"] = [dict(row) for row in items]
    return sale


def _sees_all_sales(actor: Actor) -> bool:
    return actor.can(Permission.VIEW_SALES_REPORTS)


def _recent_cutoff() -> str:
    cutoff = datetime.fromisoformat(utc_now()) - timedelta(days=RECENT_SALES_DAYS)
    return cutoff.isoformat(timespec="seconds")
