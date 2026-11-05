"""The bridge without any screens: result shape, session, idle lock, conversions, startup."""

from __future__ import annotations

import csv
import logging
import os
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QLockFile, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from restaurant.bridge.api import Api
from restaurant.bridge.base import (
    GENERIC_ERROR,
    BridgeObject,
    api_slot,
    configure_logging,
    local_time,
    to_qml,
)
from restaurant.bridge.reports import REPORTS
from restaurant.core import Actor, AuthenticationError, ConflictError, Permission, Role
from restaurant.db.connection import connect
from restaurant.services import auth, inventory, menu, staff

PASSWORD = "correct-horse"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Slots that are meant to work with nobody signed in.
OPEN_SLOTS = {
    ("session", "signIn"),
    ("session", "createFirstManager"),
    ("session", "signOut"),
    ("settings", "get"),
    ("staff", "roles"),
    ("sales", "paymentMethods"),
    ("reports", "localDate"),
}
AREAS = (
    "session",
    "staff",
    "settings",
    "backup",
    "menu",
    "inventory",
    "sales",
    "reports",
    "audit",
)


@pytest.fixture(scope="session")
def qapp() -> QCoreApplication:
    QQuickStyle.setStyle("Basic")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def api(qapp, conn, tmp_path) -> Iterator[Api]:
    bridge = Api(conn, ":memory:", tmp_path / "backups")
    yield bridge
    bridge.session.shutdown()


@pytest.fixture
def file_api(qapp, tmp_path) -> Iterator[Api]:
    """A bridge over a real database file, needed for backup and restore."""
    db_path = tmp_path / "restaurant.db"
    bridge = Api(connect(db_path), db_path, tmp_path / "backups")
    yield bridge
    bridge.shutdown()


@pytest.fixture
def log_file(tmp_path) -> Iterator[Path]:
    handler = configure_logging(tmp_path)
    yield tmp_path / "restaurant.log"
    logging.getLogger("restaurant").removeHandler(handler)
    handler.close()


@pytest.fixture
def category(conn, manager) -> dict:
    return menu.create_category(conn, manager, "Mains")


@pytest.fixture
def item(conn, manager, category) -> dict:
    return menu.create_item(conn, manager, category["id"], "Burger", 1250)


@pytest.fixture
def flour(conn, manager) -> dict:
    return inventory.create_ingredient(conn, manager, "Flour", "kg", 200, 1000)


def _data(result: dict):
    assert result["ok"] is True, result
    return result["data"]


def _error(result: dict) -> str:
    assert result["ok"] is False, result
    assert set(result) == {"ok", "error"}
    return result["error"]


def _dummy_args(meta_method) -> list:
    dummies = {
        "int": 999,
        "bool": True,
        "QString": "x",
        "QVariantMap": {},
        "QVariantList": [],
        "QVariant": 1,
    }
    return [dummies[bytes(kind).decode()] for kind in meta_method.parameterTypes()]


def _slots(bridge: QObject) -> list[tuple[str, list]]:
    """Every public slot. Private ones are Python methods connected to internal signals.

    Starts from QObject's methods, not methodOffset(): connecting a Python method to a
    signal gives the object a dynamic meta-object whose offset skips the class's slots.
    """
    meta = bridge.metaObject()
    found = []
    for index in range(QObject.staticMetaObject.methodCount(), meta.methodCount()):
        method = meta.method(index)
        name = bytes(method.name()).decode()
        if method.methodType() == method.MethodType.Slot and not name.startswith("_"):
            found.append((name, _dummy_args(method)))
    return found


# --- Result shape -------------------------------------------------------------------


class _Probe(BridgeObject):
    @api_slot()
    def fine(self) -> dict:
        return {"value": 1}

    @api_slot()
    def expected(self) -> None:
        raise ConflictError("Staff can read this.")

    @api_slot(str)
    def unexpected(self, password: str) -> None:
        raise RuntimeError(f"secret detail {password}")

    @api_slot()
    def bad_result(self) -> object:
        return {"price_cents": 1, "nested": [object]} | {1 / 1: "x"} | {"a": 1 / 0}


def test_success_is_ok_with_data(api):
    assert _Probe(api).fine() == {"ok": True, "data": {"value": 1}}


def test_app_error_becomes_its_message(api, log_file):
    assert _Probe(api).expected() == {"ok": False, "error": "Staff can read this."}
    assert not log_file.exists() or "Staff can read" not in log_file.read_text()


def test_unexpected_error_is_logged_with_traceback_and_hidden_from_staff(api, log_file):
    result = _Probe(api).unexpected("hunter2-password")
    assert result == {"ok": False, "error": GENERIC_ERROR}
    logged = log_file.read_text(encoding="utf-8")
    assert "Traceback (most recent call last)" in logged
    assert "_Probe.unexpected" in logged
    assert "RuntimeError" in logged


def test_error_while_building_the_result_is_contained(api, log_file):
    assert _Probe(api).bad_result() == {"ok": False, "error": GENERIC_ERROR}
    assert "ZeroDivisionError" in log_file.read_text(encoding="utf-8")


def test_database_failure_is_contained(api, log_file):
    probe = _Probe(api)
    api.conn = sqlite3.connect(":memory:")  # no tables: every settings read fails
    try:
        assert probe.fine() == {"ok": False, "error": GENERIC_ERROR}
    finally:
        api.conn.close()


def test_every_slot_returns_the_result_shape_for_every_role_on_junk_input(
    file_api, log_file, manager_cashier_clerk
):
    """Junk input must give a staff-facing error, never the generic unexpected one."""
    for actor in manager_cashier_clerk:
        for area in AREAS:
            bridge = getattr(file_api, area)
            for name, args in _slots(bridge):
                if (area, name) == ("session", "signOut"):
                    continue
                file_api.session.start(actor)
                result = getattr(bridge, name)(*args)
                assert isinstance(result, dict) and "ok" in result, (area, name)
                if not result["ok"]:
                    assert result["error"] != GENERIC_ERROR, (actor.role, area, name)
    assert not log_file.exists() or log_file.read_text(encoding="utf-8") == ""


