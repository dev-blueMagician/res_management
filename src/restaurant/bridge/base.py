"""Plumbing shared by every bridge object: the slot decorator and QML value conversion."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QUrl, Slot

from restaurant.core import (
    Actor,
    AppError,
    AuthenticationError,
    ValidationError,
    format_money,
    format_quantity,
)
from restaurant.services import settings

if TYPE_CHECKING:
    from restaurant.bridge.api import Api

log = logging.getLogger("restaurant.bridge")

LOG_FILE_NAME = "restaurant.log"
GENERIC_ERROR = (
    "Something went wrong and the action was not completed. Please try again, "
    "and tell your manager if it keeps happening."
)
ROLE_LABELS = {"manager": "Manager", "cashier": "Cashier", "stock_clerk": "Stock clerk"}
PAYMENT_LABELS = {"cash": "Cash", "card": "Card", "other": "Other"}
MOVEMENT_LABELS = {
    "received": "Received",
    "adjusted": "Adjusted",
    "wasted": "Wasted",
    "sold": "Sold",
    "void_reversal": "Void reversal",
}
_LABEL_KEYS = {
    "role": ROLE_LABELS,
    "payment_method": PAYMENT_LABELS,
    "movement_type": MOVEMENT_LABELS,
}


def configure_logging(data_dir: Path) -> logging.Handler:
    """Send errors to a log file in the data folder. Returns the handler it added."""
    handler = RotatingFileHandler(
        data_dir / LOG_FILE_NAME, maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger("restaurant").addHandler(handler)
    return handler


def api_slot(*arg_types: Any) -> Callable:
    """Make a method a QML slot returning ``{ok: true, data}`` or ``{ok: false, error}``.

    Expected errors become their staff-facing message. Anything else is logged with
    its traceback and replaced by a generic message, so no exception ever reaches
    QML. Arguments are deliberately not logged: some of them are passwords.
    """

    def decorate(method: Callable) -> Callable:
        @wraps(method)
        def wrapper(self: BridgeObject, *args: Any) -> dict:
            try:
                data = method(self, *args)
                return {"ok": True, "data": to_qml(data, self.currency_symbol())}
            except AppError as error:
                return {"ok": False, "error": str(error)}
            except Exception:
                log.exception("Unexpected error in %s.%s", type(self).__name__, method.__name__)
                return {"ok": False, "error": GENERIC_ERROR}

        return Slot(*arg_types, result="QVariantMap")(wrapper)

    return decorate


class BridgeObject(QObject):
    """Base class giving a bridge object the database and the signed-in user."""

    def __init__(self, api: Api) -> None:
        super().__init__(api)
        self.api = api

    @property
    def conn(self) -> sqlite3.Connection:
        return self.api.conn

    @property
    def actor(self) -> Actor:
        """The signed-in user. Reading it with nobody signed in fails the slot."""
        if self.api.current_actor is None:
            raise AuthenticationError("Please sign in first.")
        return self.api.current_actor

    def currency_symbol(self) -> str:
        """Only this setting: a bad one elsewhere must not fail an action that has already run."""
        return settings.get_text(self.conn, "currency_symbol")


def to_qml(value: Any, symbol: str) -> Any:
    """Make a service result safe for QML and add display strings to every record.

    For a record holding ``price_cents`` this adds ``price_text`` ("$12.50") and
    ``price_input`` ("12.50", for pre-filling an edit form), and likewise for
    ``*_milli``. See docs/BRIDGE_API.md. ``None`` becomes "" because QVariantMap
    otherwise drops the key, and assigning undefined to a QML text property warns.
    """
    if value is None:
        return ""
    if isinstance(value, Mapping):
        record: dict[str, Any] = {}
        for key, item in value.items():
            record[key] = to_qml(item, symbol)
            record.update(_display_strings(key, item, symbol))
        return record
    if isinstance(value, (list, tuple)):
        return [to_qml(item, symbol) for item in value]
    return value


def _display_strings(key: str, value: Any, symbol: str) -> dict[str, Any]:
    if key == "active":
        return {"active": bool(value)}
    if key == "voided_at":
        return {"voided": bool(value), "voided_at_text": local_time(value)}
    if key.endswith("_at"):
        return {f"{key}_text": local_time(value)}
    if key in _LABEL_KEYS:
        return {f"{key}_text": _LABEL_KEYS[key].get(value, value)}
    if not isinstance(value, int) or isinstance(value, bool):
        return {}
    if key.endswith("_cents"):
        stem = key.removesuffix("_cents")
        return {
            f"{stem}_text": format_money(value, symbol),
            f"{stem}_input": format_money(value, "").replace(",", ""),
        }
    if key.endswith("_milli"):
        stem = key.removesuffix("_milli")
        text = format_quantity(value)
        return {
            f"{stem}_text": f"+{text}" if key == "delta_milli" and value > 0 else text,
            f"{stem}_input": text.replace(",", ""),
        }
    if key == "tax_rate_bp":
        rate = format_money(value, "").replace(",", "")
        return {"tax_rate_text": f"{rate}%", "tax_rate_input": rate}
    return {}


def local_time(stamp: str | None) -> str:
    """A stored UTC timestamp as local time for display; empty when there is none."""
    if not stamp:
        return ""
    try:
        return datetime.fromisoformat(stamp).astimezone().strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return stamp


def whole_number(value: Any, label: str) -> int:
    """An int from QML: JS numbers can arrive as floats, and typed text as strings."""
    if isinstance(value, bool):
        raise ValidationError(f"Enter a whole number for the {label}.")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    raise ValidationError(f"Enter a whole number for the {label}.")


def read_fields(fields: Mapping[str, Any], *allowed: str) -> dict[str, Any]:
    """The given fields of an edit, dropping nulls. An unknown name is a screen bug."""
    unknown = set(fields) - set(allowed)
    if unknown:
        raise ValueError(f"Unknown fields: {sorted(unknown)}")
    return {key: value for key, value in fields.items() if value is not None}


def optional_date(text: str) -> str | None:
    """A date argument where QML passes "" for no date."""
    return text.strip() or None


def local_path(text: str) -> str:
    """A filesystem path from a typed path or a FileDialog ``file:`` URL."""
    path = QUrl(text).toLocalFile() if text.startswith("file:") else text.strip()
    if not path:
        raise ValidationError("Choose a file.")
    return path


def check_confirmation(password: str, confirmation: str) -> None:
    if password != confirmation:
        raise ValidationError("The two passwords don't match.")
