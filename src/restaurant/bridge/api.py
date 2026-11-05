"""The root object QML sees as ``api``, and creation of the QML engine."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PySide6.QtCore import Property, QCoreApplication, QEvent, QObject, Signal
from PySide6.QtQml import QQmlApplicationEngine

from restaurant.bridge.areas import (
    AuditBridge,
    BackupBridge,
    InventoryBridge,
    MenuBridge,
    SettingsBridge,
    StaffBridge,
)
from restaurant.bridge.reports import ReportsBridge
from restaurant.bridge.sales import SalesBridge
from restaurant.bridge.session import DEFAULT_IDLE_TIMEOUT_MS, SessionBridge
from restaurant.core import Actor

QML_DIR = Path(__file__).resolve().parent.parent / "qml"
MAIN_QML = QML_DIR / "Main.qml"


class Api(QObject):
    """Owns the connection and session, and groups the bridge objects by area."""

    salesChanged = Signal()
    stockChanged = Signal()
    menuChanged = Signal()
    staffChanged = Signal()
    restored = Signal()  # a backup replaced the database; everyone has been signed out

    def __init__(
        self,
        conn: sqlite3.Connection,
        db_path: str | Path,
        backup_dir: str | Path,
        idle_timeout_ms: int = DEFAULT_IDLE_TIMEOUT_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.conn = conn
        self.db_path = Path(db_path)
        self.backup_dir = Path(backup_dir)
        self.current_actor: Actor | None = None
        self._sales = SalesBridge(self)
        self._session = SessionBridge(self, idle_timeout_ms)
        self._staff = StaffBridge(self)
        self._settings = SettingsBridge(self)
        self._backup = BackupBridge(self)
        self._menu = MenuBridge(self)
        self._inventory = InventoryBridge(self)
        self._reports = ReportsBridge(self)
        self._audit = AuditBridge(self)

    def shutdown(self) -> None:
        """Stop the idle lock and close whichever connection is current."""
        self._session.shutdown()
        self.conn.close()

    session = Property(QObject, lambda self: self._session, constant=True)
    staff = Property(QObject, lambda self: self._staff, constant=True)
    settings = Property(QObject, lambda self: self._settings, constant=True)
    backup = Property(QObject, lambda self: self._backup, constant=True)
    menu = Property(QObject, lambda self: self._menu, constant=True)
    inventory = Property(QObject, lambda self: self._inventory, constant=True)
    sales = Property(QObject, lambda self: self._sales, constant=True)
    reports = Property(QObject, lambda self: self._reports, constant=True)
    audit = Property(QObject, lambda self: self._audit, constant=True)


def create_engine(api: Api) -> QQmlApplicationEngine:
    """An engine with ``api`` available to every QML file. The caller loads Main.qml."""
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("api", api)
    return engine


def destroy_engine(engine: QQmlApplicationEngine) -> None:
    """Destroy the engine and its window now, while ``api`` still exists.

    Otherwise every binding re-evaluates against a null ``api`` as Python shuts down.
    """
    engine.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
