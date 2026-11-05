"""Restaurant settings: name, currency symbol and tax rate."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from restaurant.core import Actor, Permission, ValidationError, require
from restaurant.db.connection import transaction
from restaurant.services import audit

MAX_TAX_RATE_BP = 10_000
MAX_CURRENCY_SYMBOL_LENGTH = 3
MAX_RESTAURANT_NAME_LENGTH = 100


def get_all(conn: sqlite3.Connection, actor: Actor | None = None) -> dict:
    """Every setting, with tax_rate_bp as an int. Any role may read them.

    ``actor`` is optional because no permission is needed, and the name is also
    wanted on the sign-in screen, before anyone has signed in.
    """
    values = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}
    values["tax_rate_bp"] = int(values["tax_rate_bp"])
    return values


def get_text(conn: sqlite3.Connection, key: str) -> str:
    """One setting as stored, or "" if it is missing. For display: bad content never raises."""
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else ""


def check_stored(values: Mapping[str, object]) -> None:
    """Raise ValidationError unless these stored settings are all present and usable."""
    _clean_restaurant_name(values.get("restaurant_name"))
    _clean_currency_symbol(values.get("currency_symbol"))
    rate = values.get("tax_rate_bp")
    if not (isinstance(rate, str) and rate.isascii() and rate.isdigit()):
        raise ValidationError("The stored tax rate is not a whole number.")
    _check_tax_rate(int(rate))


def update(
    conn: sqlite3.Connection,
    actor: Actor,
    *,
    restaurant_name: str | None = None,
    currency_symbol: str | None = None,
    tax_rate_bp: int | None = None,
) -> dict:
    """Change the given settings together, or none if any is invalid. Returns all settings."""
    require(actor, Permission.MANAGE_SETTINGS)
    new_values: dict[str, str | int] = {}
    if restaurant_name is not None:
        new_values["restaurant_name"] = _clean_restaurant_name(restaurant_name)
    if currency_symbol is not None:
        new_values["currency_symbol"] = _clean_currency_symbol(currency_symbol)
    if tax_rate_bp is not None:
        new_values["tax_rate_bp"] = _check_tax_rate(tax_rate_bp)
    with transaction(conn):
        old_values = get_all(conn)
        changes = []
        for key, value in new_values.items():
            if value != old_values[key]:
                conn.execute("UPDATE settings SET value = ? WHERE key = ?", (str(value), key))
                changes.append(f"{key}: {old_values[key]} to {value}")
        if changes:
            audit.record(conn, actor.id, "settings.updated", "; ".join(changes))
        return get_all(conn)


def _clean_restaurant_name(name: str) -> str:
    text = name.strip() if isinstance(name, str) else ""
    if not text:
        raise ValidationError("Restaurant name is required.")
    if len(text) > MAX_RESTAURANT_NAME_LENGTH or not text.isprintable():
        raise ValidationError(
            f"Restaurant name must be at most {MAX_RESTAURANT_NAME_LENGTH} plain characters."
        )
    return text


def _clean_currency_symbol(symbol: str) -> str:
    text = symbol.strip() if isinstance(symbol, str) else None
    if text is None or len(text) > MAX_CURRENCY_SYMBOL_LENGTH or not text.isprintable():
        raise ValidationError(
            f"Currency symbol must be at most {MAX_CURRENCY_SYMBOL_LENGTH} characters."
        )
    return text


def _check_tax_rate(tax_rate_bp: int) -> int:
    # type() rather than isinstance: True is an int, and floats have no place in money.
    if type(tax_rate_bp) is not int or not 0 <= tax_rate_bp <= MAX_TAX_RATE_BP:
        raise ValidationError("Tax rate must be a percentage from 0 to 100.")
    return tax_rate_bp
