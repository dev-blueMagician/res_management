"""Text staff typed is only ever shown as text: never markup, never a network request (NFR-1).

Every label in every screen and dialog is checked, including combo-box rows, over data
holding markup that would hide text or fetch an image from a local listener.
"""

from __future__ import annotations

import http.server
import os
import re
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QUICK_BACKEND", "software")
if os.environ.get("WINDIR"):
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ["WINDIR"]) / "Fonts"))

from PySide6.QtCore import QMetaObject, QObject, Qt, QUrl  # noqa: E402
from PySide6.QtQml import QQmlEngine, QQmlExpression  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from restaurant.bridge.api import MAIN_QML, QML_DIR, Api, create_engine, destroy_engine  # noqa: E402
from restaurant.core import Actor, Role  # noqa: E402
from restaurant.db.connection import connect  # noqa: E402
from restaurant.services import audit, auth, inventory, menu, sales, settings, staff  # noqa: E402

SETTLE_MS = 40
PASSWORD = "correct-horse"
PLAIN_TEXT = 0  # Text.PlainText
HIDDEN_NOTE = 'routine<font color="#ffffff"> TOOK 4KG HOME</font>'
FORGED_NOTE = "x<br><b>BIG BOLD LINE</b><br>fake second line"
SHORT_FORGERY = "x<br><b>y</b><br>z"
ALLOWED_TEXT_ELEMENTS = {"Label.qml"}  # the one file that may name the Qt label


class _Listener(http.server.BaseHTTPRequestHandler):
    requests: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - the name http.server looks for
        type(self).requests.append(self.path)
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def listener() -> Iterator[tuple[int, list[str]]]:
    _Listener.requests = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Listener)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], _Listener.requests
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def qapp():
    QQuickStyle.setStyle("Basic")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def hostile(qapp, tmp_path, listener) -> Iterator[Api]:
    """A real bridge over data where every free-text field holds markup."""
    port, _requests = listener
    image = f'<img src="http://127.0.0.1:{port}/%s.png">'
    conn = connect(":memory:")
    created = auth.create_first_manager(conn, "boss", image % "manager-name", PASSWORD)
    boss = Actor(created["id"], "boss", "Boss", Role.MANAGER)
    settings.update(conn, boss, restaurant_name=image % "restaurant", tax_rate_bp=825)
    made = staff.create_staff(conn, boss, "clerk", image % "staff-name", "stock_clerk", PASSWORD)
    clerk = Actor(made["id"], "clerk", "Clerk", Role.STOCK_CLERK)
    made = staff.create_staff(conn, boss, "cash", "Cash", "cashier", PASSWORD)
    cashier = Actor(made["id"], "cash", "Cash", Role.CASHIER)

    ingredient = inventory.create_ingredient(
        conn, clerk, (image % "ingredient-name") + "Oil", image % "unit", 100, 5000
    )
    inventory.receive_stock(conn, clerk, ingredient["id"], 3000, image % "receive-note")
    inventory.adjust_stock(conn, clerk, ingredient["id"], -100, HIDDEN_NOTE)
    inventory.adjust_stock(conn, clerk, ingredient["id"], -100, FORGED_NOTE)
    inventory.adjust_stock(conn, clerk, ingredient["id"], -100, image % "audit-note")

    category = menu.create_category(conn, boss, image % "category")
    item = menu.create_item(conn, boss, category["id"], image % "item", 1250)
    menu.set_recipe(conn, boss, item["id"], [{"ingredient_id": ingredient["id"], "qty_milli": 10}])
    line = {"menu_item_id": item["id"], "quantity": 1}
    sales.record_sale(conn, cashier, [line], "cash")
    voided = sales.record_sale(conn, cashier, [line], "card")
    sales.void_sale(conn, boss, voided["id"], image % "void-reason")

    api = Api(conn, ":memory:", tmp_path / "backups")
    api.boss = boss
    api.clerk = clerk
    api.cashier = cashier
    yield api
    api.session.end()
    api.shutdown()


class _Window:
    def __init__(self, api: Api) -> None:
        self.engine = create_engine(api)
        self.engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
        assert self.engine.rootObjects(), "Main.qml failed to load"
        self.window = self.engine.rootObjects()[0]
        self.window.resize(1280, 800)
        QTest.qWaitForWindowExposed(self.window)
        QTest.qWait(SETTLE_MS)

    def close(self) -> None:
        destroy_engine(self.engine)

    def items(self) -> Iterator[QQuickItem]:
        pending = [self.window.contentItem()]
        while pending:
            item = pending.pop()
            yield item
            pending.extend(item.childItems())

    def text_items(self) -> list[QQuickItem]:
        # Qt's own field placeholders are skipped: test_placeholders_are_fixed_text covers them.
        return [
            item
            for item in self.items()
            if item.inherits("QQuickText") and not item.inherits("QQuickPlaceholderText")
        ]

    def not_plain(self) -> list[str]:
        return [
            item.property("text")
            for item in self.text_items()
            if self._text_format(item) != PLAIN_TEXT
        ]

    def _text_format(self, item: QQuickItem) -> int:
        # PySide6 can't convert the enum, but QML hands it over as a number.
        context = QQmlEngine.contextForObject(self.window)
        expression = QQmlExpression(context, item, "textFormat")
        value, _undefined = expression.evaluate()
        assert not expression.hasError(), expression.error().toString()
        return value

    def popups(self) -> list[QObject]:
        return [p for p in self.window.findChildren(QObject) if p.inherits("QQuickPopup")]