@pytest.fixture
def manager_cashier_clerk(file_api) -> list[Actor]:
    conn = file_api.conn
    record = auth.create_first_manager(conn, "maria", "Maria", PASSWORD)
    manager = Actor(record["id"], "maria", "Maria", Role.MANAGER)
    cashier = staff.create_staff(conn, manager, "carl", "Carl", "cashier", PASSWORD)
    clerk = staff.create_staff(conn, manager, "sam", "Sam", "stock_clerk", PASSWORD)
    return [
        manager,
        Actor(cashier["id"], "carl", "Carl", Role.CASHIER),
        Actor(clerk["id"], "sam", "Sam", Role.STOCK_CLERK),
    ]


# --- No-session guard ---------------------------------------------------------------


def test_every_slot_is_refused_with_nobody_signed_in(api):
    checked = 0
    for area in AREAS:
        bridge = getattr(api, area)
        for name, args in _slots(bridge):
            if (area, name) in OPEN_SLOTS:
                continue
            assert _error(getattr(bridge, name)(*args)) == "Please sign in first.", (area, name)
            checked += 1
    assert checked >= 48  # the slots really were found


@pytest.mark.parametrize("area", AREAS)
def test_every_decorated_method_is_a_qt_slot_and_the_reverse(api, area):
    bridge = getattr(api, area)
    decorated = {
        name for name, member in vars(type(bridge)).items() if hasattr(member, "__wrapped__")
    }
    assert {name for name, _ in _slots(bridge)} == decorated


def test_open_slots_work_before_sign_in(api):
    assert _data(api.settings.get())["restaurant_name"] == "My Restaurant"
    assert [role["value"] for role in _data(api.staff.roles())] == [
        "manager",
        "cashier",
        "stock_clerk",
    ]
    assert [m["value"] for m in _data(api.sales.paymentMethods())] == ["cash", "card", "other"]


def test_guard_applies_again_after_sign_out(api, manager):
    api.session.start(manager)
    assert api.menu.listCategories(True)["ok"]
    api.session.signOut()
    assert _error(api.menu.listCategories(True)) == "Please sign in first."


# --- First run and sign-in ----------------------------------------------------------


def test_first_run_flow(api, conn):
    assert api.session.needsFirstRun is True
    assert api.session.signedIn is False
    changed = QSignalSpy(api.session.changed)

    user = _data(api.session.createFirstManager("maria", "Maria Lopez", PASSWORD, PASSWORD))

    assert user["role"] == "manager"
    assert user["role_text"] == "Manager"
    assert api.session.needsFirstRun is False
    assert api.session.signedIn is True
    assert api.session.user["full_name"] == "Maria Lopez"
    assert changed.count() == 0  # queued, so the calling QML handler can finish
    QTest.qWait(0)
    assert changed.count() == 1
    assert auth.authenticate(conn, "maria", PASSWORD).role is Role.MANAGER


def test_first_run_refused_once_an_account_exists(api, manager):
    result = api.session.createFirstManager("second", "Second", PASSWORD, PASSWORD)
    assert "already" in _error(result)
    assert api.session.signedIn is False


@pytest.mark.parametrize(
    ("username", "name", "password", "confirm", "message"),
    [
        ("maria", "Maria", PASSWORD, "different-pass", "match"),
        ("maria", "Maria", "short", "short", "at least 8"),
        ("", "Maria", PASSWORD, PASSWORD, "Username"),
        ("maria", "", PASSWORD, PASSWORD, "Full name"),
    ],
)
def test_first_run_rejects_bad_input_without_creating_anything(
    api, conn, username, name, password, confirm, message
):
    assert message in _error(api.session.createFirstManager(username, name, password, confirm))
    assert api.session.needsFirstRun is True
    assert api.session.signedIn is False
    assert conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0] == 0


def test_sign_in_flow(api, conn):
    auth.create_first_manager(conn, "maria", "Maria", PASSWORD)
    user = _data(api.session.signIn("maria", PASSWORD))
    assert user["username"] == "maria"
    assert api.session.signedIn is True
    assert api.session.permissions["manage_staff"] is True
    actions = [row[0] for row in conn.execute("SELECT action FROM audit_log")]
    assert "auth.sign_in" in actions


def test_failed_sign_in_says_the_same_thing_for_any_cause(api, conn):
    auth.create_first_manager(conn, "maria", "Maria", PASSWORD)
    wrong_password = _error(api.session.signIn("maria", "wrong-password"))
    unknown_user = _error(api.session.signIn("nobody", PASSWORD))
    assert wrong_password == unknown_user == "Incorrect username or password."
    assert api.session.signedIn is False
    failures = conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'auth.sign_in_failed'"
    ).fetchone()[0]
    assert failures == 2


@pytest.mark.parametrize(("username", "password"), [("", "x"), ("   ", "x"), ("maria", "")])
def test_blank_sign_in_is_rejected_without_an_audit_entry(api, conn, username, password):
    assert "Enter your username" in _error(api.session.signIn(username, password))
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_deactivated_staff_cannot_sign_in(api, conn, manager):
    record = staff.create_staff(conn, manager, "carl", "Carl", "cashier", PASSWORD)
    staff.deactivate_staff(conn, manager, record["id"])
    assert _error(api.session.signIn("carl", PASSWORD)) == "Incorrect username or password."


def test_passwords_never_reach_the_audit_log_or_results(api, conn):
    result = api.session.createFirstManager("maria", "Maria", PASSWORD, PASSWORD)
    api.session.signOut()
    api.session.signIn("maria", "another-password")
    details = " ".join(row[0] for row in conn.execute("SELECT details FROM audit_log"))
    assert PASSWORD not in details and "another-password" not in details
    assert "password_hash" not in result["data"]


def test_change_password(api, conn):
    api.session.createFirstManager("maria", "Maria", PASSWORD, PASSWORD)
    assert "match" in _error(api.session.changePassword(PASSWORD, "new-password", "other-password"))
    assert "incorrect" in _error(
        api.session.changePassword("wrong-current", "new-password", "new-password")
    )
    assert api.session.changePassword(PASSWORD, "new-password", "new-password")["ok"]
    assert auth.authenticate(conn, "maria", "new-password")
    with pytest.raises(AuthenticationError):
        auth.authenticate(conn, "maria", PASSWORD)


# --- Permissions and navigation -----------------------------------------------------


@pytest.mark.parametrize(
    ("fixture", "sections"),
    [
        ("manager", ["home", "sales", "menu", "inventory", "reports", "staff", "settings", "audit"]),
        ("cashier", ["home", "sales", "menu"]),
        ("stock_clerk", ["home", "menu", "inventory", "reports"]),
    ],
)
def test_navigation_lists_only_what_the_role_may_use(api, request, fixture, sections):
    assert api.session.sections == []
    actor = request.getfixturevalue(fixture)
    api.session.start(actor)
    assert [section["key"] for section in api.session.sections] == sections
    assert api.session.currentSection == "home"
    assert api.session.currentView == "HomeView.qml"
    for key in sections:
        assert _data(api.session.goTo(key)) == key
    assert api.session.currentSection == sections[-1]


