"""The Menu, Inventory, Staff, Settings and Audit screens, driven through the real window.

Buttons are clicked with real mouse events and forms filled in through their fields, so each
test covers the screen, the bridge and the service together. Any QML warning fails the test.
"""

from __future__ import annotations

import os
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QUICK_BACKEND", "software")
if os.environ.get("WINDIR"):
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ["WINDIR"]) / "Fonts"))

from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QEvent,
    QObject,
    Qt,
    QtMsgType,
    QUrl,
    qInstallMessageHandler,
)
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtQml import QQmlEngine, QQmlExpression  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from restaurant.bridge.api import MAIN_QML, Api, create_engine, destroy_engine  # noqa: E402
from restaurant.core import Actor, AuthenticationError, Role  # noqa: E402
from restaurant.db.connection import connect  # noqa: E402
from restaurant.services import audit, auth, inventory, menu, sales, settings, staff  # noqa: E402

SETTLE_MS = 30
PASSWORD = "correct-horse"
IGNORED_MESSAGES = ("propagateSizeHints", "QFontDatabase: Cannot find font directory")
VIEW_NAMES = {
    "menu": "menuView",
    "inventory": "inventoryView",
    "staff": "staffView",
    "settings": "settingsView",
    "audit": "auditView",
}


@pytest.fixture(scope="session")
def qapp():
    QQuickStyle.setStyle("Basic")
    return QApplication.instance() or QApplication([])


class Screen:
    """A signed-in window on one section, with the actions a person can take on it."""

    def __init__(self, api: Api, actor: Actor, section: str) -> None:
        self.api = api
        self.section = section
        self.problems: list[str] = []
        self._previous_handler = qInstallMessageHandler(self._on_message)
        api.session.start(actor)
        self.engine = create_engine(api)
        self.engine.warnings.connect(
            lambda errors: self.problems.extend(error.toString() for error in errors)
        )
        self.engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
        assert self.engine.rootObjects(), "Main.qml failed to load"
        self.window = self.engine.rootObjects()[0]
        self.window.setMinimumWidth(0)
        self.window.setMinimumHeight(0)
        self.window.resize(1280, 800)
        QTest.qWaitForWindowExposed(self.window)
        result = api.session.goTo(section)
        assert result["ok"], result
        self.settle()

    def _on_message(self, kind, _context, message: str) -> None:
        if kind in (QtMsgType.QtDebugMsg, QtMsgType.QtInfoMsg):
            return
        if not any(text in message for text in IGNORED_MESSAGES):
            self.problems.append(message)

    def close(self) -> None:
        qInstallMessageHandler(self._previous_handler)
        destroy_engine(self.engine)

    def settle(self) -> None:
        QTest.qWait(SETTLE_MS)

    @property
    def view(self) -> QObject:
        view = self.window.findChild(QObject, VIEW_NAMES[self.section])
        assert view is not None, f"{self.section} view is not showing"
        return view

    def evaluate(self, expression: str):
        """Run JavaScript in the view's own scope, where its ids are visible."""
        context = QQmlEngine.contextForObject(self.view)
        script = QQmlExpression(context, self.view, expression)
        value = script.evaluate()
        assert not script.hasError(), script.error().toString()
        return value[0] if isinstance(value, tuple) else value

    # --- Reading the screen ---------------------------------------------------------

    def _items(self) -> Iterator[QQuickItem]:
        pending = [self.window.contentItem()]
        while pending:
            item = pending.pop()
            if not item.isVisible():
                continue
            yield item
            pending.extend(item.childItems())

    def texts(self) -> list[str]:
        """Everything readable on screen, buttons' captions included."""
        return [item.property("text") for item in self._items() if item.inherits("QQuickText")]

    def page_text(self) -> str:
        return "\n".join(self.texts())

    def buttons(self) -> list[QQuickItem]:
        return [item for item in self._items() if item.inherits("QQuickAbstractButton")]

    def button_texts(self) -> list[str]:
        return [button.property("text") for button in self.buttons()]

    def _row_of(self, text: str, clickable: bool = False) -> QQuickItem:
        """The list row (a DataRow) showing this text."""
        for label in self._items():
            if label.inherits("QQuickText") and label.property("text") == text:
                item = label.parentItem()
                while item is not None:
                    if item.property("selected") is not None and item.property("clickable") is not None:
                        if item.property("clickable") or not clickable:
                            return item
                    item = item.parentItem()
        raise AssertionError(f"No row shows {text!r}. On screen: {self.texts()}")

    def row_texts(self, text: str) -> list[str]:
        pending = [self._row_of(text)]
        found = []
        while pending:
            item = pending.pop()
            if item.isVisible():
                if item.inherits("QQuickText"):
                    found.append(item.property("text"))
                pending.extend(item.childItems())
        return found

    def _field(self, label: str) -> QQuickItem:
        for item in self._items():
            if item.property("label") == label:
                return item
        known = [item.property("label") for item in self._items() if item.property("label")]
        raise AssertionError(f"No field labelled {label!r}. Fields: {known}")

    def field_text(self, label: str) -> str:
        return self._field(label).property("text")

    # --- Acting on the screen -------------------------------------------------------

    def _mouse_click(self, item: QQuickItem) -> None:
        centre = item.mapToScene(item.boundingRect().center()).toPoint()
        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre)
        self.settle()

    def click(self, text: str, row: str | None = None, outside_rows: bool = False) -> None:
        """Click a button by caption: the one in that row, or else the topmost.

        An open dialog's buttons are found first, as the overlay comes last in the window.
        """
        if row is None:
            buttons = self.buttons()
        else:
            pending = [self._row_of(row)]
            buttons = []
            while pending:
                item = pending.pop()
                if item.isVisible():
                    if item.inherits("QQuickAbstractButton"):
                        buttons.append(item)
                    pending.extend(item.childItems())
        matches = [button for button in buttons if button.property("text") == text]
        if outside_rows:
            matches = [button for button in matches if not self._in_a_row(button)]
        assert matches, f"No button {text!r} in {[b.property('text') for b in buttons]}"
        button = matches[0]
        assert button.isEnabled(), f"Button {text!r} is disabled"
        self._mouse_click(button)

    @staticmethod
    def _in_a_row(item: QQuickItem) -> bool:
        while item is not None:
            if item.property("selected") is not None and item.property("clickable") is not None:
                return True
            item = item.parentItem()
        return False

    def click_row(self, text: str) -> None:
        self._mouse_click(self._row_of(text, clickable=True))

    def is_enabled(self, text: str) -> bool:
        return next(b.isEnabled() for b in self.buttons() if b.property("text") == text)

    def type_in(self, label: str, text: str) -> None:
        self._field(label).setProperty("text", text)
        self.settle()

    def choose(self, label: str, text: str) -> None:
        combo = self._field(label)
        for index in range(100):
            combo.setProperty("currentIndex", index)
            if combo.property("currentText") == text:
                self.settle()
                return
        raise AssertionError(f"{label!r} does not offer {text!r}")

    def assert_clean(self) -> None:
        assert self.problems == []


