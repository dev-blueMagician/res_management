"""Drive the home, sales and reports screens in a real window with real mouse clicks.

Each test loads Main.qml over demo data, clicks what staff would click, and checks
both what the screen shows and what reached the database. Any QML warning fails.
"""

from __future__ import annotations

import csv
import importlib.util
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QUICK_BACKEND", "software")
if os.environ.get("WINDIR"):
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ["WINDIR"]) / "Fonts"))

from PySide6.QtCore import QObject, QPointF, Qt, QtMsgType, QUrl, qInstallMessageHandler  # noqa: E402
from PySide6.QtQml import QQmlEngine, QQmlExpression  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from restaurant.bridge.api import MAIN_QML, Api, create_engine, destroy_engine  # noqa: E402
from restaurant.db.connection import connect  # noqa: E402
from restaurant.services import sales  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SETTLE_MS = 40
IGNORED_MESSAGES = ("propagateSizeHints", "QFontDatabase: Cannot find font directory")


def _load_demo_tool():
    spec = importlib.util.spec_from_file_location("screenshot_tool", ROOT / "tools" / "screenshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def qapp():
    QQuickStyle.setStyle("Basic")
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def template(qapp) -> Iterator[tuple[sqlite3.Connection, dict]]:
    """Demo data built once, since creating its accounts runs scrypt three times."""
    conn = connect(":memory:")
    actors = _load_demo_tool().load_demo_data(conn)
    yield conn, actors
    conn.close()


class Screen:
    """A real window showing one signed-in role, with helpers to find and click things."""

    def __init__(self, conn: sqlite3.Connection, actors: dict, backups: Path) -> None:
        self.conn = conn
        self.actors = actors
        self.api = Api(conn, ":memory:", backups)
        self.problems: list[str] = []
        self.engine = create_engine(self.api)
        self.engine.warnings.connect(
            lambda errors: self.problems.extend(error.toString() for error in errors)
        )
        self._previous_handler = qInstallMessageHandler(self._on_message)
        self.window = None

    def _on_message(self, kind, _context, message: str) -> None:
        if kind in (QtMsgType.QtDebugMsg, QtMsgType.QtInfoMsg):
            return
        if not any(text in message for text in IGNORED_MESSAGES):
            self.problems.append(message)

    def open(self, role: str, section: str = "home") -> Screen:
        self.api.session.start(self.actors[role])
        assert self.api.session.goTo(section)["ok"]
        self.engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
        assert self.engine.rootObjects(), "Main.qml failed to load"
        self.window = self.engine.rootObjects()[0]
        self.window.resize(1280, 800)
        QTest.qWaitForWindowExposed(self.window)
        QTest.qWait(SETTLE_MS * 2)
        return self

    def find(self, name: str) -> QObject | None:
        """A visible item by objectName, or any QML object of that name."""

        def walk(item):
            if item.objectName() == name and item.isVisible():
                return item
            for child in item.childItems():
                found = walk(child)
                if found is not None:
                    return found
            return None

        return walk(self.window.contentItem()) or self.window.findChild(QObject, name)

    def visible(self, name: str) -> bool:
        def walk(item):
            if item.objectName() == name and item.isVisible():
                return True
            return any(walk(child) for child in item.childItems())

        return walk(self.window.contentItem())

    def click(self, name: str) -> None:
        item = self.find(name)
        assert item is not None, f"{name} is not on screen"
        assert item.isVisible(), f"{name} is hidden"
        center = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
        QTest.qWait(SETTLE_MS)

    def text(self, name: str) -> str:
        item = self.find(name)
        assert item is not None, f"{name} is not on screen"
        return item.property("text")

    def evaluate(self, view: str, expression: str):
        target = self.find(view)
        context = QQmlEngine.contextForObject(target)
        qml = QQmlExpression(context, target, expression)
        value, _undefined = qml.evaluate()
        assert not qml.hasError(), qml.error().toString()
        return value

    def item_id(self, name: str) -> int:
        return self.conn.execute("SELECT id FROM menu_items WHERE name = ?", (name,)).fetchone()[0]

    def sale_count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0]

    def on_hand(self, ingredient: str) -> int:
        return self.conn.execute(
            "SELECT on_hand_milli FROM ingredients WHERE name = ?", (ingredient,)
        ).fetchone()[0]

    def close(self) -> None:
        self.api.session.end()
        QTest.qWait(SETTLE_MS)
        qInstallMessageHandler(self._previous_handler)
        destroy_engine(self.engine)
        QTest.qWait(SETTLE_MS)
        self.api.shutdown()