@pytest.mark.parametrize(
    ("fixture", "section"),
    [("cashier", "staff"), ("cashier", "reports"), ("stock_clerk", "sales"), ("stock_clerk", "audit")],
)
def test_navigation_refuses_a_section_the_role_may_not_use(api, request, fixture, section):
    api.session.start(request.getfixturevalue(fixture))
    assert "permission" in _error(api.session.goTo(section))
    assert api.session.currentSection == "home"


def test_permissions_map_matches_the_role(api, cashier):
    assert all(value is False for value in api.session.permissions.values())
    assert set(api.session.permissions) == {p.value for p in Permission}
    api.session.start(cashier)
    allowed = {key for key, value in api.session.permissions.items() if value}
    assert allowed == {"record_sales", "view_menu"}


def test_signing_in_starts_on_home_even_after_someone_else_navigated(api, manager, cashier):
    api.session.start(manager)
    api.session.goTo("staff")
    api.session.signOut()
    assert api.session.currentSection == ""
    assert api.session.currentView == ""
    api.session.start(cashier)
    assert api.session.currentSection == "home"


@pytest.mark.parametrize(
    ("area", "slot", "args"),
    [
        ("staff", "listStaff", ()),
        ("settings", "update", ({"restaurant_name": "X"},)),
        ("backup", "create", ()),
        ("audit", "listEntries", (10, 0)),
        ("menu", "createCategory", ("Mains", 0)),
        ("inventory", "createIngredient", ("Flour", "kg", "1", "1")),
        ("inventory", "listIngredients", (True,)),
        ("sales", "voidSale", (1, "reason")),
        ("reports", "run", ("stock_levels", {})),
    ],
)
def test_cashier_is_refused_by_the_service(api, cashier, area, slot, args):
    api.session.start(cashier)
    assert "permission" in _error(getattr(getattr(api, area), slot)(*args))


@pytest.mark.parametrize(
    ("area", "slot", "args"),
    [
        ("staff", "listStaff", ()),
        ("settings", "update", ({"restaurant_name": "X"},)),
        ("menu", "createCategory", ("Mains", 0)),
        ("sales", "cart", ()),
        ("sales", "cartAdd", (1,)),
        ("sales", "checkout", ("cash",)),
        ("reports", "run", ("sales_summary", {"start_date": "2026-01-01", "end_date": "2026-01-02"})),
    ],
)
def test_stock_clerk_is_refused_by_the_service(api, stock_clerk, area, slot, args):
    api.session.start(stock_clerk)
    assert "permission" in _error(getattr(getattr(api, area), slot)(*args))


# --- Changes to one's own account ---------------------------------------------------


def _second_manager(conn, manager) -> dict:
    return staff.create_staff(conn, manager, "mo", "Mo", "manager", PASSWORD)


def test_demoting_yourself_signs_you_out(api, conn, manager):
    _second_manager(conn, manager)
    api.session.start(manager)
    assert _data(api.staff.updateStaff(manager.id, {"role": "cashier"}))["role"] == "cashier"
    assert api.session.signedIn is False
    assert "sign in again" in api.session.notice


def test_deactivating_yourself_signs_you_out(api, conn, manager):
    _second_manager(conn, manager)
    api.session.start(manager)
    assert _data(api.staff.deactivateStaff(manager.id))["active"] is False
    assert api.session.signedIn is False


def test_the_last_manager_cannot_demote_themselves_and_stays_signed_in(api, manager):
    api.session.start(manager)
    assert "last active manager" in _error(api.staff.updateStaff(manager.id, {"role": "cashier"}))
    assert api.session.signedIn is True


def test_renaming_yourself_updates_the_session_without_signing_out(api, manager):
    api.session.start(manager)
    api.staff.updateStaff(manager.id, {"full_name": "New Name"})
    assert api.session.signedIn is True
    assert api.session.user["full_name"] == "New Name"


def test_changing_someone_else_leaves_your_session_alone(api, conn, manager, cashier):
    api.session.start(manager)
    spy = QSignalSpy(api.staffChanged)
    assert _data(api.staff.updateStaff(cashier.id, {"role": "stock_clerk"}))["role_text"] == (
        "Stock clerk"
    )
    assert api.session.signedIn is True
    assert api.session.user["role"] == "manager"
    assert spy.count() == 1


def test_staff_slots(api, conn, manager):
    api.session.start(manager)
    assert "match" in _error(
        api.staff.createStaff("carl", "Carl", "cashier", PASSWORD, "other-password")
    )
    created = _data(api.staff.createStaff("carl", "Carl", "cashier", PASSWORD, PASSWORD))
    assert created["active"] is True  # a bool for QML, though the service returns 1
    assert "password_hash" not in created
    assert "taken" in _error(api.staff.createStaff("CARL", "Carl", "cashier", PASSWORD, PASSWORD))
    assert api.staff.resetPassword(created["id"], "fresh-password", "fresh-password")["ok"]
    assert auth.authenticate(conn, "carl", "fresh-password")
    assert _data(api.staff.deactivateStaff(created["id"]))["active"] is False
    assert _data(api.staff.reactivateStaff(created["id"]))["active"] is True
    assert {record["username"] for record in _data(api.staff.listStaff())} == {"manager", "carl"}


# --- Idle lock ----------------------------------------------------------------------


IDLE_MS = 400
INPUT_GAP_MS = 100  # input this often keeps the session alive
PAST_IDLE_MS = 700


@pytest.fixture
def idle_api(qapp, conn, tmp_path) -> Iterator[Api]:
    bridge = Api(conn, ":memory:", tmp_path / "backups", idle_timeout_ms=IDLE_MS)
    yield bridge
    bridge.session.shutdown()


def _press_key() -> None:
    target = QObject()
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier)
    QCoreApplication.sendEvent(target, event)


def test_default_idle_timeout_is_ten_minutes(api, manager):
    api.session.start(manager)
    assert api.session._idle._timer.interval() == 600_000