@pytest.fixture
def api(qapp, conn, tmp_path) -> Iterator[Api]:
    bridge = Api(conn, ":memory:", tmp_path / "backups")
    yield bridge
    bridge.session.shutdown()


@pytest.fixture
def open_screen(api) -> Iterator:
    opened: list[Screen] = []

    def make(actor: Actor, section: str) -> Screen:
        screen = Screen(api, actor, section)
        opened.append(screen)
        return screen

    yield make
    for screen in opened:
        api.session.end()
        screen.settle()
        problems = list(screen.problems)
        screen.close()
        assert problems == []


def _actors(request, role: str) -> Actor:
    return request.getfixturevalue(role)


# --- Menu ----------------------------------------------------------------------------


@pytest.fixture
def pizza(conn, manager) -> dict:
    return menu.create_category(conn, manager, "Pizza", 0)


@pytest.fixture
def margherita(conn, manager, pizza) -> dict:
    return menu.create_item(conn, manager, pizza["id"], "Margherita", 1150)


@pytest.fixture
def flour(conn, manager) -> dict:
    return inventory.create_ingredient(conn, manager, "Flour", "kg", 120, 5000)


@pytest.fixture
def basil(conn, manager) -> dict:
    return inventory.create_ingredient(conn, manager, "Basil", "bunch", 175, 0)


def test_empty_menu_explains_itself_and_cannot_add_items_yet(open_screen, manager):
    screen = open_screen(manager, "menu")
    assert "The menu is empty. Create a category first, then add items to it." in screen.texts()
    assert "No categories yet." in screen.texts()
    assert screen.is_enabled("New item") is False


def test_create_category_and_item_through_the_forms(open_screen, conn, manager):
    screen = open_screen(manager, "menu")

    screen.click("New category")
    screen.type_in("Category name", "Drinks")
    screen.click("Save")
    categories = menu.list_categories(conn, manager)
    assert [c["name"] for c in categories] == ["Drinks"]
    assert "Drinks was saved." in screen.texts()

    screen.click("New item")
    screen.type_in("Item name", "Espresso")
    screen.type_in("Price before tax", "2.80")
    screen.click("Save")
    [item] = menu.list_items(conn, manager)
    assert (item["name"], item["price_cents"], item["category_id"]) == (
        "Espresso",
        280,
        categories[0]["id"],
    )
    assert "No recipe" in screen.row_texts("Espresso")
    assert "$2.80" in screen.row_texts("Espresso")


def test_a_form_can_be_completed_with_the_keyboard_alone(open_screen, conn, manager):
    screen = open_screen(manager, "menu")
    screen.click("New category")
    for character in "Soups":  # focus starts on the name
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            event = QKeyEvent(kind, Qt.Key(ord(character.upper())), Qt.KeyboardModifier.NoModifier, character)
            QCoreApplication.sendEvent(screen.window, event)
    QTest.keyClick(screen.window, Qt.Key.Key_Return)  # on to the position
    QTest.keyClick(screen.window, Qt.Key.Key_Return)  # and save
    screen.settle()
    assert [c["name"] for c in menu.list_categories(conn, manager)] == ["Soups"]
    assert "Category name" not in screen.texts()


def test_save_is_refused_until_the_required_fields_are_filled(open_screen, pizza, manager):
    screen = open_screen(manager, "menu")
    screen.click("New item")
    assert screen.is_enabled("Save") is False
    screen.type_in("Item name", "Marinara")
    assert screen.is_enabled("Save") is False
    screen.type_in("Price before tax", "9.50")
    assert screen.is_enabled("Save") is True


def test_duplicate_category_stays_in_the_dialog_with_the_reason(open_screen, conn, pizza, manager):
    screen = open_screen(manager, "menu")
    screen.click("New category")
    screen.type_in("Category name", "pizza")
    screen.click("Save")
    assert 'A category named "pizza" already exists.' in screen.texts()
    assert screen.field_text("Category name") == "pizza"  # still open, nothing lost
    assert len(menu.list_categories(conn, manager)) == 1
    screen.click("Cancel")
    assert "Category name" not in screen.texts()


def test_invalid_price_is_shown_and_nothing_is_created(open_screen, conn, pizza, manager):
    screen = open_screen(manager, "menu")
    screen.click("New item")
    screen.type_in("Item name", "Marinara")
    screen.type_in("Price before tax", "abc")
    screen.click("Save")
    assert "Enter a valid amount." in screen.texts()
    assert menu.list_items(conn, manager) == []


def test_edit_item_prefills_and_changes_the_price_with_an_audit_entry(
    open_screen, conn, margherita, manager
):
    screen = open_screen(manager, "menu")
    screen.click("Edit", row="Margherita")
    assert screen.field_text("Item name") == "Margherita"
    assert screen.field_text("Price before tax") == "11.50"
    screen.type_in("Price before tax", "12.00")
    screen.click("Save")
    assert menu.get_item(conn, manager, margherita["id"])["price_cents"] == 1200
    assert "$12.00" in screen.row_texts("Margherita")
    actions = [entry["action"] for entry in audit.list_entries(conn, manager)]
    assert "menu.price_changed" in actions


def test_item_can_move_to_another_category(open_screen, conn, pizza, margherita, manager):
    drinks = menu.create_category(conn, manager, "Drinks", 1)
    screen = open_screen(manager, "menu")
    screen.click("Edit", row="Margherita")
    screen.choose("Category", "Drinks")
    screen.click("Save")
    assert menu.get_item(conn, manager, margherita["id"])["category_id"] == drinks["id"]


def test_deactivating_an_item_confirms_first_and_can_be_undone(
    open_screen, conn, margherita, manager
):
    screen = open_screen(manager, "menu")

    screen.click("Deactivate", row="Margherita")
    assert "Deactivate Margherita?" in screen.texts()
    screen.click("Cancel")
    assert menu.get_item(conn, manager, margherita["id"])["active"] is True

    screen.click("Deactivate", row="Margherita")
    screen.click("Deactivate")
    assert menu.get_item(conn, manager, margherita["id"])["active"] is False
    assert "Inactive" in screen.row_texts("Margherita")
    assert menu.list_sellable_items(conn, manager) == []

    screen.click("Activate", row="Margherita")
    assert menu.get_item(conn, manager, margherita["id"])["active"] is True
    assert "Margherita is active again." in screen.texts()