@pytest.fixture
def screen(template, tmp_path) -> Iterator[Screen]:
    source, actors = template
    conn = connect(":memory:")
    source.backup(conn)
    made = Screen(conn, actors, tmp_path / "backups")
    yield made
    made.close()
    assert made.problems == []


# --- Sales: building and saving a sale -------------------------------------------------


def _ring_up(screen: Screen, *names: str) -> None:
    for name in names:
        screen.click(f"salesTile-{name}")


def test_tapping_tiles_builds_a_sale_with_totals_from_the_bridge(screen):
    screen.open("cashier", "sales")
    _ring_up(screen, "Margherita", "Margherita", "Espresso")

    margherita = screen.item_id("Margherita")
    assert screen.text(f"cartQuantity-{margherita}") == "2"
    # 2 x 11.50 + 2.80 = 25.80; tax at 8.25% = 2.13 (2.1285 half up)
    cart = screen.evaluate("salesView", "cart")
    assert cart["subtotal_text"] == "$25.80"
    assert cart["tax_text"] == "$2.13"
    assert cart["total_text"] == "$27.93"
    assert screen.find("cartTotal").property("value") == "$27.93"


def test_plus_minus_and_remove_change_the_sale(screen):
    screen.open("cashier", "sales")
    _ring_up(screen, "Margherita", "Espresso")
    margherita = screen.item_id("Margherita")
    espresso = screen.item_id("Espresso")

    screen.click(f"cartPlus-{margherita}")
    assert screen.text(f"cartQuantity-{margherita}") == "2"
    screen.click(f"cartMinus-{margherita}")
    assert screen.text(f"cartQuantity-{margherita}") == "1"

    screen.click(f"cartMinus-{margherita}")  # at one, minus takes the line away
    assert screen.find(f"cartLine-{margherita}") is None
    screen.click(f"cartRemove-{espresso}")
    assert screen.evaluate("salesView", "cart.item_count") == 0


def test_category_tabs_filter_the_tiles(screen):
    screen.open("cashier", "sales")
    assert screen.visible("salesTile-Margherita")
    screen.click("salesTab-Drinks")
    assert screen.visible("salesTile-Espresso")
    assert not screen.visible("salesTile-Margherita")
    screen.click("salesTab-All")
    assert screen.visible("salesTile-Margherita")


def test_inactive_items_get_no_tile(screen):
    screen.open("cashier", "sales")
    assert not screen.visible("salesTile-Panna Cotta")  # deactivated in the demo data


def test_a_category_with_nothing_to_sell_gets_no_tab(screen):
    conn = screen.conn
    desserts = conn.execute("SELECT id FROM menu_categories WHERE name = 'Desserts'").fetchone()[0]
    screen.open("manager", "sales")
    assert screen.visible("salesTab-Desserts")
    assert screen.api.menu.setCategoryActive(desserts, False)["ok"]
    QTest.qWait(SETTLE_MS)
    assert not screen.visible("salesTab-Desserts")
    assert not screen.visible("salesTile-Tiramisu")


def test_confirm_needs_items_and_a_payment_method(screen):
    screen.open("cashier", "sales")
    confirm = screen.find("confirmSaleButton")
    assert confirm.property("enabled") is False

    _ring_up(screen, "Espresso")
    assert confirm.property("enabled") is False
    assert screen.text("confirmSaleButton") == "Choose how they paid"
    before = screen.sale_count()
    screen.click("confirmSaleButton")  # disabled, so nothing happens
    assert screen.sale_count() == before

    screen.click("payment-card")
    assert confirm.property("enabled") is True
    assert "$3.03" in screen.text("confirmSaleButton")