def test_idle_timeout_signs_out_and_signals(idle_api, manager):
    locked = QSignalSpy(idle_api.session.locked)
    idle_api.session.start(manager)
    QTest.qWait(PAST_IDLE_MS)
    assert idle_api.session.signedIn is False
    assert locked.count() == 1
    assert "idle" in idle_api.session.notice
    assert _error(idle_api.menu.listCategories(True)) == "Please sign in first."


def test_user_input_postpones_the_lock(idle_api, manager):
    idle_api.session.start(manager)
    for _ in range(7):
        QTest.qWait(INPUT_GAP_MS)
        _press_key()
    assert idle_api.session.signedIn is True  # well past one timeout
    QTest.qWait(PAST_IDLE_MS)
    assert idle_api.session.signedIn is False


def test_events_that_are_not_input_do_not_postpone_the_lock(idle_api, manager):
    idle_api.session.start(manager)
    for _ in range(7):
        QTest.qWait(INPUT_GAP_MS)
        QCoreApplication.sendEvent(QObject(), QEvent(QEvent.Type.HoverMove))
    assert idle_api.session.signedIn is False


def test_idle_lock_discards_the_cart(idle_api, conn, manager, item):
    idle_api.session.start(manager)
    _data(idle_api.sales.cartAdd(item["id"]))
    cart_changed = QSignalSpy(idle_api.sales.cartChanged)
    QTest.qWait(PAST_IDLE_MS)
    assert cart_changed.count() == 1
    idle_api.session.start(manager)
    assert _data(idle_api.sales.cart())["lines"] == []


def test_no_lock_fires_after_signing_out_manually(idle_api, manager):
    locked = QSignalSpy(idle_api.session.locked)
    idle_api.session.start(manager)
    idle_api.session.signOut()
    _press_key()  # must not restart the stopped timer
    QTest.qWait(PAST_IDLE_MS)
    assert locked.count() == 0
    assert idle_api.session.notice == ""


def test_signing_in_clears_the_idle_notice(idle_api, manager):
    idle_api.session.start(manager)
    QTest.qWait(PAST_IDLE_MS)
    assert idle_api.session.notice != ""
    idle_api.session.start(manager)
    assert idle_api.session.notice == ""


# --- Cart and sales -----------------------------------------------------------------


def test_cart_totals_are_computed_by_the_bridge(api, conn, manager, item):
    api.session.start(manager)
    _data(api.settings.update({"tax_rate": "10"}))
    _data(api.sales.cartAdd(item["id"]))
    cart = _data(api.sales.cartAdd(item["id"]))
    assert cart["item_count"] == 2
    assert cart["subtotal_text"] == "$25.00"
    assert cart["tax_text"] == "$2.50"
    assert cart["total_text"] == "$27.50"
    assert cart["tax_rate_text"] == "10.00%"
    line = cart["lines"][0]
    assert (line["name"], line["quantity"], line["line_total_text"]) == ("Burger", 2, "$25.00")
    assert line["unit_price_text"] == "$12.50"
    assert line["available"] is True


def test_empty_cart_is_valid(api, manager):
    api.session.start(manager)
    cart = _data(api.sales.cart())
    assert cart["lines"] == []
    assert cart["total_text"] == "$0.00"
    assert "at least one item" in _error(api.sales.checkout("cash"))


@pytest.mark.parametrize(("value", "expected"), [(3, 3), (2.0, 2), ("4", 4), (" 5 ", 5)])
def test_cart_quantity_accepts_whole_numbers_as_qml_may_send_them(
    api, manager, item, value, expected
):
    api.session.start(manager)
    api.sales.cartAdd(item["id"])
    assert _data(api.sales.cartSetQuantity(item["id"], value))["item_count"] == expected


@pytest.mark.parametrize("value", [2.5, "2.5", "two", "", True, -1, 10_001, None])
def test_cart_quantity_rejects_anything_else_and_keeps_the_line(api, manager, item, value):
    api.session.start(manager)
    api.sales.cartAdd(item["id"])
    _error(api.sales.cartSetQuantity(item["id"], value))
    assert _data(api.sales.cart())["item_count"] == 1


def test_cart_line_edits(api, manager, item):
    api.session.start(manager)
    assert "not in the sale" in _error(api.sales.cartSetQuantity(item["id"], 1))
    api.sales.cartAdd(item["id"])
    assert _data(api.sales.cartSetQuantity(item["id"], 0))["lines"] == []
    api.sales.cartAdd(item["id"])
    assert _data(api.sales.cartRemove(item["id"]))["lines"] == []
    api.sales.cartAdd(item["id"])
    assert _data(api.sales.cartClear())["lines"] == []


def test_cart_refuses_items_that_cannot_be_sold(api, conn, manager, category, item):
    api.session.start(manager)
    menu.set_item_active(conn, manager, item["id"], False)
    assert "not available" in _error(api.sales.cartAdd(item["id"]))
    menu.set_item_active(conn, manager, item["id"], True)
    menu.set_category_active(conn, manager, category["id"], False)
    assert "not available" in _error(api.sales.cartAdd(item["id"]))
    assert "no longer exists" in _error(api.sales.cartAdd(99_999))


def test_item_deactivated_while_in_the_cart_blocks_checkout_and_keeps_the_cart(
    api, conn, manager, item
):
    api.session.start(manager)
    api.sales.cartAdd(item["id"])
    menu.set_item_active(conn, manager, item["id"], False)
    assert _data(api.sales.cart())["lines"][0]["available"] is False
    assert "not available" in _error(api.sales.checkout("cash"))
    assert _data(api.sales.cart())["item_count"] == 1
    assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 0


def test_checkout_records_the_sale_empties_the_cart_and_signals(
    api, conn, manager, cashier, item, flour
):
    menu.set_recipe(conn, manager, item["id"], [{"ingredient_id": flour["id"], "qty_milli": 250}])
    api.session.start(cashier)
    sales_changed = QSignalSpy(api.salesChanged)
    stock_changed = QSignalSpy(api.stockChanged)
    api.sales.cartAdd(item["id"])
    api.sales.cartAdd(item["id"])

    sale = _data(api.sales.checkout("card"))

    assert sale["total_text"] == "$25.00"
    assert sale["payment_method_text"] == "Card"
    assert sale["cashier_name"] == "Cashier"
    assert sale["voided"] is False
    assert sale["voided_at_text"] == ""
    assert sale["void_reason"] == ""  # null reaches QML as an empty string
    assert sale["created_at_text"]
    assert sale["items"][0]["name_as_sold"] == "Burger"
    assert sales_changed.count() == 1
    assert stock_changed.count() == 1
    assert _data(api.sales.cart())["lines"] == []
    on_hand = conn.execute(
        "SELECT on_hand_milli FROM ingredients WHERE id = ?", (flour["id"],)
    ).fetchone()[0]
    assert on_hand == -500


