"""Bridge objects for staff, settings, backup, menu, inventory and the audit log."""

from __future__ import annotations

import sqlite3

from PySide6.QtCore import Property, QUrl, Signal

from restaurant.bridge.base import (
    ROLE_LABELS,
    BridgeObject,
    api_slot,
    check_confirmation,
    local_path,
    optional_date,
    read_fields,
    whole_number,
)
from restaurant.core import Permission, ValidationError, parse_money, parse_quantity, require
from restaurant.db.connection import connect
from restaurant.services import audit, backup, inventory, menu, settings, staff


def _optional(fields: dict, key: str, convert) -> object:
    return convert(fields[key]) if key in fields else None


class StaffBridge(BridgeObject):
    @api_slot()
    def roles(self) -> list:
        return [{"value": value, "label": label} for value, label in ROLE_LABELS.items()]

    @api_slot()
    def listStaff(self) -> list:
        return staff.list_staff(self.conn, self.actor)

    @api_slot(str, str, str, str, str)
    def createStaff(
        self, username: str, full_name: str, role: str, password: str, confirm_password: str
    ) -> dict:
        actor = self.actor
        check_confirmation(password, confirm_password)
        record = staff.create_staff(self.conn, actor, username, full_name, role, password)
        self.api.staffChanged.emit()
        return record

    @api_slot(int, "QVariantMap")
    def updateStaff(self, staff_id: int, fields: dict) -> dict:
        given = read_fields(fields, "full_name", "role")
        return self._changed(
            staff.update_staff(
                self.conn, self.actor, staff_id, given.get("full_name"), given.get("role")
            )
        )

    @api_slot(int)
    def deactivateStaff(self, staff_id: int) -> dict:
        return self._changed(staff.deactivate_staff(self.conn, self.actor, staff_id))

    @api_slot(int)
    def reactivateStaff(self, staff_id: int) -> dict:
        return self._changed(staff.reactivate_staff(self.conn, self.actor, staff_id))

    @api_slot(int, str, str)
    def resetPassword(self, staff_id: int, new_password: str, confirm_password: str) -> None:
        actor = self.actor
        check_confirmation(new_password, confirm_password)
        staff.reset_password(self.conn, actor, staff_id, new_password)

    def _changed(self, record: dict) -> dict:
        self.api.staffChanged.emit()
        self.api.session.staff_record_changed(record)
        return record


class SettingsBridge(BridgeObject):
    changed = Signal()

    def _read(self, key: str) -> str:
        try:
            return settings.get_all(self.conn)[key]
        except sqlite3.Error:
            return ""  # a display value only; the slots report the failure

    restaurantName = Property(str, lambda self: self._read("restaurant_name"), notify=changed)
    currencySymbol = Property(str, lambda self: self._read("currency_symbol"), notify=changed)

    @api_slot()
    def get(self) -> dict:
        """Readable before sign-in, since the sign-in screen shows the restaurant name."""
        return settings.get_all(self.conn)

    @api_slot("QVariantMap")
    def update(self, fields: dict) -> dict:
        actor = self.actor
        given = read_fields(fields, "restaurant_name", "currency_symbol", "tax_rate")
        result = settings.update(
            self.conn,
            actor,
            restaurant_name=given.get("restaurant_name"),
            currency_symbol=given.get("currency_symbol"),
            tax_rate_bp=_optional(given, "tax_rate", _parse_tax_rate),
        )
        self.changed.emit()
        return result


def _parse_tax_rate(text: object) -> int:
    """Typed percentage to basis points: "8.25" is 825."""
    try:
        return parse_money(str(text))
    except ValidationError:
        raise ValidationError(
            "Enter the tax rate as a percentage with at most two decimals, such as 8.25."
        ) from None