def test_saving_a_sale_records_it_confirms_the_total_and_starts_fresh(screen):
    screen.open("cashier", "sales")
    flour_before = screen.on_hand("Flour")
    count_before = screen.sale_count()

    _ring_up(screen, "Margherita", "Margherita")
    screen.click("payment-cash")
    screen.click("confirmSaleButton")

    assert screen.sale_count() == count_before + 1
    sale = screen.conn.execute("SELECT * FROM sales ORDER BY id DESC LIMIT 1").fetchone()
    assert sale["payment_method"] == "cash"
    assert sale["staff_id"] == screen.actors["cashier"].id
    assert sale["total_cents"] == 2490  # 23.00 + 8.25% tax (1.90 after rounding)
    assert screen.on_hand("Flour") == flour_before - 500  # two at 0.25 kg each

    assert screen.visible("saleConfirmation")
    assert screen.text("saleConfirmationTotal") == "$24.90"
    assert screen.evaluate("salesView", "cart.item_count") == 0
    # The next sale must choose its payment method again.
    assert screen.find("confirmSaleButton").property("enabled") is False
    _ring_up(screen, "Espresso")
    assert not screen.visible("saleConfirmation")
    assert screen.find("confirmSaleButton").property("enabled") is False


def test_clearing_a_sale_asks_first(screen):
    screen.open("cashier", "sales")
    _ring_up(screen, "Espresso")
    screen.click("clearSaleButton")
    QTest.qWait(SETTLE_MS)
    dialog = screen.find("clearSaleDialog")
    assert dialog.property("opened") is True
    assert screen.evaluate("salesView", "cart.item_count") == 1  # nothing lost yet
    dialog.close()
    QTest.qWait(SETTLE_MS)
    assert screen.evaluate("salesView", "cart.item_count") == 1

    dialog.setProperty("visible", True)
    QTest.qWait(SETTLE_MS)
    dialog.submitted.emit()
    QTest.qWait(SETTLE_MS)
    assert screen.evaluate("salesView", "cart.item_count") == 0
    assert dialog.property("opened") is False


def test_an_item_deactivated_mid_sale_is_shown_and_checkout_names_it(screen):
    screen.open("manager", "sales")
    espresso = screen.item_id("Espresso")
    _ring_up(screen, "Espresso")
    screen.click("payment-card")
    before = screen.sale_count()

    assert screen.api.menu.setItemActive(espresso, False)["ok"]
    QTest.qWait(SETTLE_MS)
    assert screen.evaluate("salesView", "cart.lines[0].available") in (False, 0)
    assert not screen.visible("salesTile-Espresso")

    screen.click("confirmSaleButton")
    assert screen.sale_count() == before  # refused, and the sale is kept for fixing
    banner = screen.find("salesBanner")
    assert banner.property("kind") == "error"
    assert "Espresso" in banner.property("text")
    assert screen.evaluate("salesView", "cart.item_count") == 1


def test_a_refused_tile_tap_shows_in_the_banner(screen):
    screen.open("cashier", "sales")
    espresso = screen.item_id("Espresso")
    screen.conn.execute("UPDATE menu_items SET active = 0 WHERE id = ?", (espresso,))
    screen.click("salesTile-Espresso")
    banner = screen.find("salesBanner")
    assert "Espresso" in banner.property("text")
    assert not screen.visible("salesTile-Espresso")  # the stale tile went away


def test_empty_menu_says_so(screen):
    screen.conn.execute("UPDATE menu_items SET active = 0")
    screen.open("cashier", "sales")
    assert not screen.visible("salesTile-Margherita")
    assert screen.visible("salesMenuEmpty")


def test_cart_survives_going_to_another_screen_and_back(screen):
    screen.open("cashier", "sales")
    _ring_up(screen, "Espresso")
    assert screen.api.session.goTo("home")["ok"]
    QTest.qWait(SETTLE_MS * 2)
    assert screen.find("homeCurrentSale").property("value") == "$3.03"
    screen.click("homeStartSale")
    QTest.qWait(SETTLE_MS)
    assert screen.evaluate("salesView", "cart.item_count") == 1


def test_idle_lock_clears_the_cart_with_dialogs_open(template, tmp_path):
    source, actors = template
    conn = connect(":memory:")
    source.backup(conn)
    screen = Screen(conn, actors, tmp_path / "backups")
    try:
        screen.open("manager", "sales")
        _ring_up(screen, "Espresso")
        screen.click("clearSaleButton")
        QTest.qWait(SETTLE_MS)
        screen.api.session._on_idle()
        QTest.qWait(SETTLE_MS * 2)
        assert screen.api.session.signedIn is False
        screen.api.session.start(actors["manager"])
        QTest.qWait(SETTLE_MS * 2)
        assert screen.api.sales.cart()["data"]["item_count"] == 0
        assert screen.api.session.goTo("sales")["ok"]
        QTest.qWait(SETTLE_MS * 2)
        assert screen.evaluate("salesView", "cart.item_count") == 0
    finally:
        screen.close()
    assert screen.problems == []