def test_bad_payment_method_keeps_the_cart_and_sends_no_signals(api, manager, item):
    api.session.start(manager)
    api.sales.cartAdd(item["id"])
    sales_changed = QSignalSpy(api.salesChanged)
    assert "payment method" in _error(api.sales.checkout("bitcoin"))
    assert sales_changed.count() == 0
    assert _data(api.sales.cart())["item_count"] == 1


def test_void_returns_stock_and_signals_and_cannot_repeat(api, conn, manager, item, flour):
    menu.set_recipe(conn, manager, item["id"], [{"ingredient_id": flour["id"], "qty_milli": 250}])
    api.session.start(manager)
    api.sales.cartAdd(item["id"])
    sale = _data(api.sales.checkout("cash"))
    stock_changed = QSignalSpy(api.stockChanged)
    assert "reason" in _error(api.sales.voidSale(sale["id"], "   "))
    assert stock_changed.count() == 0

    voided = _data(api.sales.voidSale(sale["id"], "Mistake"))

    assert voided["voided"] is True
    assert voided["voided_by_name"] == "Manager"
    assert voided["voided_at_text"]
    assert stock_changed.count() == 1
    assert "already been voided" in _error(api.sales.voidSale(sale["id"], "Again"))
    assert _data(api.sales.getSale(sale["id"]))["void_reason"] == "Mistake"
    listed = _data(api.sales.listSales("", "", 10, 0))
    assert [row["id"] for row in listed] == [sale["id"]]


def test_list_sales_date_arguments(api, manager):
    api.session.start(manager)
    today = _data(api.reports.localDate(0))
    assert _data(api.sales.listSales(today, today, 10, 0)) == []
    assert "both a start date" in _error(api.sales.listSales(today, "", 10, 0))
    assert "valid date" in _error(api.sales.listSales("soon", "later", 10, 0))


# --- Money and quantity text --------------------------------------------------------


def test_menu_accepts_typed_money(api, manager, category):
    api.session.start(manager)
    created = _data(api.menu.createItem(category["id"], "Burger", " 1234.50 "))
    assert created["price_cents"] == 123450
    assert created["price_text"] == "$1,234.50"
    assert created["price_input"] == "1234.50"  # no comma: parse_money must accept it back
    assert created["has_recipe"] is False
    updated = _data(api.menu.updateItem(created["id"], {"price": created["price_input"]}))
    assert updated["price_cents"] == 123450


@pytest.mark.parametrize("price", ["", "abc", "12.345", "-1", "1,000", "nan", "1e400000"])
def test_menu_rejects_bad_typed_money(api, conn, manager, category, price):
    api.session.start(manager)
    _error(api.menu.createItem(category["id"], "Burger", price))
    assert conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0] == 0


def test_menu_slots_signal_changes_only_on_success(api, manager, category):
    api.session.start(manager)
    spy = QSignalSpy(api.menuChanged)
    _error(api.menu.createCategory("", 0))
    assert spy.count() == 0
    _data(api.menu.createCategory("Drinks", "3"))
    assert spy.count() == 1
    assert "already exists" in _error(api.menu.createCategory("drinks", 0))
    assert "whole number" in _error(api.menu.createCategory("Sides", "first"))
    assert spy.count() == 1


def test_menu_edit_only_changes_the_given_fields(api, manager, category, item):
    api.session.start(manager)
    renamed = _data(api.menu.updateItem(item["id"], {"name": "Cheeseburger"}))
    assert (renamed["name"], renamed["price_cents"]) == ("Cheeseburger", 1250)
    other = _data(api.menu.createCategory("Lunch", 1))
    moved = _data(api.menu.updateItem(item["id"], {"category_id": other["id"]}))
    assert moved["category_name"] == "Lunch"
    assert _data(api.menu.updateCategory(other["id"], {"sort_order": 9}))["sort_order"] == 9
    assert _data(api.menu.setItemActive(item["id"], False))["active"] is False
    assert _data(api.menu.setCategoryActive(other["id"], False))["active"] is False
    assert _data(api.menu.listSellableItems()) == []
    assert len(_data(api.menu.listItems(0, True))) == 1
    assert _data(api.menu.listItems(0, False)) == []
    assert len(_data(api.menu.listItems(other["id"], True))) == 1
    assert _data(api.menu.getItem(item["id"]))["name"] == "Cheeseburger"


def test_unknown_field_names_are_a_logged_screen_bug(api, manager, category, item, log_file):
    api.session.start(manager)
    assert _error(api.menu.updateItem(item["id"], {"price_cents": 1})) == GENERIC_ERROR
    assert "Unknown fields" in log_file.read_text(encoding="utf-8")


def test_recipe_takes_typed_quantities(api, conn, manager, item, flour):
    api.session.start(manager)
    lines = _data(api.menu.setRecipe(item["id"], [{"ingredient_id": flour["id"], "qty": "0.25"}]))
    assert lines[0]["qty_milli"] == 250
    assert lines[0]["qty_text"] == "0.25"
    assert _data(api.menu.getItem(item["id"]))["has_recipe"] is True
    assert _data(api.menu.getRecipe(item["id"]))[0]["ingredient_name"] == "Flour"
    assert "not valid" in _error(api.menu.setRecipe(item["id"], [{"qty": "1"}]))
    assert "not valid" in _error(api.menu.setRecipe(item["id"], ["junk"]))
    _error(api.menu.setRecipe(item["id"], [{"ingredient_id": flour["id"], "qty": "0.0001"}]))
    assert _data(api.menu.setRecipe(item["id"], [])) == []


