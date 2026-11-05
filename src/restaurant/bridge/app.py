"""Starting the application: the Qt application, the single-copy lock and startup errors."""

from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
from pathlib import Path

from PySide6.QtCore import (
    QLockFile,
    QMessageLogContext,
    QStandardPaths,
    QtMsgType,
    QTimer,
    QUrl,
    qInstallMessageHandler,
)
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QMessageBox

from restaurant.bridge.api import MAIN_QML, Api, create_engine, destroy_engine
from restaurant.bridge.base import configure_logging
from restaurant.core import AppError
from restaurant.db.connection import connect
from restaurant.services import backup

log = logging.getLogger("restaurant.main")

APP_NAME = "RestaurantManagement"
APP_TITLE = "Restaurant Management"
DATA_DIR_VARIABLE = "RESTAURANT_DATA_DIR"
DATABASE_NAME = "restaurant.db"
BACKUP_DIR_NAME = "backups"
LOCK_NAME = "restaurant.lock"
SMOKE_TEST_MS = 800

EXIT_OK = 0
EXIT_QML_FAILED = 1
EXIT_ALREADY_RUNNING = 3
EXIT_DATA_FOLDER = 4
EXIT_DATABASE = 5


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="restaurant", description=APP_TITLE)
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="load everything, wait briefly and quit, for automated checks",
    )
    args = parser.parse_args(argv)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_TITLE)
    # A custom-styled control needs a style that allows it, set before any QML loads.
    QQuickStyle.setStyle("Basic")

    def fail(message: str, code: int) -> int:
        _show_error(message, interactive=not args.smoke_test)
        return code

    try:
        data_dir = _data_dir()
        data_dir.mkdir(parents=True, exist_ok=True)
        log_handler = configure_logging(data_dir)
    except OSError as error:
        return fail(f"The data folder could not be opened.\n\n{error}", EXIT_DATA_FOLDER)
    _route_diagnostics_to_log()

    lock = QLockFile(str(data_dir / LOCK_NAME))
    lock.setStaleLockTime(0)  # never by age: only a lock whose process has died is stale
    if not lock.tryLock(0):
        return fail(
            f"{APP_TITLE} is already running on this computer.\n\n"
            "Switch to the open window instead of starting it again.",
            EXIT_ALREADY_RUNNING,
        )

    db_path = data_dir / DATABASE_NAME
    backup_dir = data_dir / BACKUP_DIR_NAME
    try:
        conn = _open_database(db_path)
    except (sqlite3.Error, RuntimeError, OSError) as error:
        log.exception("The database could not be opened")
        return fail(
            f"The database could not be opened, so {APP_TITLE} can't start.\n\n"
            f"{error}\n\n"
            f"Database: {db_path}\n"
            f"Dated backups: {backup_dir}\n\n"
            "To recover, close this message and replace the database with the newest "
            f"good backup, renamed to {DATABASE_NAME}. Keep the damaged file somewhere safe.",
            EXIT_DATABASE,
        )

    api = Api(conn, db_path, backup_dir)
    try:
        backup.auto_backup(conn, backup_dir)
    except AppError as error:
        # Not worth stopping the restaurant over, but someone should know.
        log.warning("Automatic backup failed: %s", error)
        api.session.set_notice(f"The automatic backup failed. {error}")

    engine = create_engine(api)
    warnings: list[str] = []

    def record_warnings(errors: list) -> None:
        for error in errors:
            warnings.append(error.toString())
            log.warning("QML: %s", error.toString())

    engine.warnings.connect(record_warnings)
    engine.load(QUrl.fromLocalFile(str(MAIN_QML)))
    if not engine.rootObjects():
        code = fail(
            f"{APP_TITLE} could not load its screens.\n\n" + "\n".join(warnings),
            EXIT_QML_FAILED,
        )
    else:
        if args.smoke_test:
            QTimer.singleShot(SMOKE_TEST_MS, app.quit)
        code = app.exec()
        if args.smoke_test and warnings:
            code = fail("QML warnings:\n" + "\n".join(warnings), EXIT_QML_FAILED)

    destroy_engine(engine)
    api.shutdown()
    lock.unlock()
    logging.getLogger("restaurant").removeHandler(log_handler)
    log_handler.close()
    return code


def _data_dir() -> Path:
    override = os.environ.get(DATA_DIR_VARIABLE)
    if override:
        return Path(override)
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    if not location:
        raise OSError("Windows did not report an application data folder.")
    return Path(location)


def _open_database(path: Path) -> sqlite3.Connection:
    """Open and check the database, so a damaged one never replaces the good backups."""
    conn = connect(path)
    try:
        result = conn.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise sqlite3.DatabaseError(f"The integrity check failed: {result}")
    except BaseException:
        conn.close()
        raise
    return conn


def _show_error(message: str, interactive: bool) -> None:
    if sys.stderr is not None:  # None under pythonw
        print(message, file=sys.stderr)
    log.error(message)
    if interactive and os.environ.get("QT_QPA_PLATFORM") != "offscreen":
        QMessageBox.critical(None, APP_TITLE, message)


def _route_diagnostics_to_log() -> None:
    """Keep Qt warnings and uncaught Python errors: a windowed app has no console."""

    def on_qt_message(kind: QtMsgType, _context: QMessageLogContext, message: str) -> None:
        if kind != QtMsgType.QtDebugMsg and kind != QtMsgType.QtInfoMsg:
            log.warning("Qt: %s", message)

    def on_uncaught(kind: type, value: BaseException, traceback: object) -> None:
        log.critical("Uncaught exception", exc_info=(kind, value, traceback))

    qInstallMessageHandler(on_qt_message)
    sys.excepthook = on_uncaught