# --- Sales: recent sales and voids -----------------------------------------------------


def test_cashier_sees_recent_sales_but_no_void_control(screen):
    screen.open("cashier", "sales")
    screen.click("salesPageRecent")
    assert screen.visible("saleRow-1")
    screen.click("saleRow-1")
    assert screen.text("saleDetailTotal") == "$30.31"
    assert not screen.visible("voidSaleButton")


def test_recent_sales_only_lists_the_cashiers_own(screen):
    screen.conn.execute(
        "UPDATE sales SET staff_id = ? WHERE id = 1", (screen.actors["manager"].id,)
    )
    screen.open("cashier", "sales")
    screen.click("salesPageRecent")
    assert not screen.visible("saleRow-1")
    assert screen.visible("saleRow-2")


def _open_void_dialog(screen: Screen, sale_id: int) -> QObject:
    screen.click("salesPageRecent")
    screen.click(f"saleRow-{sale_id}")
    screen.click("voidSaleButton")
    QTest.qWait(SETTLE_MS)
    dialog = screen.find("voidSaleDialog")
    assert dialog.property("opened") is True
    return dialog


def test_manager_voids_a_sale_with_a_reason_and_stock_returns(screen):
    screen.open("manager", "sales")
    before = screen.on_hand("Flour")
    dialog = _open_void_dialog(screen, 5)  # 7 Margherita at 0.25 kg flour
    assert dialog.property("confirmEnabled") is False  # no reason yet

    dialog.confirmed.emit("Customer changed their mind")
    QTest.qWait(SETTLE_MS)

    row = screen.conn.execute("SELECT * FROM sales WHERE id = 5").fetchone()
    assert row["voided_at"] is not None
    assert row["void_reason"] == "Customer changed their mind"
    assert row["voided_by"] == screen.actors["manager"].id
    assert screen.on_hand("Flour") == before + 1750

    banner = screen.find("salesBanner")
    assert banner.property("kind") == "success"
    assert "#5" in banner.property("text")
    assert screen.visible("voidDetails")
    assert "Customer changed their mind" in screen.find("voidDetails").property("text")
    assert not screen.visible("voidSaleButton")  # can't be voided twice from the screen


def test_cancelling_the_void_dialog_changes_nothing(screen):
    screen.open("manager", "sales")
    dialog = _open_void_dialog(screen, 5)
    dialog.close()
    QTest.qWait(SETTLE_MS)
    row = screen.conn.execute("SELECT voided_at FROM sales WHERE id = 5").fetchone()
    assert row["voided_at"] is None
    assert screen.visible("voidSaleButton")


def test_voiding_an_already_voided_sale_shows_the_refusal(screen):
    screen.open("manager", "sales")
    dialog = _open_void_dialog(screen, 5)
    # Voided behind the screen's back, so the button was still there.
    sales.void_sale(screen.conn, screen.actors["manager"], 5, "Someone else got there")
    dialog.confirmed.emit("Too late")
    QTest.qWait(SETTLE_MS)
    banner = screen.find("salesBanner")
    assert banner.property("kind") == "error"
    assert "already been voided" in banner.property("text")
    assert not screen.visible("voidSaleButton")


def test_voided_sale_details_show_who_and_why(screen):
    screen.open("manager", "sales")
    screen.click("salesPageRecent")
    screen.click("saleRow-6")  # voided in the demo data
    details = screen.find("voidDetails").property("text")
    assert "Maria Lopez" in details
    assert "Entered the wrong item" in details
    assert not screen.visible("voidSaleButton")