def test_inventory_slots_take_typed_values_and_signal(api, conn, manager, flour):
    api.session.start(manager)
    stock = QSignalSpy(api.stockChanged)
    ingredient = flour["id"]
    assert _data(api.inventory.receiveStock(ingredient, "2.5", "Delivery"))["delta_text"] == "+2.5"
    assert stock.count() == 1
    assert _data(api.inventory.recordWaste(ingredient, "0.5", ""))["delta_text"] == "-0.5"
    assert _data(api.inventory.adjustStock(ingredient, "-0.25", ""))["delta_input"] == "-0.25"
    assert "valid quantity" in _error(api.inventory.receiveStock(ingredient, "lots", ""))
    assert stock.count() == 3

    counted = _data(api.inventory.recordCount(ingredient, "1.75", ""))
    assert counted == ""  # matched, so no movement
    movement = _data(api.inventory.recordCount(ingredient, "2", "Recount"))
    assert movement["movement_type_text"] == "Adjusted"

    record = _data(api.inventory.getIngredient(ingredient))
    assert record["on_hand_text"] == "2"
    assert record["unit_cost_text"] == "$2.00"
    assert record["low_text"] == "1"
    rows = _data(api.inventory.listMovements(ingredient, "", "", 50, 0))
    assert rows[0]["movement_type"] == "adjusted"
    assert rows[0]["created_at_text"]
    assert _data(api.inventory.listMovements(0, "", "", 1, 0))[0]["id"] == rows[0]["id"]


def test_ingredient_edits_and_low_stock(api, conn, manager, stock_clerk):
    api.session.start(stock_clerk)
    created = _data(api.inventory.createIngredient("Salt", "kg", "0.80", "2"))
    assert created["unit_cost_cents"] == 80
    assert created["low_milli"] == 2000
    assert _data(api.inventory.lowStockCount()) == 1
    assert _data(api.inventory.listLowStock())[0]["name"] == "Salt"
    assert "already exists" in _error(api.inventory.createIngredient("salt", "kg", "1", "1"))
    assert "valid amount" in _error(api.inventory.createIngredient("Oil", "L", "", "1"))
    updated = _data(api.inventory.updateIngredient(created["id"], {"low": "0", "unit_cost": "1"}))
    assert (updated["low_milli"], updated["unit_cost_cents"], updated["name"]) == (0, 100, "Salt")
    assert _data(api.inventory.setIngredientActive(created["id"], False))["active"] is False
    assert _data(api.inventory.listIngredients(False)) == []


def test_low_stock_count_reaches_a_cashier(api, conn, manager, cashier, flour):
    api.session.start(cashier)
    assert _data(api.inventory.lowStockCount()) == 1
    assert "permission" in _error(api.inventory.listLowStock())


def test_settings_tax_rate_and_symbol(api, manager, item):
    changed = QSignalSpy(api.settings.changed)
    api.session.start(manager)
    updated = _data(
        api.settings.update(
            {"restaurant_name": "Olive", "currency_symbol": "EUR", "tax_rate": "8.25"}
        )
    )
    assert updated["tax_rate_bp"] == 825
    assert updated["tax_rate_text"] == "8.25%"
    assert updated["tax_rate_input"] == "8.25"
    assert changed.count() == 1
    assert api.settings.restaurantName == "Olive"
    assert api.settings.currencySymbol == "EUR"
    assert _data(api.menu.getItem(item["id"]))["price_text"] == "EUR12.50"


@pytest.mark.parametrize("rate", ["8.255", "abc", "", "-1", "100.01"])
def test_bad_tax_rate_changes_nothing(api, manager, rate):
    api.session.start(manager)
    changed = QSignalSpy(api.settings.changed)
    _error(api.settings.update({"restaurant_name": "Changed", "tax_rate": rate}))
    assert changed.count() == 0
    assert api.settings.restaurantName == "My Restaurant"


def test_audit_slot(api, manager, category):
    api.session.start(manager)
    entries = _data(api.audit.listEntries(10, 0))
    assert entries[0]["action"] == "menu.category_created"
    assert entries[0]["created_at_text"]


# --- Conversions --------------------------------------------------------------------


def test_to_qml_adds_display_strings_at_every_depth():
    result = to_qml(
        {
            "total_cents": -123456,
            "lines": [{"on_hand_milli": -1500, "created_at": "2026-01-02T03:04:05+00:00"}],
            "missing": None,
            "active": 1,
            "name": "x",
            "count": 3,
            "flag": True,
            "delta_milli": 2000,
        },
        "$",
    )
    assert result["total_text"] == "-$1,234.56"
    assert result["total_input"] == "-1234.56"
    assert result["lines"][0]["on_hand_text"] == "-1.5"
    assert result["lines"][0]["created_at_text"]
    assert result["missing"] == ""
    assert result["active"] is True
    assert result["flag"] is True
    assert result["delta_text"] == "+2"
    assert not any(isinstance(value, float) for value in result.values())


def test_local_time_converts_from_utc(monkeypatch):
    assert local_time(None) == ""
    assert local_time("") == ""
    assert local_time("not a time") == "not a time"
    from datetime import datetime, timezone

    stamp = datetime(2026, 6, 15, 12, 30, tzinfo=timezone.utc)
    expected = stamp.astimezone().strftime("%Y-%m-%d %H:%M")
    assert local_time(stamp.isoformat()) == expected


def test_values_survive_the_trip_through_real_qml(qapp, api, conn, manager, category):
    """JS numbers, objects and arrays really arrive in the shapes the slots expect."""
    api.session.start(manager)
    engine = QQmlEngine()
    engine.rootContext().setContextProperty("api", api)
    component = QQmlComponent(engine)
    component.setData(
        b"""
        import QtQml
        QtObject {
            property var made: api.menu.createItem(api.menu.listCategories(true).data[0].id,
                                                   "Soup", "4.50")
            property var cart: api.sales.cartAdd(made.data.id)
            property var quantity: api.sales.cartSetQuantity(made.data.id, 6 / 2)
            property var edit: api.menu.updateItem(made.data.id, {name: "Broth", price: "5"})
            property var bad: api.menu.updateItem(made.data.id, {name: ""})
            property string badType: typeof bad.ok + ":" + bad.ok + ":" + bad.error
            property var nullValue: api.sales.checkout("cash").data.void_reason
            property int reportRows: {
                var checkedOut = nullValue  // the sale above comes first
                return api.reports.run("sales_by_item", {
                    start_date: api.reports.localDate(0).data,
                    end_date: api.reports.localDate(1 - 1).data }).data.rows.length
            }
            property string userName: api.session.user.full_name
            property bool canEdit: api.session.permissions.edit_menu
            property string firstSection: api.session.sections[0].key
        }
        """,
        "file:///probe.qml",
    )
    root = component.create()
    assert root is not None, component.errorString()
    assert root.property("made")["ok"] is True
    assert root.property("quantity")["data"]["item_count"] == 3
    assert root.property("edit")["data"]["price_cents"] == 500
    assert root.property("badType").startswith("boolean:false:Enter a")
    assert root.property("nullValue") == ""
    assert root.property("userName") == "Manager"
    assert root.property("canEdit") is True
    assert root.property("firstSection") == "home"
    assert root.property("reportRows") == 1
    del root, engine