@pytest.fixture
def open_window(hostile) -> Iterator:
    windows: list[_Window] = []

    def make() -> _Window:
        window = _Window(hostile)
        windows.append(window)
        return window

    yield make
    hostile.session.end()
    QTest.qWait(SETTLE_MS)
    for window in windows:
        window.close()


@pytest.mark.parametrize(
    "role, sections",
    [
        ("boss", ("home", "sales", "menu", "inventory", "reports", "staff", "settings", "audit")),
        ("cashier", ("home", "sales", "menu")),
        ("clerk", ("home", "menu", "inventory", "reports")),
    ],
)
def test_every_label_on_every_screen_is_plain_text_and_nothing_is_fetched(
    hostile, open_window, listener, role, sections
):
    _port, requests = listener
    hostile.session.start(getattr(hostile, role))
    window = open_window()
    for section in sections:
        assert hostile.session.goTo(section)["ok"]
        QTest.qWait(SETTLE_MS)
        assert window.text_items(), section
        assert window.not_plain() == [], section
    assert requests == []


def test_every_dialog_and_drop_down_row_is_plain_text_too(hostile, open_window, listener):
    _port, requests = listener
    hostile.session.start(hostile.boss)
    window = open_window()
    opened = 0
    for section in ("sales", "menu", "inventory", "reports", "staff", "settings", "audit"):
        assert hostile.session.goTo(section)["ok"]
        QTest.qWait(SETTLE_MS)
        for popup in window.popups():
            QMetaObject.invokeMethod(popup, "open")
            QTest.qWait(10)
            opened += 1
            assert window.not_plain() == [], (section, popup)
            QMetaObject.invokeMethod(popup, "close")
            QTest.qWait(10)
    assert opened >= 5
    assert requests == []


def test_sign_in_screen_is_plain_text(hostile, open_window, listener):
    _port, requests = listener
    hostile.session.end()
    window = open_window()
    assert window.not_plain() == []
    assert requests == []


def test_hidden_text_in_a_note_is_visible_in_the_audit_log(hostile, open_window):
    hostile.session.start(hostile.boss)
    window = open_window()
    assert hostile.session.goTo("audit")["ok"]
    QTest.qWait(SETTLE_MS)
    shown = [item.property("text") for item in window.text_items() if item.isVisible()]
    assert any(HIDDEN_NOTE in text for text in shown), shown


def test_markup_cannot_forge_extra_lines_in_the_audit_log(hostile, open_window):
    audit.record(hostile.conn, hostile.boss.id, "stock.adjusted", SHORT_FORGERY)
    hostile.session.start(hostile.boss)
    window = open_window()
    assert hostile.session.goTo("audit")["ok"]
    QTest.qWait(SETTLE_MS)
    forged = next(item for item in window.text_items() if item.property("text") == SHORT_FORGERY)
    assert forged.property("lineCount") == 1


def test_markup_in_a_data_table_is_shown_literally(hostile, open_window):
    hostile.session.start(hostile.boss)
    window = open_window()  # home, whose low-stock table lists the hostile ingredient
    QTest.qWait(SETTLE_MS)
    shown = [item.property("text") for item in window.text_items() if item.isVisible()]
    assert any(text.startswith("<img src=") and text.endswith("Oil") for text in shown), shown
    assert window.not_plain() == []


def test_qml_only_uses_the_plain_label():
    """Rich text comes back the moment a screen names a raw Qt text element."""
    pattern = re.compile(
        r"(?<![\w.])(Text|TextEdit|TextArea)\s*\{|ToolTip|StyledText|RichText|MarkdownText|AutoText"
    )
    offenders = []
    for path in QML_DIR.rglob("*.qml"):
        source = path.read_text(encoding="utf-8")
        offenders += [f"{path.name}: {match.group()}" for match in pattern.finditer(source)]
        if "Controls.Label" in source and path.name not in ALLOWED_TEXT_ELEMENTS:
            offenders.append(f"{path.name}: Controls.Label")
    assert offenders == []


def test_placeholders_are_fixed_text():
    """Qt draws placeholders with its own rich-text-capable item, so no data may reach one."""
    for path in QML_DIR.rglob("*.qml"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if "placeholderText:" in line and "property" not in line and "root.placeholderText" not in line:
                assert re.fullmatch(r'\s*placeholderText: "[^"]*"', line), (path.name, line)


def test_every_file_using_label_sees_the_plain_one_last():
    """Later imports win, so the components folder must come after QtQuick.Controls."""
    for path in QML_DIR.rglob("*.qml"):
        if path.name in ALLOWED_TEXT_ELEMENTS:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        imports = [line.strip() for line in lines if line.startswith("import ")]
        if "import QtQuick.Controls" not in imports:
            continue
        local = [
            i for i, line in enumerate(imports)
            if line in ('import "."', 'import "components"', 'import "../components"')
        ]
        assert local and local[-1] == len(imports) - 1, path.name


def test_the_constant_this_module_checks_against_is_plain_text():
    assert Qt.TextFormat.PlainText.value == PLAIN_TEXT