def test_recent_sales_pages_through_older_sales(screen):
    now = "2026-11-05T00:00:00+00:00"
    staff_id = screen.actors["manager"].id
    screen.conn.executemany(
        "INSERT INTO sales (staff_id, payment_method, subtotal_cents, tax_rate_bp, tax_cents, "
        "total_cents, created_at) VALUES (?, 'cash', 100, 0, 0, 100, ?)",
        [(staff_id, now)] * 60,
    )
    screen.open("manager", "sales")
    screen.click("salesPageRecent")
    panel_sales = QQmlExpression(
        QQmlEngine.contextForObject(screen.find("salesRecentPanel")),
        screen.find("salesRecentPanel"),
        "sales.length",
    )
    assert panel_sales.evaluate()[0] == 50
    screen.find("salesRecentPanel").loadMore()
    QTest.qWait(SETTLE_MS)
    assert panel_sales.evaluate()[0] == 66
    assert screen.find("salesRecentPanel").property("hasMore") is False


# --- Home ------------------------------------------------------------------------------


def test_manager_home_shows_todays_sales_and_stock(screen):
    screen.open("manager", "home")
    assert screen.find("homeSalesToday").property("value") == "$195.33"
    assert screen.find("homeSalesToday").property("note") == "5 sales"
    assert screen.find("homeVoidsToday").property("value") == "1"
    assert screen.find("homeLowStock").property("value") == "2"
    assert screen.find("homeStockValue").property("value") == "$214.03"
    assert screen.visible("homeTopItems")
    assert screen.visible("homeLowItems")


def test_top_sellers_leaves_out_items_whose_only_sales_were_voided(screen):
    # Void every sale except the seven Margheritas, leaving one real top seller.
    live = screen.conn.execute("SELECT id FROM sales WHERE voided_at IS NULL").fetchall()
    keep = screen.conn.execute(
        "SELECT sale_id FROM sale_items WHERE name_as_sold = 'Margherita' AND quantity = 7"
    ).fetchone()[0]
    for row in live:
        if row[0] != keep:
            sales.void_sale(screen.conn, screen.actors["manager"], row[0], "Testing")
    screen.open("manager", "home")
    assert screen.evaluate("homeTopItems", "rows.length") == 1
    assert screen.evaluate("homeTopItems", "rows[0].name") == "Margherita"


def test_cashier_home_has_no_sales_figures_and_no_stock_link(screen):
    screen.open("cashier", "home")
    for hidden in ("homeSalesToday", "homeVoidsToday", "homeTopItems", "homeStockValue", "homeLowItems"):
        assert not screen.visible(hidden), hidden
    assert screen.find("homeLowStockCount").property("value") == "2"
    assert screen.find("homeLowStockCount").property("clickable") is False
    assert screen.find("homeCurrentSale").property("value") == "Empty"


def test_stock_clerk_home_has_no_sales_figures(screen):
    screen.open("stock_clerk", "home")
    for hidden in ("homeSalesToday", "homeVoidsToday", "homeTopItems", "homeNewSale", "homeStartSale"):
        assert not screen.visible(hidden), hidden
    assert screen.find("homeIngredients").property("value") == "6"
    assert screen.find("homeBelowZero").property("value") == "0"


def test_low_stock_tile_opens_the_low_stock_report(screen):
    screen.open("stock_clerk", "home")
    screen.click("homeLowStock")
    QTest.qWait(SETTLE_MS * 2)
    assert screen.api.session.currentSection == "reports"
    assert screen.evaluate("reportsView", "report.name") == "low_stock"
    assert screen.evaluate("reportsView", "report.rows.length") == 2


def test_home_quick_links_navigate(screen):
    screen.open("manager", "home")
    screen.click("homeLink-inventory")
    QTest.qWait(SETTLE_MS * 2)
    assert screen.api.session.currentSection == "inventory"


def test_manager_new_sale_button_goes_to_the_till(screen):
    screen.open("manager", "home")
    screen.click("homeNewSale")
    QTest.qWait(SETTLE_MS * 2)
    assert screen.api.session.currentSection == "sales"


def test_negative_stock_is_counted_on_home(screen):
    screen.conn.execute("UPDATE ingredients SET on_hand_milli = -500 WHERE name = 'Basil'")
    screen.open("stock_clerk", "home")
    tile = screen.find("homeBelowZero")
    assert tile.property("value") == "1"
    assert tile.property("tone") == "danger"


def test_home_reloads_when_stock_changes(screen):
    screen.open("manager", "home")
    flour = screen.conn.execute("SELECT id FROM ingredients WHERE name = 'Flour'").fetchone()[0]
    assert screen.api.inventory.recordWaste(flour, "37", "Spilled")["ok"]
    QTest.qWait(SETTLE_MS)
    assert screen.find("homeLowStock").property("value") == "3"


