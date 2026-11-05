"""The cart being built on the sales screen, and recorded sales (FR-P1 to FR-P6).

The cart lives here rather than in QML because its totals are money arithmetic. It
is a preview: record_sale reads prices and tax from the database again when saving.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Signal

from restaurant.bridge.base import (
    PAYMENT_LABELS,
    BridgeObject,
    api_slot,
    optional_date,
    whole_number,
)
from restaurant.core import Permission, ValidationError, calculate_tax_cents, require
from restaurant.services import menu, sales, settings

if TYPE_CHECKING:
    from restaurant.bridge.api import Api


class SalesBridge(BridgeObject):
    cartChanged = Signal()

    def __init__(self, api: Api) -> None:
        super().__init__(api)
        self._cart: dict[int, int] = {}  # menu item id to quantity, in the order added

    def discard_cart(self) -> None:
        if self._cart:
            self._cart.clear()
            self.cartChanged.emit()

    @api_slot()
    def cart(self) -> dict:
        return self._cart_view()

    @api_slot(int)
    def cartAdd(self, item_id: int) -> dict:
        """Add one of an item."""
        actor = self.actor
        require(actor, Permission.RECORD_SALES)
        item = menu.get_item(self.conn, actor, item_id)
        if not (item["active"] and item["category_active"]):
            raise ValidationError(f"{item['name']} is not available for sale.")
        return self._set_quantity(item_id, self._cart.get(item_id, 0) + 1)

    @api_slot(int, "QVariant")
    def cartSetQuantity(self, item_id: int, quantity: object) -> dict:
        """Set a line's quantity, which may be typed text. Zero removes the line."""
        require(self.actor, Permission.RECORD_SALES)
        if item_id not in self._cart:
            raise ValidationError("That item is not in the sale.")
        return self._set_quantity(item_id, whole_number(quantity, "quantity"))

    @api_slot(int)
    def cartRemove(self, item_id: int) -> dict:
        require(self.actor, Permission.RECORD_SALES)
        if self._cart.pop(item_id, None) is not None:
            self.cartChanged.emit()
        return self._cart_view()

    @api_slot()
    def cartClear(self) -> dict:
        require(self.actor, Permission.RECORD_SALES)
        self.discard_cart()
        return self._cart_view()

    @api_slot()
    def paymentMethods(self) -> list:
        return [{"value": value, "label": PAYMENT_LABELS[value]} for value in sales.PAYMENT_METHODS]

    @api_slot(str)
    def checkout(self, payment_method: str) -> dict:
        """Save the cart as a sale and empty it. On failure the cart is left as it was."""
        lines = [
            {"menu_item_id": item_id, "quantity": quantity}
            for item_id, quantity in self._cart.items()
        ]
        sale = sales.record_sale(self.conn, self.actor, lines, payment_method)
        self.discard_cart()
        self.api.salesChanged.emit()
        self.api.stockChanged.emit()
        return sale

    @api_slot(int, str)
    def voidSale(self, sale_id: int, reason: str) -> dict:
        sale = sales.void_sale(self.conn, self.actor, sale_id, reason)
        self.api.salesChanged.emit()
        self.api.stockChanged.emit()
        return sale

    @api_slot(int)
    def getSale(self, sale_id: int) -> dict:
        return sales.get_sale(self.conn, self.actor, sale_id)

    @api_slot(str, str, int, int)
    def listSales(self, start_date: str, end_date: str, limit: int, offset: int) -> list:
        """Dates are YYYY-MM-DD, both or neither ("")."""
        return sales.list_sales(
            self.conn,
            self.actor,
            optional_date(start_date),
            optional_date(end_date),
            limit=limit,
            offset=offset,
        )

    def _set_quantity(self, item_id: int, quantity: int) -> dict:
        if not 0 <= quantity <= sales.MAX_LINE_QUANTITY:
            raise ValidationError(f"Quantities must be from 0 to {sales.MAX_LINE_QUANTITY:,}.")
        if quantity == 0:
            self._cart.pop(item_id, None)
        else:
            self._cart[item_id] = quantity
        self.cartChanged.emit()
        return self._cart_view()

    def _cart_view(self) -> dict:
        actor = self.actor
        require(actor, Permission.RECORD_SALES)
        lines = []
        for item_id, quantity in self._cart.items():
            item = menu.get_item(self.conn, actor, item_id)
            lines.append(
                {
                    "menu_item_id": item_id,
                    "name": item["name"],
                    "category": item["category_name"],
                    "quantity": quantity,
                    "unit_price_cents": item["price_cents"],
                    "line_total_cents": item["price_cents"] * quantity,
                    # False if a manager deactivated it meanwhile: checkout will refuse.
                    "available": item["active"] and item["category_active"],
                }
            )
        tax_rate_bp = settings.get_all(self.conn)["tax_rate_bp"]
        subtotal = sum(line["line_total_cents"] for line in lines)
        tax = calculate_tax_cents(subtotal, tax_rate_bp)
        return {
            "lines": lines,
            "item_count": sum(line["quantity"] for line in lines),
            "subtotal_cents": subtotal,
            "tax_rate_bp": tax_rate_bp,
            "tax_cents": tax,
            "total_cents": subtotal + tax,
        }
