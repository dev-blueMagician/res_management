"""Load every QML file with a real engine and fail on any QML warning or error (NFR-8).

Files are found by globbing, so screens added later are covered automatically.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QUICK_BACKEND", "software")
if os.environ.get("WINDIR"):
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ["WINDIR"]) / "Fonts"))

from PySide6.QtCore import (  # noqa: E402
    QCoreApplication,
    QEvent,
    Qt,
    QtMsgType,
    QUrl,
    qInstallMessageHandler,
)
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtQml import QQmlEngine, QQmlExpression  # noqa: E402
from PySide6.QtQuickControls2 import QQuickStyle  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from restaurant.bridge.api import (  # noqa: E402
    MAIN_QML,
    QML_DIR,
    Api,
    create_engine,
    destroy_engine,
)
from restaurant.core import Actor  # noqa: E402
from restaurant.db.connection import connect  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SETTLE_MS = 30
PASSWORD = "correct-horse"
ROLES = ("manager", "cashier", "stock_clerk")

VIEW_FILES = sorted((QML_DIR / "views").glob("*.qml"))
COMPONENT_FILES = sorted((QML_DIR / "components").glob("*.qml"))

# Qt talking about the platform, not about our QML.
IGNORED_MESSAGES = ("propagateSizeHints", "QFontDatabase: Cannot find font directory")

HARNESS = b"""
import QtQuick
import QtQuick.Controls