def test_home_with_no_sales_today_shows_zeros(screen):
    screen.conn.execute("UPDATE sales SET created_at = '2020-01-01T00:00:00+00:00'")
    screen.open("manager", "home")
    assert screen.find("homeSalesToday").property("value") == "$0.00"
    assert screen.find("homeSalesToday").property("note") == "0 sales"
    assert screen.evaluate("homeView", "topItems.length") == 0


# --- Reports ---------------------------------------------------------------------------


MANAGER_TABS = {
    "sales_summary",
    "sales_by_item",
    "sales_by_category",
    "sales_by_payment_method",
    "sales_by_cashier",
    "stock_levels",
    "stock_value",
    "low_stock",
    "movement_history",
}
STOCK_TABS = {"stock_levels", "stock_value", "low_stock", "movement_history"}


@pytest.mark.parametrize(
    ("role", "tabs", "first"),
    [("manager", MANAGER_TABS, "sales_summary"), ("stock_clerk", STOCK_TABS, "stock_levels")],
)
def test_reports_shows_only_the_tabs_the_role_may_see(screen, role, tabs, first):
    screen.open(role, "reports")
    for name in MANAGER_TABS:
        assert screen.visible(f"reportTab-{name}") == (name in tabs), name
    assert screen.evaluate("reportsView", "report.name") == first


def test_cashier_cannot_reach_reports(screen):
    screen.api.session.start(screen.actors["cashier"])
    assert screen.api.session.goTo("reports")["ok"] is False


@pytest.mark.parametrize("name", sorted(MANAGER_TABS))
def test_every_tab_runs_its_report(screen, name):
    screen.open("manager", "reports")
    screen.click(f"reportTab-{name}")
    assert screen.evaluate("reportsView", "report.name") == name
    assert screen.find("reportsBanner").property("text") == ""
    if name == "sales_summary":
        assert not screen.visible("reportTable")
    else:
        assert screen.visible("reportTable")


def test_date_row_only_shows_for_dated_reports(screen):
    screen.open("manager", "reports")
    assert screen.visible("presetToday")
    screen.click("reportTab-stock_levels")
    assert not screen.visible("presetToday")
    screen.click("reportTab-movement_history")
    assert screen.visible("presetToday")


def _set_range(screen: Screen, start: str, end: str) -> None:
    screen.find("reportFrom").setProperty("text", start)
    screen.find("reportTo").setProperty("text", end)
    screen.click("reportApplyDates")


def test_presets_set_the_date_range(screen):
    screen.open("manager", "reports")
    today = screen.api.reports.localDate(0)["data"]
    week_start = screen.api.reports.localDate(-6)["data"]

    assert screen.evaluate("reportsView", "preset") == "today"
    assert screen.evaluate("reportsView", "reportParams.start_date") == today

    screen.click("presetWeek")
    assert screen.evaluate("reportsView", "preset") == "week"
    assert screen.evaluate("reportsView", "reportParams.start_date") == week_start
    assert screen.evaluate("reportsView", "reportParams.end_date") == today

    screen.click("presetMonth")
    assert screen.evaluate("reportsView", "reportParams.start_date") == today[:8] + "01"
    assert screen.evaluate("reportsView", "reportParams.end_date") == today

    screen.click("presetToday")
    assert screen.evaluate("reportsView", "reportParams.start_date") == today


def test_typing_dates_deselects_the_preset_and_applies_on_show(screen):
    screen.open("manager", "reports")
    _set_range(screen, "2020-01-01", "2020-01-31")
    assert screen.evaluate("reportsView", "preset") == ""
    assert screen.evaluate("reportsView", "report.summary.sale_count") == 0
    assert screen.visible("summaryEmpty")


@pytest.mark.parametrize(
    ("start", "end"),
    [("not a date", "2026-01-01"), ("2026-02-01", "2026-01-01"), ("", "")],
)
def test_bad_dates_show_in_the_banner_and_block_export(screen, start, end):
    screen.open("manager", "reports")
    _set_range(screen, start, end)
    banner = screen.find("reportsBanner")
    assert banner.property("kind") == "error"
    assert banner.property("text") != ""
    assert screen.find("exportCsvButton").property("enabled") is False
    assert screen.visible("reportTable")  # says it could not load, rather than old figures