# --- Reports ------------------------------------------------------------------------


def _sold_one(api, conn, manager, item) -> dict:
    api.session.start(manager)
    api.sales.cartAdd(item["id"])
    return _data(api.sales.checkout("cash"))


def _range(api) -> dict:
    today = _data(api.reports.localDate(0))
    return {"start_date": today, "end_date": today}


def _params_for(api, name: str) -> dict:
    return _range(api) if REPORTS[name].dated else {}


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("manager", set(REPORTS)),
        ("stock_clerk", {"stock_levels", "stock_value", "low_stock", "movement_history"}),
        ("cashier", set()),
    ],
)
def test_report_list_matches_the_role(api, request, fixture, expected):
    api.session.start(request.getfixturevalue(fixture))
    assert {report["name"] for report in _data(api.reports.listReports())} == expected


@pytest.mark.parametrize("name", list(REPORTS))
def test_every_report_runs_with_display_values_for_every_column(
    api, conn, manager, item, flour, name
):
    menu.set_recipe(conn, manager, item["id"], [{"ingredient_id": flour["id"], "qty_milli": 250}])
    _sold_one(api, conn, manager, item)
    report = _data(api.reports.run(name, _params_for(api, name)))
    assert report["rows"], "the sale should appear in every report"
    assert report["title"]
    for column in report["columns"]:
        assert column["display"] in report["rows"][0], column


@pytest.mark.parametrize("name", list(REPORTS))
def test_every_report_exports_to_csv(api, conn, manager, item, flour, name, tmp_path):
    menu.set_recipe(conn, manager, item["id"], [{"ingredient_id": flour["id"], "qty_milli": 250}])
    _sold_one(api, conn, manager, item)
    path = tmp_path / f"{name}.csv"
    result = _data(api.reports.exportCsv(name, _params_for(api, name), str(path)))
    with open(path, encoding="utf-8-sig", newline="") as file:
        table = list(csv.reader(file))
    assert table[0] == [column.header for column in REPORTS[name].columns]
    assert len(table) == result["row_count"] + 1 >= 2