class BackupBridge(BridgeObject):
    folder = Property(str, lambda self: str(self.api.backup_dir), constant=True)
    folderUrl = Property(
        str, lambda self: QUrl.fromLocalFile(str(self.api.backup_dir)).toString(), constant=True
    )

    @api_slot()
    def create(self) -> dict:
        return backup.create_backup(self.conn, self.actor, self.api.backup_dir)

    @api_slot(str)
    def restore(self, path: str) -> dict:
        """Replace the database with a backup, reconnect, sign out and tell QML.

        If the restore fails the current database is untouched and the user stays
        signed in. The connection is reopened either way.
        """
        actor = self.actor
        require(actor, Permission.MANAGE_SETTINGS)  # before closing anything
        source = local_path(path)
        self.conn.close()
        try:
            result = backup.restore_backup(self.api.db_path, source, actor)
        finally:
            self.api.conn = connect(self.api.db_path)
        self.api.session.end("The backup was restored. Please sign in again.")
        self.api.settings.changed.emit()
        self.api.restored.emit()
        return result


class MenuBridge(BridgeObject):
    @api_slot(bool)
    def listCategories(self, include_inactive: bool) -> list:
        return menu.list_categories(self.conn, self.actor, include_inactive)

    @api_slot(str, "QVariant")
    def createCategory(self, name: str, sort_order: object) -> dict:
        actor = self.actor
        order = whole_number(sort_order, "sort order")
        return self._changed(menu.create_category(self.conn, actor, name, order))

    @api_slot(int, "QVariantMap")
    def updateCategory(self, category_id: int, fields: dict) -> dict:
        actor = self.actor
        given = read_fields(fields, "name", "sort_order")
        order = _optional(given, "sort_order", lambda v: whole_number(v, "sort order"))
        return self._changed(
            menu.update_category(self.conn, actor, category_id, given.get("name"), order)
        )

    @api_slot(int, bool)
    def setCategoryActive(self, category_id: int, active: bool) -> dict:
        return self._changed(menu.set_category_active(self.conn, self.actor, category_id, active))

    @api_slot(int, bool)
    def listItems(self, category_id: int, include_inactive: bool) -> list:
        """``category_id`` 0 means every category."""
        return menu.list_items(self.conn, self.actor, category_id or None, include_inactive)

    @api_slot()
    def listSellableItems(self) -> list:
        return menu.list_sellable_items(self.conn, self.actor)

    @api_slot(int)
    def getItem(self, item_id: int) -> dict:
        return menu.get_item(self.conn, self.actor, item_id)

    @api_slot(int, str, str)
    def createItem(self, category_id: int, name: str, price: str) -> dict:
        actor = self.actor
        cents = parse_money(price)
        return self._changed(menu.create_item(self.conn, actor, category_id, name, cents))

    @api_slot(int, "QVariantMap")
    def updateItem(self, item_id: int, fields: dict) -> dict:
        actor = self.actor
        given = read_fields(fields, "name", "category_id", "price")
        return self._changed(
            menu.update_item(
                self.conn,
                actor,
                item_id,
                name=given.get("name"),
                category_id=_optional(given, "category_id", lambda v: whole_number(v, "category")),
                price_cents=_optional(given, "price", lambda v: parse_money(str(v))),
            )
        )

    @api_slot(int, bool)
    def setItemActive(self, item_id: int, active: bool) -> dict:
        return self._changed(menu.set_item_active(self.conn, self.actor, item_id, active))

    @api_slot(int)
    def getRecipe(self, item_id: int) -> list:
        return menu.get_recipe(self.conn, self.actor, item_id)

    @api_slot(int, "QVariantList")
    def setRecipe(self, item_id: int, lines: list) -> list:
        """``lines`` is ``[{ingredient_id, qty}]``, with qty as typed text such as "0.25"."""
        actor = self.actor
        try:
            cleaned = [
                {
                    "ingredient_id": whole_number(line["ingredient_id"], "ingredient"),
                    "qty_milli": parse_quantity(str(line["qty"])),
                }
                for line in lines
            ]
        except (KeyError, TypeError):
            raise ValidationError("The recipe lines are not valid.") from None
        return self._changed(menu.set_recipe(self.conn, actor, item_id, cleaned))

    def _changed(self, result):
        self.api.menuChanged.emit()
        return result