def test_recovering_from_bad_dates_clears_the_error(screen):
    screen.open("manager", "reports")
    _set_range(screen, "nope", "nope")
    screen.click("presetToday")
    assert screen.find("reportsBanner").property("text") == ""
    assert screen.find("exportCsvButton").property("enabled") is True


def test_voided_sales_are_excluded_and_counted_on_the_summary(screen):
    screen.open("manager", "reports")
    assert screen.evaluate("reportsView", "report.summary.total_text") == "$195.33"
    assert screen.evaluate("reportsView", "report.summary.void_count") == 1


def test_export_writes_what_is_on_screen(screen, tmp_path):
    screen.open("manager", "reports")
    screen.click("reportTab-sales_by_payment_method")
    path = tmp_path / "out.csv"
    url = QUrl.fromLocalFile(str(path)).toString()
    assert screen.evaluate("reportsView", f'exportTo("{url}")') is True

    with open(path, encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    assert rows[0][0] == "Payment method"
    assert {row[0] for row in rows[1:]} == {"card", "cash", "other"}
    banner = screen.find("reportsBanner")
    assert banner.property("kind") == "success"
    assert "3 rows" in banner.property("text")
    assert str(path) in banner.property("text")  # with Windows separators, as staff expect


def test_export_uses_the_range_shown_not_a_half_typed_one(screen, tmp_path):
    screen.open("manager", "reports")
    screen.click("reportTab-sales_by_item")
    screen.find("reportFrom").setProperty("text", "2020-01-01")  # typed, not applied
    screen.find("reportTo").setProperty("text", "2020-01-01")
    path = tmp_path / "shown.csv"
    url = QUrl.fromLocalFile(str(path)).toString()
    assert screen.evaluate("reportsView", f'exportTo("{url}")') is True
    with open(path, encoding="utf-8-sig", newline="") as file:
        assert len(list(csv.reader(file))) > 1


def test_export_failure_shows_in_the_banner(screen, tmp_path):
    screen.open("manager", "reports")
    url = QUrl.fromLocalFile(str(tmp_path / "missing" / "folder" / "x.csv")).toString()
    assert screen.evaluate("reportsView", f'exportTo("{url}")') is False
    banner = screen.find("reportsBanner")
    assert banner.property("kind") == "error"
    assert "Could not write" in banner.property("text")


def test_export_button_opens_the_file_dialog_with_a_sensible_name(screen):
    screen.open("stock_clerk", "reports")
    screen.click("reportTab-low_stock")
    screen.click("exportCsvButton")
    QTest.qWait(SETTLE_MS)
    dialog = screen.find("reportSaveDialog")
    assert dialog is not None
    assert dialog.property("selectedFile").toString().endswith("/low_stock.csv")
    assert dialog.property("visible") is True
    dialog.close()
    QTest.qWait(SETTLE_MS)
    assert screen.find("reportsBanner").property("text") == ""


def test_low_stock_flags_negative_levels(screen):
    screen.conn.execute("UPDATE ingredients SET on_hand_milli = -500 WHERE name = 'Basil'")
    screen.open("stock_clerk", "reports")
    screen.click("reportTab-low_stock")
    rows = screen.evaluate("reportsView", "report.rows")
    basil = next(row for row in rows if row["name"] == "Basil")
    assert basil["status"] == "Below zero"


def test_empty_reports_say_so(screen):
    screen.conn.execute("UPDATE ingredients SET low_milli = 0")
    screen.open("stock_clerk", "reports")
    screen.click("reportTab-low_stock")
    assert screen.evaluate("reportsView", "report.rows.length") == 0
    assert screen.find("reportTable").property("emptyText") == "Nothing is low on stock."
    screen.click("reportTab-movement_history")
    _set_range(screen, "2020-01-01", "2020-01-31")
    assert screen.evaluate("reportsView", "report.rows.length") == 0
    assert "No stock movements" in screen.find("reportTable").property("emptyText")


def test_stock_value_shows_its_total(screen):
    screen.open("stock_clerk", "reports")
    screen.click("reportTab-stock_value")
    assert screen.find("stockValueTotal").property("value") == "$214.03"
