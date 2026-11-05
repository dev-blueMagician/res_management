"""Shared types used by every service: errors, the signed-in user, permissions, helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


class AppError(Exception):
    """Base class for expected errors. The message is safe to show to staff."""


class ValidationError(AppError):
    """The input is invalid, such as an empty name or a negative price."""


class NotFoundError(AppError):
    """The referenced record does not exist."""


class ConflictError(AppError):
    """The change conflicts with existing data, such as a duplicate name."""


class AuthenticationError(AppError):
    """Wrong username or password, or a deactivated account."""


class PermissionDeniedError(AppError):
    """The signed-in user's role does not allow this action."""


class Role(str, Enum):
    MANAGER = "manager"
    CASHIER = "cashier"
    STOCK_CLERK = "stock_clerk"


class Permission(Enum):
    RECORD_SALES = "record_sales"
    VOID_SALES = "void_sales"
    VIEW_MENU = "view_menu"
    EDIT_MENU = "edit_menu"
    MANAGE_STOCK = "manage_stock"
    VIEW_SALES_REPORTS = "view_sales_reports"
    VIEW_STOCK_REPORTS = "view_stock_reports"
    MANAGE_STAFF = "manage_staff"
    MANAGE_SETTINGS = "manage_settings"
    VIEW_AUDIT_LOG = "view_audit_log"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.MANAGER: frozenset(Permission),
    Role.CASHIER: frozenset({Permission.RECORD_SALES, Permission.VIEW_MENU}),
    Role.STOCK_CLERK: frozenset(
        {Permission.VIEW_MENU, Permission.MANAGE_STOCK, Permission.VIEW_STOCK_REPORTS}
    ),
}


@dataclass(frozen=True)
class Actor:
    """The signed-in user performing an action. Every service call takes one."""

    id: int
    username: str
    full_name: str
    role: Role

    def can(self, permission: Permission) -> bool:
        return permission in ROLE_PERMISSIONS[self.role]


def require(actor: Actor, permission: Permission) -> None:
    """Raise PermissionDeniedError unless the actor's role has the permission."""
    if not actor.can(permission):
        raise PermissionDeniedError("You don't have permission to do that.")


def utc_now() -> str:
    """Current time as a UTC ISO-8601 string, the format stored in the database."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def calculate_tax_cents(subtotal_cents: int, tax_rate_bp: int) -> int:
    """Tax on a subtotal in whole cents, rounding half up. 825 bp means 8.25%."""
    tax = Decimal(subtotal_cents) * Decimal(tax_rate_bp) / Decimal(10_000)
    return int(tax.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def parse_money(text: str) -> int:
    """Convert user input such as ``"12.50"`` to whole cents (1250)."""
    return _parse_scaled(text, 100, "amount")


def parse_quantity(text: str) -> int:
    """Convert user input such as ``"1.5"`` to thousandths of a unit (1500)."""
    return _parse_scaled(text, 1000, "quantity")


def _parse_scaled(text: str, scale: int, label: str) -> int:
    try:
        value = Decimal(str(text).strip()) * scale
    except ArithmeticError:
        raise ValidationError(f"Enter a valid {label}.") from None
    if not value.is_finite() or value != value.to_integral_value():
        raise ValidationError(f"Enter a valid {label}.")
    return int(value)


def format_money(cents: int, symbol: str = "$") -> str:
    """Format whole cents for display: 1250 becomes ``$12.50``."""
    sign = "-" if cents < 0 else ""
    return f"{sign}{symbol}{abs(cents) // 100:,}.{abs(cents) % 100:02d}"


def format_quantity(milli: int) -> str:
    """Format thousandths of a unit for display: 1500 becomes ``1.5``."""
    sign = "-" if milli < 0 else ""
    whole, fraction = divmod(abs(milli), 1000)
    text = f"{whole:,}.{fraction:03d}".rstrip("0").rstrip(".")
    return f"{sign}{text}"