class InventoryBridge(BridgeObject):
    @api_slot(bool)
    def listIngredients(self, include_inactive: bool) -> list:
        return inventory.list_ingredients(self.conn, self.actor, include_inactive)

    @api_slot(int)
    def getIngredient(self, ingredient_id: int) -> dict:
        return inventory.get_ingredient(self.conn, self.actor, ingredient_id)

    @api_slot(str, str, str, str)
    def createIngredient(self, name: str, unit: str, unit_cost: str, low: str) -> dict:
        actor = self.actor
        return self._changed(
            inventory.create_ingredient(
                self.conn, actor, name, unit, parse_money(unit_cost), parse_quantity(low)
            )
        )

    @api_slot(int, "QVariantMap")
    def updateIngredient(self, ingredient_id: int, fields: dict) -> dict:
        actor = self.actor
        given = read_fields(fields, "name", "unit", "unit_cost", "low")
        return self._changed(
            inventory.update_ingredient(
                self.conn,
                actor,
                ingredient_id,
                name=given.get("name"),
                unit=given.get("unit"),
                unit_cost_cents=_optional(given, "unit_cost", lambda v: parse_money(str(v))),
                low_milli=_optional(given, "low", lambda v: parse_quantity(str(v))),
            )
        )

    @api_slot(int, bool)
    def setIngredientActive(self, ingredient_id: int, active: bool) -> dict:
        return self._changed(
            inventory.set_ingredient_active(self.conn, self.actor, ingredient_id, active)
        )

    @api_slot(int, str, str)
    def receiveStock(self, ingredient_id: int, qty: str, note: str) -> dict:
        actor = self.actor
        return self._changed(
            inventory.receive_stock(self.conn, actor, ingredient_id, parse_quantity(qty), note)
        )

    @api_slot(int, str, str)
    def recordWaste(self, ingredient_id: int, qty: str, note: str) -> dict:
        actor = self.actor
        return self._changed(
            inventory.record_waste(self.conn, actor, ingredient_id, parse_quantity(qty), note)
        )

    @api_slot(int, str, str)
    def adjustStock(self, ingredient_id: int, delta: str, note: str) -> dict:
        """``delta`` is signed typed text, such as "-1.5"."""
        actor = self.actor
        return self._changed(
            inventory.adjust_stock(self.conn, actor, ingredient_id, parse_quantity(delta), note)
        )

    @api_slot(int, str, str)
    def recordCount(self, ingredient_id: int, counted: str, note: str) -> object:
        """Data is the adjustment movement, or "" when the count matched (FR-I3)."""
        actor = self.actor
        return self._changed(
            inventory.record_count(self.conn, actor, ingredient_id, parse_quantity(counted), note)
        )

    @api_slot()
    def listLowStock(self) -> list:
        return inventory.list_low_stock(self.conn, self.actor)

    @api_slot()
    def lowStockCount(self) -> int:
        self.actor  # noqa: B018 - the service takes no actor, so the guard is here
        return inventory.low_stock_count(self.conn)

    @api_slot(int, str, str, int, int)
    def listMovements(
        self, ingredient_id: int, date_from: str, date_to: str, limit: int, offset: int
    ) -> list:
        """``ingredient_id`` 0 means every ingredient; "" means no date limit."""
        return inventory.list_movements(
            self.conn,
            self.actor,
            ingredient_id or None,
            optional_date(date_from),
            optional_date(date_to),
            limit,
            offset,
        )

    def _changed(self, result):
        self.api.stockChanged.emit()
        return result


class AuditBridge(BridgeObject):
    @api_slot(int, int)
    def listEntries(self, limit: int, offset: int) -> list:
        return audit.list_entries(self.conn, self.actor, limit, offset)