def test_filtering_by_category_and_deactivating_it(open_screen, conn, pizza, margherita, manager):
    drinks = menu.create_category(conn, manager, "Drinks", 1)
    menu.create_item(conn, manager, drinks["id"], "Espresso", 280)
    screen = open_screen(manager, "menu")
    assert "Margherita" in screen.texts() and "Espresso" in screen.texts()

    screen.click_row("Drinks")
    assert "Espresso" in screen.texts()
    assert "Margherita" not in screen.texts()

    screen.click("Deactivate", outside_rows=True)  # the category's, not an item's
    assert "Deactivate Drinks?" in screen.texts()
    screen.click("Deactivate")
    assert "Category inactive" in screen.row_texts("Espresso")
    assert [i["name"] for i in menu.list_sellable_items(conn, manager)] == ["Margherita"]


def test_edit_category_renames_and_reorders(open_screen, conn, pizza, manager):
    screen = open_screen(manager, "menu")
    screen.click_row("Pizza")
    screen.click("Edit")
    assert screen.field_text("Category name") == "Pizza"
    screen.type_in("Category name", "Pizzas")
    screen.type_in("Position in the list (lower numbers come first)", "7")
    screen.click("Save")
    [category] = menu.list_categories(conn, manager)
    assert (category["name"], category["sort_order"]) == ("Pizzas", 7)


def test_invalid_sort_order_is_reported(open_screen, conn, pizza, manager):
    screen = open_screen(manager, "menu")
    screen.click_row("Pizza")
    screen.click("Edit")
    screen.type_in("Position in the list (lower numbers come first)", "soon")
    screen.click("Save")
    assert "Enter a whole number for the sort order." in screen.texts()
    assert menu.list_categories(conn, manager)[0]["sort_order"] == 0


def test_recipe_editor_adds_lines_and_saves_exact_quantities(
    open_screen, conn, margherita, flour, basil, manager
):
    screen = open_screen(manager, "menu")
    assert "No recipe" in screen.row_texts("Margherita")

    screen.click("Recipe", row="Margherita")
    assert "No recipe: selling this item deducts no stock" in screen.texts()
    screen.choose("Add an ingredient", "Flour (kg)")
    screen.type_in("Quantity", "0.25")
    screen.click("Add")
    assert "Flour" in screen.texts()
    # Only what is left is offered, so an ingredient can't be chosen twice.
    screen.choose("Add an ingredient", "Basil (bunch)")
    with pytest.raises(AssertionError):
        screen.choose("Add an ingredient", "Flour (kg)")
    screen.choose("Add an ingredient", "Basil (bunch)")
    screen.type_in("Quantity", "0.1")  # typed but never added: Save must not lose it
    screen.click("Save recipe")

    recipe = menu.get_recipe(conn, manager, margherita["id"])
    assert {line["ingredient_name"]: line["qty_milli"] for line in recipe} == {
        "Flour": 250,
        "Basil": 100,
    }
    assert "No recipe" not in screen.row_texts("Margherita")
    assert "The recipe for Margherita was saved." in screen.texts()


def test_recipe_editor_changes_and_removes_existing_lines(
    open_screen, conn, margherita, flour, basil, manager
):
    menu.set_recipe(
        conn,
        manager,
        margherita["id"],
        [
            {"ingredient_id": flour["id"], "qty_milli": 250},
            {"ingredient_id": basil["id"], "qty_milli": 100},
        ],
    )
    screen = open_screen(manager, "menu")
    screen.click("Recipe", row="Margherita")
    assert screen.evaluate("recipeDialog.visible") is True

    fields = [i for i in screen._items() if i.property("label") == "" and i.property("errorText") == ""]
    by_value = {field.property("text"): field for field in fields}
    assert set(by_value) == {"0.25", "0.1"}
    by_value["0.25"].setProperty("text", "0.3")
    screen.settle()
    removes = [b for b in screen.buttons() if b.property("text") == "Remove"]
    removes.sort(key=lambda button: button.mapToScene(button.boundingRect().center()).y())
    screen._mouse_click(removes[0])  # lines are in name order, so this is Basil
    screen.click("Save recipe")

    recipe = menu.get_recipe(conn, manager, margherita["id"])
    assert [(r["ingredient_name"], r["qty_milli"]) for r in recipe] == [("Flour", 300)]


def test_removing_every_line_clears_the_recipe(open_screen, conn, margherita, flour, manager):
    menu.set_recipe(conn, manager, margherita["id"], [{"ingredient_id": flour["id"], "qty_milli": 250}])
    screen = open_screen(manager, "menu")
    screen.click("Recipe", row="Margherita")
    screen.click("Remove")
    assert "No recipe: selling this item deducts no stock" in screen.texts()
    screen.click("Save recipe")
    assert menu.get_recipe(conn, manager, margherita["id"]) == []
    assert "No recipe" in screen.row_texts("Margherita")
    assert "Margherita now has no recipe, so selling it deducts no stock." in screen.texts()


@pytest.mark.parametrize("quantity", ["0", "-1", "abc", "0.0001"])
def test_bad_recipe_quantity_keeps_the_dialog_and_the_old_recipe(
    open_screen, conn, margherita, flour, basil, manager, quantity
):
    menu.set_recipe(conn, manager, margherita["id"], [{"ingredient_id": flour["id"], "qty_milli": 250}])
    screen = open_screen(manager, "menu")
    screen.click("Recipe", row="Margherita")
    screen.choose("Add an ingredient", "Basil (bunch)")
    screen.type_in("Quantity", quantity)
    screen.click("Add")
    screen.click("Save recipe")
    assert any(text.startswith("Enter a valid") for text in screen.texts())
    assert "Save recipe" in screen.button_texts()  # still open
    recipe = menu.get_recipe(conn, manager, margherita["id"])
    assert [(r["ingredient_name"], r["qty_milli"]) for r in recipe] == [("Flour", 250)]


def test_cancelling_the_recipe_editor_changes_nothing(open_screen, conn, margherita, flour, manager):
    screen = open_screen(manager, "menu")
    screen.click("Recipe", row="Margherita")
    screen.choose("Add an ingredient", "Flour (kg)")
    screen.type_in("Quantity", "1")
    screen.click("Add")
    screen.click("Cancel")
    assert menu.get_recipe(conn, manager, margherita["id"]) == []
    # Reopening starts again from what is stored.
    screen.click("Recipe", row="Margherita")
    assert "No recipe: selling this item deducts no stock" in screen.texts()
    assert screen.field_text("Quantity") == ""