def test_csv_holds_exact_decimals_not_display_strings(api, conn, manager, item, tmp_path):
    _sold_one(api, conn, manager, item)
    path = tmp_path / "summary.csv"
    _data(api.reports.exportCsv("sales_summary", _range(api), str(path)))
    with open(path, encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows[0]["Total"] == "12.50"
    assert rows[0]["Sales"] == "1"


def test_csv_stock_value_ends_with_a_total_row(api, conn, manager, flour, tmp_path):
    api.session.start(manager)
    api.inventory.receiveStock(flour["id"], "3", "")
    report = _data(api.reports.run("stock_value", {}))
    assert report["summary"]["total_value_text"] == "$6.00"
    path = tmp_path / "value.csv"
    _data(api.reports.exportCsv("stock_value", {}, str(path)))
    with open(path, encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    assert rows[-1]["Ingredient"] == "Total"
    assert rows[-1]["Value"] == "6.00"


def test_csv_accepts_a_file_dialog_url(api, manager, tmp_path):
    from PySide6.QtCore import QUrl

    api.session.start(manager)
    path = tmp_path / "from dialog.csv"
    url = QUrl.fromLocalFile(str(path)).toString()
    assert _data(api.reports.exportCsv("stock_levels", {}, url))["row_count"] == 0
    assert path.exists()


def test_csv_export_failures_are_staff_messages(api, manager, tmp_path):
    api.session.start(manager)
    assert "Choose a file" in _error(api.reports.exportCsv("stock_levels", {}, ""))
    assert "Could not write" in _error(api.reports.exportCsv("stock_levels", {}, str(tmp_path)))


def test_csv_export_is_refused_without_permission(api, stock_clerk, tmp_path):
    api.session.start(stock_clerk)
    path = tmp_path / "x.csv"
    assert "permission" in _error(api.reports.exportCsv("sales_summary", _range(api), str(path)))
    assert not path.exists()


def test_report_argument_errors(api, conn, manager, item, flour):
    menu.set_recipe(conn, manager, item["id"], [{"ingredient_id": flour["id"], "qty_milli": 250}])
    _sold_one(api, conn, manager, item)
    assert "doesn't exist" in _error(api.reports.run("nope", {}))
    assert "valid date" in _error(api.reports.run("sales_summary", {}))
    backwards = {"start_date": "2026-02-02", "end_date": "2026-02-01"}
    assert "start date" in _error(api.reports.run("sales_summary", backwards))
    assert _error(api.reports.run("stock_levels", {"start_date": "x"})) == GENERIC_ERROR


def test_movement_report_can_filter_by_ingredient(api, conn, manager, flour):
    api.session.start(manager)
    other = inventory.create_ingredient(conn, manager, "Salt", "kg", 100, 0)
    api.inventory.receiveStock(flour["id"], "1", "")
    api.inventory.receiveStock(other["id"], "1", "")
    params = {**_range(api), "ingredient_id": other["id"]}
    rows = _data(api.reports.run("movement_history", params))["rows"]
    assert [row["ingredient"] for row in rows] == ["Salt"]
    assert len(_data(api.reports.run("movement_history", _range(api)))["rows"]) == 2


def test_voided_sale_is_counted_separately_in_the_report(api, conn, manager, item):
    sale = _sold_one(api, conn, manager, item)
    api.sales.voidSale(sale["id"], "Mistake")
    summary = _data(api.reports.run("sales_summary", _range(api)))["summary"]
    assert summary["sale_count"] == 0
    assert summary["total_text"] == "$0.00"
    assert summary["void_count"] == 1
    assert summary["voided_total_text"] == "$12.50"


# --- Backup and restore -------------------------------------------------------------


def _seed_manager(api: Api) -> dict:
    return _data(api.session.createFirstManager("maria", "Maria", PASSWORD, PASSWORD))


def test_backup_on_demand(file_api):
    _seed_manager(file_api)
    path = Path(_data(file_api.backup.create())["path"])
    assert path.parent == file_api.backup_dir
    assert path.exists()
    assert file_api.backup.folder == str(file_api.backup_dir)
    assert file_api.backup.folderUrl.startswith("file:///")


def test_restore_replaces_the_data_reconnects_signs_out_and_tells_qml(file_api, qapp):
    _seed_manager(file_api)
    saved = _data(file_api.backup.create())["path"]
    _data(file_api.settings.update({"restaurant_name": "After the backup"}))
    category = _data(file_api.menu.createCategory("Later", 0))
    item = _data(file_api.menu.createItem(category["id"], "Later item", "1"))
    file_api.sales.cartAdd(item["id"])
    old_conn = file_api.conn
    QTest.qWait(0)  # let the sign-in notification go first
    restored = QSignalSpy(file_api.restored)
    settings_changed = QSignalSpy(file_api.settings.changed)
    session_changed = QSignalSpy(file_api.session.changed)
    cart_changed = QSignalSpy(file_api.sales.cartChanged)

    result = _data(file_api.backup.restore(saved))

    assert result["restored_from"] == saved
    assert Path(result["safety_copy"]).exists()
    assert file_api.conn is not old_conn
    with pytest.raises(sqlite3.ProgrammingError):
        old_conn.execute("SELECT 1")
    assert file_api.session.signedIn is False
    assert "restored" in file_api.session.notice
    assert restored.count() == 1
    assert settings_changed.count() == 1
    assert cart_changed.count() == 1
    QTest.qWait(0)
    assert session_changed.count() == 1
    assert file_api.settings.restaurantName == "My Restaurant"
    assert file_api.conn.execute("SELECT COUNT(*) FROM menu_items").fetchone()[0] == 0
    assert _data(file_api.session.signIn("maria", PASSWORD))["username"] == "maria"


def test_restore_accepts_a_file_dialog_url(file_api):
    from PySide6.QtCore import QUrl

    _seed_manager(file_api)
    saved = _data(file_api.backup.create())["path"]
    assert file_api.backup.restore(QUrl.fromLocalFile(saved).toString())["ok"]


def test_failed_restore_keeps_the_user_signed_in_with_a_working_database(file_api, tmp_path):
    _seed_manager(file_api)
    _data(file_api.menu.createCategory("Kept", 0))
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not a database")
    restored = QSignalSpy(file_api.restored)

    assert "not a valid restaurant backup" in _error(file_api.backup.restore(str(junk)))

    assert file_api.session.signedIn is True
    assert restored.count() == 0
    assert _data(file_api.menu.listCategories(True))[0]["name"] == "Kept"


def test_restore_is_refused_without_permission_before_touching_the_connection(file_api, tmp_path):
    _seed_manager(file_api)
    saved = _data(file_api.backup.create())["path"]
    manager = file_api.current_actor
    record = staff.create_staff(file_api.conn, manager, "carl", "Carl", "cashier", PASSWORD)
    file_api.session.end()
    file_api.session.start(Actor(record["id"], "carl", "Carl", Role.CASHIER))
    conn = file_api.conn

    assert "permission" in _error(file_api.backup.restore(saved))

    assert file_api.conn is conn
    conn.execute("SELECT 1")
    assert file_api.session.signedIn is True


def test_restore_of_a_missing_file_or_no_file(file_api, tmp_path):
    _seed_manager(file_api)
    assert "Choose a file" in _error(file_api.backup.restore(""))
    assert "could not be read" in _error(file_api.backup.restore(str(tmp_path / "gone.db")))
    assert file_api.session.signedIn is True


# --- Starting the app ---------------------------------------------------------------


def _run_app(data_dir: Path, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "PYTHONPATH": str(PROJECT_ROOT / "src"),
        "QT_QPA_PLATFORM": "offscreen",
        "QT_QUICK_BACKEND": "software",
        "RESTAURANT_DATA_DIR": str(data_dir),
    }
    if os.environ.get("WINDIR"):
        env["QT_QPA_FONTDIR"] = str(Path(os.environ["WINDIR"]) / "Fonts")
    return subprocess.run(
        [sys.executable, "-m", "restaurant", "--smoke-test", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_smoke_test_starts_cleanly_and_makes_a_backup(tmp_path):
    result = _run_app(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "restaurant.db").exists()
    assert len(list((tmp_path / "backups").glob("restaurant-backup-*.db"))) == 1


def test_clean_start_and_exit_leave_no_warnings(tmp_path):
    """Includes shutdown, where bindings outliving ``api`` would complain."""
    result = _run_app(tmp_path)
    assert result.returncode == 0
    assert result.stderr == ""
    log_path = tmp_path / "restaurant.log"
    assert not log_path.exists() or log_path.read_text(encoding="utf-8") == ""


def test_data_folder_is_created_when_missing(tmp_path):
    folder = tmp_path / "does" / "not" / "exist"
    result = _run_app(folder)
    assert result.returncode == 0, result.stderr
    assert (folder / "restaurant.db").exists()


def test_second_copy_is_refused_with_a_clear_message(tmp_path):
    lock = QLockFile(str(tmp_path / "restaurant.lock"))
    assert lock.tryLock(0)
    try:
        result = _run_app(tmp_path)
    finally:
        lock.unlock()
    assert result.returncode == 3
    assert "already running" in result.stderr
    assert not (tmp_path / "restaurant.db").exists()


def test_lock_left_by_a_dead_process_does_not_block_the_next_start(tmp_path):
    assert _run_app(tmp_path).returncode == 0
    assert _run_app(tmp_path).returncode == 0


def test_corrupt_database_shows_a_message_and_makes_no_backup(tmp_path):
    (tmp_path / "restaurant.db").write_bytes(b"not a database, just some bytes" * 100)
    result = _run_app(tmp_path)
    assert result.returncode == 5
    assert "could not be opened" in result.stderr
    assert "backups" in result.stderr
    assert not (tmp_path / "backups").exists()
    assert "Traceback" in (tmp_path / "restaurant.log").read_text(encoding="utf-8")


def test_database_from_a_newer_version_is_refused(tmp_path):
    conn = sqlite3.connect(tmp_path / "restaurant.db")
    conn.execute("PRAGMA user_version = 9999")
    conn.close()
    result = _run_app(tmp_path)
    assert result.returncode == 5
    assert "newer than this app" in result.stderr


def test_failed_automatic_backup_does_not_stop_the_app(tmp_path):
    (tmp_path / "backups").write_text("a file where the folder should be")
    result = _run_app(tmp_path)
    assert result.returncode == 0, result.stderr
    assert "Automatic backup failed" in (tmp_path / "restaurant.log").read_text(encoding="utf-8")