ApplicationWindow {
    id: window
    width: 1280
    height: 800
    visible: true

    function loadFile(url) {
        var component = Qt.createComponent(url)
        if (component.status !== Component.Ready)
            return component.errorString() || "component not ready"
        var object = component.createObject(contentItem)
        if (!object)
            return component.errorString() || "createObject failed"
        if (object.width !== undefined) {
            object.width = 1016
            object.height = 688
        }
        if (typeof object.open === "function")
            object.open()
        loaded = object
        return ""
    }

    function unload() {
        if (loaded && typeof loaded.close === "function")
            loaded.close()
        if (loaded)
            loaded.destroy()
        loaded = null
    }

    property var loaded: null
}
"""


def _load_screenshot_tool():
    spec = importlib.util.spec_from_file_location("screenshot_tool", ROOT / "tools" / "screenshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def qapp():
    QQuickStyle.setStyle("Basic")
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def demo(qapp, tmp_path_factory) -> Iterator[tuple[Api, dict[str, Actor]]]:
    """A bridge over demo data, so that list delegates really get created."""
    conn = connect(":memory:")
    actors = _load_screenshot_tool().load_demo_data(conn)
    api = Api(conn, ":memory:", tmp_path_factory.mktemp("backups"))
    yield api, actors
    api.shutdown()


class Problems:
    """Everything the engine and Qt complained about while active."""

    def __init__(self, engine) -> None:
        self.found: list[str] = []
        engine.warnings.connect(self._on_engine_warnings)
        self._previous = qInstallMessageHandler(self._on_message)

    def _on_engine_warnings(self, errors: list) -> None:
        self.found.extend(error.toString() for error in errors)

    def _on_message(self, kind, _context, message: str) -> None:
        if kind in (QtMsgType.QtDebugMsg, QtMsgType.QtInfoMsg):
            return
        if not any(text in message for text in IGNORED_MESSAGES):
            self.found.append(message)

    def stop(self) -> None:
        qInstallMessageHandler(self._previous)


@pytest.fixture
def engine_for(demo) -> Iterator:
    """Make an engine for an Api, recording problems until the test ends."""
    created = []

    def make(api: Api):
        engine = create_engine(api)
        problems = Problems(engine)
        created.append((engine, problems))
        return engine, problems

    yield make
    for engine, problems in created:
        problems.stop()
        destroy_engine(engine)
    QTest.qWait(SETTLE_MS)


def _load_in_harness(engine, path: Path) -> str:
    engine.loadData(HARNESS, QUrl("file:///harness.qml"))
    window = engine.rootObjects()[-1]
    error = window.loadFile(QUrl.fromLocalFile(str(path)).toString())
    QTest.qWait(SETTLE_MS)
    window.unload()
    QTest.qWait(SETTLE_MS)
    return error


def _is_singleton(path: Path) -> bool:
    return "pragma Singleton" in path.read_text(encoding="utf-8")


def _ids(paths: list[Path]) -> list[str]:
    return [path.stem for path in paths]


def test_the_globs_found_the_screens():
    assert {"LoginView", "FirstRunView", "HomeView", "SalesView"} <= {p.stem for p in VIEW_FILES}
    assert {"Theme", "AppButton", "DataTable"} <= {p.stem for p in COMPONENT_FILES}


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("path", VIEW_FILES, ids=_ids(VIEW_FILES))
def test_view_loads_without_warnings_for_each_role(demo, engine_for, path, role):
    api, actors = demo
    api.session.start(actors[role])
    engine, problems = engine_for(api)
    error = _load_in_harness(engine, path)
    assert error == ""
    assert problems.found == []


@pytest.mark.parametrize("path", VIEW_FILES, ids=_ids(VIEW_FILES))
def test_view_loads_without_warnings_signed_out(demo, engine_for, path):
    """A screen can be alive for a moment after the idle lock signs the user out."""
    api, _ = demo
    api.session.end()
    engine, problems = engine_for(api)
    error = _load_in_harness(engine, path)
    assert error == ""
    assert problems.found == []


COMPONENTS_TO_CREATE = [path for path in COMPONENT_FILES if not _is_singleton(path)]


@pytest.mark.parametrize("path", COMPONENTS_TO_CREATE, ids=_ids(COMPONENTS_TO_CREATE))
def test_component_loads_without_warnings(demo, engine_for, path):
    api, actors = demo
    api.session.start(actors["manager"])
    engine, problems = engine_for(api)
    error = _load_in_harness(engine, path)
    assert error == ""
    assert problems.found == []


def test_every_component_is_listed_in_qmldir():
    listed = {
        line.split()[-1]
        for line in (QML_DIR / "components" / "qmldir").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    assert listed == {path.name for path in COMPONENT_FILES}


def test_every_view_named_by_the_navigation_exists(demo):
    api, actors = demo
    api.session.start(actors["manager"])
    for section in api.session.sections:
        assert (QML_DIR / "views" / section["view"]).exists(), section


def test_theme_singleton_is_usable_from_a_view(demo, engine_for, tmp_path):
    api, _ = demo
    views = tmp_path / "views"
    views.mkdir()
    probe = views / "Probe.qml"
    relative = Path(os.path.relpath(QML_DIR / "components", views)).as_posix()
    probe.write_text(
        f'import QtQuick\nimport "{relative}"\n'
        "Rectangle { color: Theme.accent; width: Theme.sidebarWidth }\n",
        encoding="utf-8",
    )
    engine, problems = engine_for(api)
    assert _load_in_harness(engine, probe) == ""
    assert problems.found == []


def test_the_check_really_catches_bad_qml(demo, engine_for, tmp_path):
    """The tests above are only worth anything if these all get noticed."""
    api, _ = demo
    broken_syntax = tmp_path / "BrokenSyntax.qml"
    broken_syntax.write_text("import QtQuick\nItem { width: }\n", encoding="utf-8")
    engine, _problems = engine_for(api)
    assert _load_in_harness(engine, broken_syntax) != ""

    bad_binding = tmp_path / "BadBinding.qml"
    bad_binding.write_text(
        "import QtQuick\nItem { width: api.noSuchObject.noSuchValue }\n", encoding="utf-8"
    )
    engine, problems = engine_for(api)
    assert _load_in_harness(engine, bad_binding) == ""
    assert any("noSuchValue" in problem for problem in problems.found)

    console_error = tmp_path / "ConsoleError.qml"
    console_error.write_text(
        'import QtQuick\nItem { Component.onCompleted: console.error("bad thing") }\n',
        encoding="utf-8",
    )
    engine, problems = engine_for(api)
    assert _load_in_harness(engine, console_error) == ""
    assert any("bad thing" in problem for problem in problems.found)


# --- Main.qml ------------------------------------------------------------------------


def _load_main(engine):
    engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    assert engine.rootObjects(), "Main.qml failed to load"
    QTest.qWait(SETTLE_MS)
    return engine.rootObjects()[0]


def _type(window, text: str) -> None:
    """Real key events through the window. QTest.keyClicks only takes widgets."""
    for character in text:
        key = Qt.Key(ord(character.upper()))
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            event = QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier, character)
            QCoreApplication.sendEvent(window, event)


def _fresh_api(tmp_path, **kwargs) -> Api:
    db_path = tmp_path / "restaurant.db"
    return Api(connect(db_path), db_path, tmp_path / "backups", **kwargs)


def test_main_loads_on_the_sign_in_screen(demo, engine_for):
    api, _ = demo
    api.session.end()
    engine, problems = engine_for(api)
    _load_main(engine)
    assert problems.found == []


def test_main_shows_every_section_for_every_role(demo, engine_for):
    api, actors = demo
    for role in ROLES:
        api.session.start(actors[role])
        engine, problems = engine_for(api)
        _load_main(engine)
        for section in api.session.sections:
            assert api.session.goTo(section["key"])["ok"]
            QTest.qWait(SETTLE_MS)
        api.session.end()
        QTest.qWait(SETTLE_MS)
        assert problems.found == [], role


def test_first_run_through_the_real_screen(qapp, engine_for, tmp_path):
    api = _fresh_api(tmp_path)
    try:
        engine, problems = engine_for(api)
        window = _load_main(engine)
        QTest.qWaitForWindowExposed(window)
        assert api.session.needsFirstRun is True
        # Focus starts on the first field; Enter moves on through the form.
        for text in ("Maria Lopez", "maria", PASSWORD, PASSWORD):
            _type(window, text)
            QTest.keyClick(window, Qt.Key.Key_Return)
        QTest.qWait(SETTLE_MS)
        assert api.session.signedIn is True
        assert api.session.user["username"] == "maria"
        assert api.session.needsFirstRun is False
        assert problems.found == []
    finally:
        api.session.end()
        QTest.qWait(SETTLE_MS)
        api.session.shutdown()


def test_sign_in_and_out_through_the_real_screen(demo, engine_for):
    api, _ = demo
    api.session.end()
    engine, problems = engine_for(api)
    window = _load_main(engine)
    QTest.qWaitForWindowExposed(window)

    _type(window, "maria")
    QTest.keyClick(window, Qt.Key.Key_Return)
    _type(window, "wrong-password")
    QTest.keyClick(window, Qt.Key.Key_Return)
    QTest.qWait(SETTLE_MS)
    assert api.session.signedIn is False

    # The failed attempt cleared the password and left focus on it.
    _type(window, _load_screenshot_tool().DEMO_PASSWORD)
    QTest.keyClick(window, Qt.Key.Key_Return)
    QTest.qWait(SETTLE_MS)
    assert api.session.signedIn is True

    assert api.session.signOut()["ok"]
    QTest.qWait(SETTLE_MS)
    assert api.session.signedIn is False
    assert problems.found == []


def test_idle_lock_returns_a_real_window_to_sign_in_without_warnings(qapp, engine_for, tmp_path):
    api = _fresh_api(tmp_path, idle_timeout_ms=400)
    try:
        api.session.createFirstManager("maria", "Maria", PASSWORD, PASSWORD)
        actor = api.current_actor
        engine, problems = engine_for(api)
        window = _load_main(engine)
        QTest.qWaitForWindowExposed(window)
        api.session.start(actor)  # restarts the clock, as loading takes a moment
        for _ in range(7):
            QTest.qWait(100)
            QTest.keyClick(window, Qt.Key.Key_Shift)  # real input through the real window
        assert api.session.signedIn is True
        QTest.qWait(700)
        assert api.session.signedIn is False
        assert problems.found == []
    finally:
        api.session.end()
        QTest.qWait(SETTLE_MS)
        api.session.shutdown()


def test_restore_returns_a_real_window_to_sign_in_without_warnings(qapp, engine_for, tmp_path):
    api = _fresh_api(tmp_path)
    try:
        api.session.createFirstManager("maria", "Maria", PASSWORD, PASSWORD)
        saved = api.backup.create()["data"]["path"]
        engine, problems = engine_for(api)
        _load_main(engine)
        assert api.backup.restore(saved)["ok"]
        QTest.qWait(SETTLE_MS)
        assert api.session.signedIn is False
        assert problems.found == []
    finally:
        api.session.end()
        QTest.qWait(SETTLE_MS)
        api.session.shutdown()
        api.conn.close()


def test_change_password_dialog_opens_from_the_shell(demo, engine_for):
    api, actors = demo
    api.session.start(actors["manager"])
    engine, problems = engine_for(api)
    window = _load_main(engine)
    expression = QQmlExpression(QQmlEngine.contextForObject(window), window, "changePasswordDialog.open()")
    expression.evaluate()
    assert not expression.hasError(), expression.error().toString()
    QTest.qWait(SETTLE_MS)
    api.session.end()  # signing out must close it
    QTest.qWait(SETTLE_MS)
    assert problems.found == []