def test_recipe_editor_only_offers_active_ingredients(
    open_screen, conn, margherita, flour, basil, manager
):
    inventory.set_ingredient_active(conn, manager, basil["id"], False)
    screen = open_screen(manager, "menu")
    screen.click("Recipe", row="Margherita")
    with pytest.raises(AssertionError):
        screen.choose("Add an ingredient", "Basil (bunch)")
    screen.choose("Add an ingredient", "Flour (kg)")


def test_recipe_editor_with_no_ingredients_says_where_to_add_them(open_screen, margherita, manager):
    screen = open_screen(manager, "menu")
    screen.click("Recipe", row="Margherita")
    assert "There are no active ingredients yet. Add them on the Inventory screen." in screen.texts()


def test_recipe_with_an_inactive_ingredient_is_flagged_and_refused(
    open_screen, conn, margherita, flour, manager
):
    menu.set_recipe(conn, manager, margherita["id"], [{"ingredient_id": flour["id"], "qty_milli": 250}])
    menu.set_item_active(conn, manager, margherita["id"], False)
    inventory.set_ingredient_active(conn, manager, flour["id"], False)
    screen = open_screen(manager, "menu")
    screen.click("Recipe", row="Margherita")
    assert "Inactive" in screen.texts()
    screen.click("Save recipe")
    assert '"Flour" is inactive and can\'t be used in a recipe.' in screen.texts()


def test_activating_an_item_with_inactive_ingredients_shows_why_not(
    open_screen, conn, margherita, flour, manager
):
    menu.set_recipe(conn, manager, margherita["id"], [{"ingredient_id": flour["id"], "qty_milli": 250}])
    menu.set_item_active(conn, manager, margherita["id"], False)
    inventory.set_ingredient_active(conn, manager, flour["id"], False)
    screen = open_screen(manager, "menu")
    screen.click("Activate", row="Margherita")
    assert any("recipe uses inactive ingredients: Flour" in text for text in screen.texts())
    assert menu.get_item(conn, manager, margherita["id"])["active"] is False


@pytest.mark.parametrize("role", ["cashier", "stock_clerk"])
def test_menu_is_read_only_without_edit_menu(
    request, open_screen, conn, margherita, flour, manager, role
):
    menu.set_recipe(conn, manager, margherita["id"], [{"ingredient_id": flour["id"], "qty_milli": 250}])
    screen = open_screen(_actors(request, role), "menu")
    captions = screen.button_texts()
    for forbidden in ("New category", "New item", "Edit", "Deactivate", "Activate"):
        assert forbidden not in captions
    assert "Margherita" in screen.texts()
    assert "$11.50" in screen.row_texts("Margherita")

    screen.click_row("Pizza")  # selecting a category offers nothing to change either
    assert "Edit" not in screen.button_texts()

    screen.click("Recipe", row="Margherita")
    assert "Recipe for Margherita" in screen.texts()
    assert "Flour" in screen.texts() and "0.25" in screen.texts() and "kg" in screen.texts()
    captions = screen.button_texts()
    assert "Close" in captions
    for forbidden in ("Save recipe", "Remove", "Add"):
        assert forbidden not in captions
    screen.click("Close")
    assert "Recipe for Margherita" not in screen.texts()


@pytest.mark.parametrize("role", ["cashier", "stock_clerk"])
def test_read_only_recipe_shows_no_recipe_mark(request, open_screen, margherita, role):
    screen = open_screen(_actors(request, role), "menu")
    assert "No recipe" in screen.row_texts("Margherita")
    screen.click("Recipe", row="Margherita")
    assert "No recipe: selling this item deducts no stock" in screen.texts()


# --- Inventory -----------------------------------------------------------------------


@pytest.mark.parametrize("role", ["manager", "stock_clerk"])
def test_create_and_edit_ingredient(request, open_screen, conn, manager, role):
    screen = open_screen(_actors(request, role), "inventory")
    assert "No ingredients yet. Add the first one with New ingredient." in screen.texts()

    screen.click("New ingredient")
    screen.type_in("Ingredient name", "Flour")
    screen.type_in("Unit", "kg")
    screen.type_in("Cost per unit", "1.20")
    screen.type_in("Low-stock level", "5")
    screen.click("Save")
    [row] = inventory.list_ingredients(conn, manager)
    assert (row["name"], row["unit"], row["unit_cost_cents"], row["low_milli"], row["on_hand_milli"]) == (
        "Flour",
        "kg",
        120,
        5000,
        0,
    )
    assert "$1.20" in screen.row_texts("Flour")

    screen.click("Edit")
    assert screen.field_text("Cost per unit") == "1.20"
    assert screen.field_text("Low-stock level") == "5"
    screen.type_in("Cost per unit", "1.35")
    screen.click("Save")
    assert inventory.list_ingredients(conn, manager)[0]["unit_cost_cents"] == 135


def test_duplicate_ingredient_is_refused_in_the_dialog(open_screen, conn, flour, manager):
    screen = open_screen(manager, "inventory")
    screen.click("New ingredient")
    screen.type_in("Ingredient name", "flour")
    screen.type_in("Unit", "kg")
    screen.type_in("Cost per unit", "1")
    screen.type_in("Low-stock level", "1")
    screen.click("Save")
    assert 'An ingredient named "flour" already exists.' in screen.texts()
    assert len(inventory.list_ingredients(conn, manager)) == 1


@pytest.mark.parametrize("role", ["manager", "stock_clerk"])
def test_receive_waste_and_count_update_stock_and_history(
    request, open_screen, conn, flour, manager, role
):
    actor = _actors(request, role)
    screen = open_screen(actor, "inventory")

    screen.click("Receive")
    assert "On hand now: 0 kg" in screen.texts()
    screen.type_in("Quantity received (kg)", "40")
    screen.type_in("Note (optional)", "Delivery 12")
    screen.click("Receive")
    assert inventory.get_ingredient(conn, manager, flour["id"])["on_hand_milli"] == 40000
    assert "Flour: +40 kg recorded." in screen.texts()
    assert "Delivery 12" in screen.texts()

    screen.click("Waste")
    screen.type_in("Quantity wasted (kg)", "1.5")
    screen.click("Record waste")
    assert inventory.get_ingredient(conn, manager, flour["id"])["on_hand_milli"] == 38500
    assert "Flour: -1.5 kg recorded." in screen.texts()

    screen.click("Count")
    screen.type_in("Quantity counted (kg)", "38")
    screen.click("Save count")
    assert inventory.get_ingredient(conn, manager, flour["id"])["on_hand_milli"] == 38000
    assert "Flour: -0.5 kg recorded." in screen.texts()

    history = inventory.list_movements(conn, manager, flour["id"])
    assert [m["movement_type"] for m in history] == ["adjusted", "wasted", "received"]
    assert all(m["staff_id"] == actor.id for m in history)
    assert {"Adjusted", "Wasted", "Received", "-0.5 kg", "-1.5 kg", "+40 kg"} <= set(screen.texts())


def test_a_matching_count_writes_no_movement_and_says_so(open_screen, conn, flour, manager):
    inventory.receive_stock(conn, manager, flour["id"], 10000)
    screen = open_screen(manager, "inventory")
    screen.click("Count")
    screen.type_in("Quantity counted (kg)", "10")
    screen.click("Save count")
    assert "The count for Flour matches, so the stock level was not changed." in screen.texts()
    assert len(inventory.list_movements(conn, manager, flour["id"])) == 1


@pytest.mark.parametrize(
    "button, field, confirm_button, text",
    [
        ("Receive", "Quantity received (kg)", "Receive", "abc"),
        ("Receive", "Quantity received (kg)", "Receive", "-1"),
        ("Waste", "Quantity wasted (kg)", "Record waste", "0"),
        ("Count", "Quantity counted (kg)", "Save count", "-3"),
    ],
)
def test_bad_stock_quantities_are_shown_and_change_nothing(
    open_screen, conn, flour, manager, button, field, confirm_button, text
):
    screen = open_screen(manager, "inventory")
    screen.click(button)
    screen.type_in(field, text)
    screen.click(confirm_button)
    assert any(line.startswith("Enter a valid") for line in screen.texts())
    assert field in screen.texts()  # the dialog stays open
    assert inventory.list_movements(conn, manager, flour["id"]) == []
    assert inventory.get_ingredient(conn, manager, flour["id"])["on_hand_milli"] == 0


def test_low_and_negative_stock_are_flagged_in_words_and_filtered(
    open_screen, conn, flour, basil, manager
):
    pepper = inventory.create_ingredient(conn, manager, "Pepper", "g", 10, 100)
    inventory.receive_stock(conn, manager, flour["id"], 20000)  # well above 5
    inventory.receive_stock(conn, manager, pepper["id"], 100)  # exactly at threshold
    inventory.record_waste(conn, manager, basil["id"], 2000)  # below zero

    screen = open_screen(manager, "inventory")
    assert "OK" in screen.row_texts("Flour")
    assert "! Low" in screen.row_texts("Pepper")
    assert "! Below zero" in screen.row_texts("Basil")
    assert "-2" in screen.row_texts("Basil")
    assert "2 ingredients are at or below their low-stock level." in screen.texts()

    screen.click("Low stock (2)")
    assert "Pepper" in screen.texts() and "Basil" in screen.texts()
    assert "Flour" not in screen.texts()

    screen.click("All ingredients")
    assert "Flour" in screen.texts()


def test_low_filter_with_nothing_low_says_so(open_screen, conn, flour, manager):
    inventory.receive_stock(conn, manager, flour["id"], 20000)
    screen = open_screen(manager, "inventory")
    assert "Nothing is at or below its low-stock level." in screen.texts()
    screen.click("Low stock (0)")
    assert "Nothing is low on stock." in screen.texts()
    assert "Select an ingredient to see its stock and history." in screen.texts()


def test_receiving_out_of_low_stock_drops_it_from_the_filter(open_screen, conn, flour, basil, manager):
    inventory.receive_stock(conn, manager, flour["id"], 20000)
    inventory.update_ingredient(conn, manager, basil["id"], low_milli=1000)
    screen = open_screen(manager, "inventory")
    screen.click("Low stock (1)")
    screen.click("Receive")
    screen.type_in("Quantity received (bunch)", "9")
    screen.click("Receive")
    assert "Low stock (0)" in screen.button_texts()
    assert "Basil: +9 bunch recorded." in screen.texts()


def test_selecting_an_ingredient_shows_its_own_history(open_screen, conn, flour, basil, manager):
    inventory.receive_stock(conn, manager, flour["id"], 1000, "for flour")
    inventory.receive_stock(conn, manager, basil["id"], 2000, "for basil")
    screen = open_screen(manager, "inventory")
    screen.click_row("Flour")
    assert "for flour" in screen.texts() and "for basil" not in screen.texts()
    screen.click_row("Basil")
    assert "for basil" in screen.texts() and "for flour" not in screen.texts()


def test_history_pages_through_older_movements(open_screen, conn, flour, manager):
    for _ in range(55):
        inventory.receive_stock(conn, manager, flour["id"], 1000)
    screen = open_screen(manager, "inventory")
    assert screen.evaluate("root.movements.length") == 50
    screen.click("Show older movements")
    assert screen.evaluate("root.movements.length") == 55
    assert "Show older movements" not in screen.button_texts()


def test_deactivation_used_by_a_recipe_is_refused_on_the_page(
    open_screen, conn, margherita, flour, manager
):
    menu.set_recipe(conn, manager, margherita["id"], [{"ingredient_id": flour["id"], "qty_milli": 250}])
    screen = open_screen(manager, "inventory")
    screen.click("Deactivate")
    assert "Deactivate Flour?" in screen.texts()
    screen.click("Deactivate")
    assert any("these active menu items use it: Margherita" in text for text in screen.texts())
    assert inventory.get_ingredient(conn, manager, flour["id"])["active"] is True


def test_inactive_ingredient_cannot_change_stock_and_can_come_back(open_screen, conn, flour, manager):
    screen = open_screen(manager, "inventory")
    screen.click("Deactivate")
    screen.click("Cancel")
    assert inventory.get_ingredient(conn, manager, flour["id"])["active"] is True

    screen.click("Deactivate")
    screen.click("Deactivate")
    assert inventory.get_ingredient(conn, manager, flour["id"])["active"] is False
    assert "Inactive" in screen.row_texts("Flour")
    for caption in ("Receive", "Waste", "Count"):
        assert screen.is_enabled(caption) is False

    screen.click("Activate")
    assert inventory.get_ingredient(conn, manager, flour["id"])["active"] is True
    assert screen.is_enabled("Receive") is True


def test_a_sale_elsewhere_refreshes_the_list_with_a_negative_flag(
    open_screen, api, conn, margherita, flour, cashier, manager
):
    menu.set_recipe(conn, manager, margherita["id"], [{"ingredient_id": flour["id"], "qty_milli": 250}])
    screen = open_screen(manager, "inventory")
    sales.record_sale(conn, cashier, [{"menu_item_id": margherita["id"], "quantity": 2}], "cash")
    api.stockChanged.emit()
    screen.settle()
    assert "! Below zero" in screen.row_texts("Flour")
    assert "-0.5" in screen.row_texts("Flour")


def test_inventory_is_closed_to_cashiers_even_if_loaded(open_screen, api, cashier):
    screen = open_screen(cashier, "menu")
    assert "Inventory" not in [s["key"] for s in api.session.sections]
    assert api.session.goTo("inventory")["ok"] is False
    assert api.inventory.receiveStock(1, "1", "")["ok"] is False
    screen.assert_clean()


# --- Staff ---------------------------------------------------------------------------


def test_staff_list_marks_you_and_deactivated_people(open_screen, conn, manager, cashier):
    staff.deactivate_staff(conn, manager, cashier.id)
    screen = open_screen(manager, "staff")
    assert "You" in screen.row_texts("manager")
    assert "Manager" in screen.row_texts("manager")
    assert "Deactivated" in screen.row_texts(cashier.full_name)
    assert "You" not in screen.row_texts(cashier.full_name)
    assert "Reactivate" in screen.button_texts()


def test_create_staff_through_the_form_can_sign_in(open_screen, conn, manager):
    screen = open_screen(manager, "staff")
    screen.click("New staff member")
    screen.type_in("Full name", "Carlos Diaz")
    screen.type_in("Username", "carlos")
    screen.choose("Role", "Stock clerk")
    screen.type_in("Password (at least 8 characters)", PASSWORD)
    screen.type_in("Password again", PASSWORD)
    screen.click("Save")
    assert "Stock clerk" in screen.row_texts("Carlos Diaz")
    assert "Carlos Diaz was saved." in screen.texts()
    assert auth.authenticate(conn, "carlos", PASSWORD).role is Role.STOCK_CLERK


@pytest.mark.parametrize(
    "password, again, message",
    [
        (PASSWORD, "different-one", "The two passwords don't match."),
        ("short", "short", None),
    ],
)
def test_create_staff_with_bad_passwords_is_refused(open_screen, conn, manager, password, again, message):
    screen = open_screen(manager, "staff")
    screen.click("New staff member")
    screen.type_in("Full name", "Carlos Diaz")
    screen.type_in("Username", "carlos")
    screen.type_in("Password (at least 8 characters)", password)
    screen.type_in("Password again", again)
    screen.click("Save")
    assert "Password again" in screen.texts()  # still open
    if message:
        assert message in screen.texts()
    assert [s["username"] for s in staff.list_staff(conn, manager)] == ["manager"]


def test_new_staff_form_never_keeps_an_old_password(open_screen, manager):
    screen = open_screen(manager, "staff")
    screen.click("New staff member")
    screen.type_in("Password (at least 8 characters)", PASSWORD)
    screen.click("Cancel")
    screen.click("New staff member")
    assert screen.field_text("Password (at least 8 characters)") == ""


def test_duplicate_username_is_refused(open_screen, conn, manager, cashier):
    screen = open_screen(manager, "staff")
    screen.click("New staff member")
    screen.type_in("Full name", "Another")
    screen.type_in("Username", "cashier")
    screen.type_in("Password (at least 8 characters)", PASSWORD)
    screen.type_in("Password again", PASSWORD)
    screen.click("Save")
    assert 'The username "cashier" is already taken.' in screen.texts()


def test_edit_changes_name_and_role(open_screen, conn, manager, cashier):
    screen = open_screen(manager, "staff")
    screen.click("Edit", row=cashier.full_name)
    assert screen.field_text("Full name") == cashier.full_name
    assert screen.field_text("Username (can't be changed)") == "cashier"
    screen.type_in("Full name", "Carla")
    screen.choose("Role", "Manager")
    screen.click("Save")
    record = next(s for s in staff.list_staff(conn, manager) if s["id"] == cashier.id)
    assert (record["full_name"], record["role"]) == ("Carla", "manager")


def test_last_manager_cannot_be_deactivated_and_the_page_says_why(open_screen, conn, manager):
    screen = open_screen(manager, "staff")
    screen.click("Deactivate", row="manager")
    assert any("your own account" in text for text in screen.texts())
    screen.click("Deactivate")
    assert "This is the last active manager. Make another manager first." in screen.texts()
    assert staff.list_staff(conn, manager)[0]["active"] == 1
    assert screen.api.session.signedIn is True


def test_last_manager_cannot_change_role_and_the_dialog_says_why(open_screen, conn, manager):
    screen = open_screen(manager, "staff")
    screen.click("Edit", row="manager")
    assert "This is your own account. Changing your role signs you out." not in screen.texts()
    screen.choose("Role", "Cashier")
    assert "This is your own account. Changing your role signs you out." in screen.texts()
    screen.click("Save")
    assert "This is the last active manager. Make another manager first." in screen.texts()
    assert staff.list_staff(conn, manager)[0]["role"] == "manager"
    assert screen.api.session.signedIn is True


def test_deactivate_confirms_first_then_reactivate(open_screen, conn, manager, cashier):
    screen = open_screen(manager, "staff")
    screen.click("Deactivate", row=cashier.full_name)
    assert f"Deactivate {cashier.full_name}?" in screen.texts()
    screen.click("Cancel")
    assert _staff_record(conn, manager, cashier)["active"] == 1

    screen.click("Deactivate", row=cashier.full_name)
    screen.click("Deactivate")
    assert _staff_record(conn, manager, cashier)["active"] == 0
    assert f"{cashier.full_name} was deactivated." in screen.texts()

    screen.click("Reactivate", row=cashier.full_name)
    assert _staff_record(conn, manager, cashier)["active"] == 1


def _staff_record(conn, manager, person) -> dict:
    return next(s for s in staff.list_staff(conn, manager) if s["id"] == person.id)


def test_reset_password_replaces_it_and_shows_no_password(open_screen, conn, manager, cashier):
    screen = open_screen(manager, "staff")
    screen.click("Reset password", row=cashier.full_name)
    assert f"Reset password: {cashier.full_name}" in screen.texts()
    screen.type_in("New password (at least 8 characters)", PASSWORD)
    screen.type_in("New password again", PASSWORD)
    screen.click("Reset password")  # the dialog's button, not the rows'
    assert f"The password for {cashier.full_name} was changed." in screen.texts()
    assert auth.authenticate(conn, "cashier", PASSWORD).id == cashier.id
    details = " ".join(entry["details"] for entry in audit.list_entries(conn, manager))
    assert PASSWORD not in details


def test_reset_password_mismatch_changes_nothing(open_screen, conn, manager, cashier):
    screen = open_screen(manager, "staff")
    screen.click("Reset password", row=cashier.full_name)
    screen.type_in("New password (at least 8 characters)", PASSWORD)
    screen.type_in("New password again", "something-else")
    screen.click("Reset password")
    assert "The two passwords don't match." in screen.texts()
    with pytest.raises(AuthenticationError):
        auth.authenticate(conn, "cashier", PASSWORD)


def test_demoting_yourself_signs_you_out_without_warnings(open_screen, conn, manager):
    other = staff.create_staff(conn, manager, "second", "Second Manager", "manager", PASSWORD)
    screen = open_screen(manager, "staff")
    screen.click("Edit", row="manager")
    screen.choose("Role", "Cashier")
    screen.click("Save")
    assert screen.api.session.signedIn is False
    assert screen.api.session.notice == "Your account was changed. Please sign in again."
    assert _staff_record(conn, Actor(other["id"], "second", "Second Manager", Role.MANAGER), manager)["role"] == "cashier"
    screen.assert_clean()


def test_deactivating_yourself_signs_you_out_without_warnings(open_screen, conn, manager):
    staff.create_staff(conn, manager, "second", "Second Manager", "manager", PASSWORD)
    screen = open_screen(manager, "staff")
    screen.click("Deactivate", row="manager")
    screen.click("Deactivate")
    assert screen.api.session.signedIn is False
    screen.assert_clean()


# --- Settings ------------------------------------------------------------------------


def test_settings_show_current_values_and_save(open_screen, conn, manager):
    settings.update(conn, manager, restaurant_name="Olive", tax_rate_bp=825)
    screen = open_screen(manager, "settings")
    assert screen.field_text("Restaurant name") == "Olive"
    assert screen.field_text("Currency symbol") == "$"
    assert screen.field_text("Tax rate (%)") == "8.25"

    screen.type_in("Restaurant name", "Thyme")
    screen.type_in("Currency symbol", "EUR")
    screen.type_in("Tax rate (%)", "10.5")
    screen.click("Save settings")
    saved = settings.get_all(conn)
    assert (saved["restaurant_name"], saved["currency_symbol"], saved["tax_rate_bp"]) == (
        "Thyme",
        "EUR",
        1050,
    )
    assert "The settings were saved." in screen.texts()
    assert screen.window.property("title") == "Thyme"  # the shell follows


@pytest.mark.parametrize(
    "label, value",
    [
        ("Tax rate (%)", "abc"),
        ("Tax rate (%)", "8.255"),
        ("Tax rate (%)", "-1"),
        ("Tax rate (%)", "100.01"),
        ("Restaurant name", "   "),
    ],
)
def test_invalid_settings_are_reported_and_nothing_is_saved(open_screen, conn, manager, label, value):
    settings.update(conn, manager, restaurant_name="Olive", currency_symbol="$", tax_rate_bp=825)
    screen = open_screen(manager, "settings")
    screen.type_in("Currency symbol", "EUR")  # valid, and must not be saved with the bad one
    screen.type_in(label, value)
    screen.click("Save settings")
    assert "The settings were saved." not in screen.texts()
    assert screen.evaluate("banner.text !== '' && banner.kind === 'error'") is True
    saved = settings.get_all(conn)
    assert (saved["restaurant_name"], saved["currency_symbol"], saved["tax_rate_bp"]) == (
        "Olive",
        "$",
        825,
    )


def test_back_up_now_writes_a_file_and_shows_where(open_screen, api, manager):
    screen = open_screen(manager, "settings")
    screen.click("Back up now")
    files = list(Path(api.backup_dir).glob("restaurant-backup-*.db"))
    assert len(files) == 1
    assert f"Backup saved: {files[0]}" in screen.texts()


def test_backup_failure_is_shown(open_screen, api, conn, manager, tmp_path):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    api.backup_dir = blocker
    screen = open_screen(manager, "settings")
    screen.click("Back up now")
    assert any(text.startswith("The backup could not be saved") for text in screen.texts())


def test_restore_button_opens_the_file_chooser_in_the_backup_folder(open_screen, api, manager):
    Path(api.backup_dir).mkdir(parents=True)
    screen = open_screen(manager, "settings")
    assert screen.evaluate("restoreFile.currentFolder.toString()") == QUrl.fromLocalFile(
        str(api.backup_dir)
    ).toString()
    screen.click("Restore from a backup…")
    assert screen.evaluate("restoreFile.visible") is True
    screen.evaluate("restoreFile.close()")
    screen.settle()
    assert screen.evaluate("restoreConfirm.visible") is False  # no file chosen, no warning

    # The file chooser's answer leads to the warning, and nothing has happened yet.
    backup_file = Path(api.backup_dir) / "restaurant-backup-1.db"
    backup_file.write_bytes(b"chosen, not read yet")
    chosen = QUrl.fromLocalFile(str(backup_file)).toString()
    screen.evaluate(f'restoreFile.selectedFile = "{chosen}"; restoreFile.accepted()')
    screen.settle()
    assert screen.evaluate("restoreConfirm.visible") is True
    assert "restaurant-backup-1.db" in screen.page_text()


@pytest.fixture
def file_screen(qapp, tmp_path) -> Iterator:
    """A manager on the Settings screen over a real database file, needed for restoring."""
    db_path = tmp_path / "restaurant.db"
    bridge = Api(connect(db_path), db_path, tmp_path / "backups")
    record = auth.create_first_manager(bridge.conn, "maria", "Maria", PASSWORD)
    actor = Actor(record["id"], "maria", "Maria", Role.MANAGER)
    screen = Screen(bridge, actor, "settings")
    yield screen, actor
    bridge.session.end()
    screen.settle()
    problems = list(screen.problems)
    screen.close()
    bridge.shutdown()
    assert problems == []


def test_restore_warns_then_replaces_data_and_signs_out(file_screen):
    screen, actor = file_screen
    api = screen.api
    saved = api.backup.create()["data"]["path"]
    settings.update(api.conn, actor, restaurant_name="Changed after the backup")

    screen.view.askRestore(QUrl.fromLocalFile(saved).toString())
    screen.settle()
    warning = screen.page_text()
    assert "All current data will be replaced" in warning
    assert Path(saved).name in warning
    assert "You will be signed out" in warning

    screen.click("Cancel")  # backing out changes nothing
    assert settings.get_all(api.conn)["restaurant_name"] == "Changed after the backup"
    assert api.session.signedIn is True

    screen.view.askRestore(QUrl.fromLocalFile(saved).toString())
    screen.settle()
    screen.click("Replace data and sign out")
    assert api.session.signedIn is False
    assert api.session.notice == "The backup was restored. Please sign in again."
    assert settings.get_all(api.conn)["restaurant_name"] == "My Restaurant"
    assert screen.window.property("title") == "My Restaurant"
    screen.assert_clean()


def test_restoring_a_bad_file_keeps_the_data_and_the_session(file_screen, tmp_path):
    screen, actor = file_screen
    api = screen.api
    settings.update(api.conn, actor, restaurant_name="Kept")
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not a database")

    screen.view.askRestore(QUrl.fromLocalFile(str(junk)).toString())
    screen.settle()
    screen.click("Replace data and sign out")
    assert "That file is not a valid restaurant backup." in screen.texts()
    assert api.session.signedIn is True
    assert settings.get_all(api.conn)["restaurant_name"] == "Kept"


def test_saved_settings_are_shown_as_stored(open_screen, manager):
    screen = open_screen(manager, "settings")
    screen.type_in("Restaurant name", "  Thyme  ")
    screen.type_in("Tax rate (%)", "8.5")
    screen.click("Save settings")
    assert screen.field_text("Restaurant name") == "Thyme"
    assert screen.field_text("Tax rate (%)") == "8.50"


# --- Audit log -----------------------------------------------------------------------


def _fill_audit(conn, count: int) -> None:
    conn.execute("DELETE FROM audit_log")
    for number in range(count):
        audit.record(conn, None, "stock.adjusted", f"entry {number}")


def test_audit_shows_newest_first_with_readable_times_and_actions(open_screen, conn, manager):
    conn.execute("DELETE FROM audit_log")
    audit.record(conn, manager.id, "auth.sign_in", "manager")
    audit.record(conn, None, "auth.sign_in_failed", "unknown username")
    audit.record(conn, manager.id, "sale_voided", "Sale #1")
    screen = open_screen(manager, "audit")

    entries = screen.evaluate("root.entries.map(function(e) { return e.details }).join('|')")
    assert entries == "Sale #1|unknown username|manager"

    failed = screen.row_texts("unknown username")
    assert "Account: sign in failed" in failed
    assert "Unknown" in failed
    assert "Sale voided" in screen.row_texts("Sale #1")
    assert "manager" in screen.row_texts("Sale #1")
    assert any(re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text) for text in failed)
    assert not any("T" in text and "+00:00" in text for text in failed)


def test_audit_pages_through_older_and_newer(open_screen, conn, manager):
    _fill_audit(conn, 120)
    screen = open_screen(manager, "audit")
    assert "Showing entries 1 to 50" in screen.texts()
    assert "entry 119" in screen.texts()
    assert screen.is_enabled("Newer") is False
    assert screen.is_enabled("Older") is True

    screen.click("Older")
    assert "Showing entries 51 to 100" in screen.texts()
    assert "entry 69" in screen.texts()
    assert screen.is_enabled("Newer") is True

    screen.click("Older")
    assert "Showing entries 101 to 120" in screen.texts()
    assert screen.is_enabled("Older") is False

    screen.click("Newer")
    screen.click("Newer")
    assert "Showing entries 1 to 50" in screen.texts()


def test_audit_page_boundary_shows_no_empty_next_page(open_screen, conn, manager):
    _fill_audit(conn, 50)
    screen = open_screen(manager, "audit")
    assert "Showing entries 1 to 50" in screen.texts()
    assert screen.is_enabled("Older") is False


def test_audit_refresh_picks_up_new_entries(open_screen, conn, manager):
    _fill_audit(conn, 1)
    screen = open_screen(manager, "audit")
    audit.record(conn, manager.id, "stock.adjusted", "a brand new entry")
    assert "a brand new entry" not in screen.texts()
    screen.click("Refresh")
    assert "a brand new entry" in screen.texts()


def test_audit_empty_state(open_screen, conn, manager):
    _fill_audit(conn, 0)
    screen = open_screen(manager, "audit")
    assert "Nothing has been logged yet." in screen.texts()
    assert screen.is_enabled("Older") is False


@pytest.mark.parametrize("role, section", [("cashier", "audit"), ("stock_clerk", "staff"), ("cashier", "settings")])
def test_sections_stay_closed_to_other_roles(request, open_screen, api, role, section):
    open_screen(_actors(request, role), "menu")
    assert api.session.goTo(section)["ok"] is False


def test_leaving_a_screen_while_its_long_lists_are_filling_is_quiet(open_screen, conn, manager, pizza):
    """Qt warns if a list is destroyed while it builds off-screen rows in the background."""
    for number in range(60):
        menu.create_item(conn, manager, pizza["id"], f"Item {number}", 100)
        inventory.create_ingredient(conn, manager, f"Ingredient {number}", "kg", 100, 0)
    screen = open_screen(manager, "menu")
    for _ in range(8):
        for section in ("menu", "inventory", "audit"):
            assert screen.api.session.goTo(section)["ok"]
            QTest.qWait(40)
    screen.assert_clean()


@pytest.mark.parametrize(
    "section, button",
    [
        ("menu", "New category"),
        ("inventory", "New ingredient"),
        ("staff", "New staff member"),
    ],
)
def test_the_idle_lock_with_a_dialog_open_leaves_nothing_behind(open_screen, manager, section, button):
    screen = open_screen(manager, section)
    screen.click(button)
    assert "Cancel" in screen.button_texts()
    screen.api.session.end("You were signed out because the app was idle.")
    screen.settle()
    assert "Cancel" not in screen.button_texts()
    assert "Sign in" in screen.button_texts()
    screen.assert_clean()


@pytest.mark.parametrize(
    "section, expected",
    [
        ("audit", "The audit log could not be loaded."),
        ("menu", "The menu could not be loaded."),
        ("inventory", "The ingredients could not be loaded."),
        ("staff", "The staff list could not be loaded."),
    ],
)
def test_load_failures_show_the_error_state(open_screen, api, manager, section, expected):
    """If the data can't be read, each screen says so rather than looking empty."""
    screen = open_screen(manager, section)
    good = api.conn
    broken = sqlite3.connect(":memory:")
    broken.row_factory = sqlite3.Row
    broken.execute("CREATE TABLE settings (key TEXT, value TEXT)")
    broken.execute("INSERT INTO settings VALUES ('currency_symbol', '$')")
    api.conn = broken
    try:
        screen.evaluate("root.load(0)" if section == "audit" else "root.reload()")
        screen.settle()
        assert expected in screen.texts()
        assert screen.evaluate("banner.kind") == "error"
        assert screen.evaluate("banner.text") != ""
    finally:
        api.conn = good
        broken.close()
