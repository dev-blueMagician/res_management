"""The signed-in user, what they may use, and the idle lock (FR-S1, FR-S2, FR-S5, FR-S6)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from PySide6.QtCore import Property, QCoreApplication, QEvent, QObject, QTimer, Signal

from restaurant.bridge.base import ROLE_LABELS, BridgeObject, api_slot, check_confirmation
from restaurant.core import Actor, Permission, PermissionDeniedError, ValidationError
from restaurant.services import auth

if TYPE_CHECKING:
    from restaurant.bridge.api import Api

DEFAULT_IDLE_TIMEOUT_MS = 10 * 60 * 1000
IDLE_NOTICE = "You were signed out because the app was idle."

# Deliberately no hover events: Qt Quick synthesizes those while the mouse sits still.
_INPUT_EVENTS = frozenset(
    {
        QEvent.Type.KeyPress,
        QEvent.Type.KeyRelease,
        QEvent.Type.MouseButtonPress,
        QEvent.Type.MouseButtonRelease,
        QEvent.Type.MouseButtonDblClick,
        QEvent.Type.MouseMove,
        QEvent.Type.Wheel,
        QEvent.Type.TouchBegin,
        QEvent.Type.TouchUpdate,
        QEvent.Type.TouchEnd,
    }
)

# key, label, view file, permissions of which any one gives access (none: everyone).
_SECTIONS = (
    ("home", "Home", "HomeView.qml", ()),
    ("sales", "Sales", "SalesView.qml", (Permission.RECORD_SALES,)),
    ("menu", "Menu", "MenuView.qml", (Permission.VIEW_MENU,)),
    ("inventory", "Inventory", "InventoryView.qml", (Permission.MANAGE_STOCK,)),
    (
        "reports",
        "Reports",
        "ReportsView.qml",
        (Permission.VIEW_SALES_REPORTS, Permission.VIEW_STOCK_REPORTS),
    ),
    ("staff", "Staff", "StaffView.qml", (Permission.MANAGE_STAFF,)),
    ("settings", "Settings", "SettingsView.qml", (Permission.MANAGE_SETTINGS,)),
    ("audit", "Audit log", "AuditView.qml", (Permission.VIEW_AUDIT_LOG,)),
)
_DEFAULT_SECTION = "home"
_SIGNED_OUT_USER = {"id": 0, "username": "", "full_name": "", "role": "", "role_text": ""}


class IdleLock(QObject):
    """Application-wide filter that calls ``on_idle`` after a stretch without input."""

    def __init__(self, timeout_ms: int, on_idle: Callable[[], None], parent: QObject) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(on_idle)
        QCoreApplication.instance().installEventFilter(self)

    def start(self) -> None:
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def shutdown(self) -> None:
        self.stop()
        QCoreApplication.instance().removeEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in _INPUT_EVENTS and self._timer.isActive():
            self._timer.start()
        return False


class SessionBridge(BridgeObject):
    changed = Signal()  # signed in or out, or the user's details changed
    sectionChanged = Signal()
    locked = Signal()  # the idle timeout signed the user out

    def __init__(self, api: Api, idle_timeout_ms: int = DEFAULT_IDLE_TIMEOUT_MS) -> None:
        super().__init__(api)
        self._notice = ""
        self._section = _DEFAULT_SECTION
        self._idle = IdleLock(idle_timeout_ms, self._on_idle, self)

    # --- Used from Python -------------------------------------------------------------

    def start(self, actor: Actor) -> None:
        """Begin a session for an authenticated user."""
        self.api.current_actor = actor
        self.api.sales.discard_cart()
        self._notice = ""
        self._section = _DEFAULT_SECTION
        self._idle.start()
        self._notify_later(self.changed, self.sectionChanged)

    def end(self, notice: str = "") -> None:
        """End the session. The cart goes too, since someone else may sign in next."""
        self.api.current_actor = None
        self._idle.stop()
        self.api.sales.discard_cart()
        self._notice = notice
        self._section = _DEFAULT_SECTION
        self._notify_later(self.changed, self.sectionChanged)

    def _notify_later(self, *signals: Signal) -> None:
        """Emit on the next event-loop turn, as the shell destroys the view on these signals.

        The view that called the slot may still be running its handler, which must be
        able to finish and use the result. The new state is already readable.
        """

        def notify() -> None:
            for signal in signals:
                signal.emit()

        QTimer.singleShot(0, self, notify)

    def set_notice(self, notice: str) -> None:
        self._notice = notice
        self.changed.emit()

    def staff_record_changed(self, record: dict) -> None:
        """An Actor keeps its sign-in role, so a change to one's own account must reach it."""
        actor = self.api.current_actor
        if actor is None or record["id"] != actor.id:
            return
        if not record["active"] or record["role"] != actor.role.value:
            self.end("Your account was changed. Please sign in again.")
        elif record["full_name"] != actor.full_name:
            self.api.current_actor = replace(actor, full_name=record["full_name"])
            self.changed.emit()

    def shutdown(self) -> None:
        self._idle.shutdown()

    def _on_idle(self) -> None:
        self.end(IDLE_NOTICE)
        self.locked.emit()

    def _allowed_sections(self) -> list[tuple]:
        actor = self.api.current_actor
        if actor is None:
            return []
        return [
            section
            for section in _SECTIONS
            if not section[3] or any(actor.can(permission) for permission in section[3])
        ]

    # --- QML properties ---------------------------------------------------------------

    def _get_signed_in(self) -> bool:
        return self.api.current_actor is not None

    def _get_needs_first_run(self) -> bool:
        try:
            return not auth.has_accounts(self.conn)
        except sqlite3.Error:
            return False  # the slots report the failure; this only picks a screen

    def _get_user(self) -> dict:
        actor = self.api.current_actor
        if actor is None:
            return dict(_SIGNED_OUT_USER)
        return {
            "id": actor.id,
            "username": actor.username,
            "full_name": actor.full_name,
            "role": actor.role.value,
            "role_text": ROLE_LABELS[actor.role.value],
        }

    def _get_permissions(self) -> dict:
        actor = self.api.current_actor
        return {p.value: actor is not None and actor.can(p) for p in Permission}

    def _get_sections(self) -> list:
        return [
            {"key": key, "label": label, "view": view}
            for key, label, view, _ in self._allowed_sections()
        ]

    def _get_current_section(self) -> str:
        return self._section if self.api.current_actor is not None else ""

    def _get_current_view(self) -> str:
        for key, _, view, _ in self._allowed_sections():
            if key == self._section:
                return view
        return ""

    def _get_notice(self) -> str:
        return self._notice

    signedIn = Property(bool, _get_signed_in, notify=changed)
    needsFirstRun = Property(bool, _get_needs_first_run, notify=changed)
    user = Property("QVariantMap", _get_user, notify=changed)
    permissions = Property("QVariantMap", _get_permissions, notify=changed)
    sections = Property("QVariantList", _get_sections, notify=changed)
    notice = Property(str, _get_notice, notify=changed)
    currentSection = Property(str, _get_current_section, notify=sectionChanged)
    currentView = Property(str, _get_current_view, notify=sectionChanged)

    # --- QML slots --------------------------------------------------------------------

    @api_slot(str, str)
    def signIn(self, username: str, password: str) -> dict:
        if not username.strip() or not password:
            raise ValidationError("Enter your username and password.")
        self.start(auth.authenticate(self.conn, username, password))
        return self._get_user()

    @api_slot(str, str, str, str)
    def createFirstManager(
        self, username: str, full_name: str, password: str, confirm_password: str
    ) -> dict:
        """Create the first account and sign it in (FR-S2)."""
        check_confirmation(password, confirm_password)
        auth.create_first_manager(self.conn, username, full_name, password)
        self.start(auth.authenticate(self.conn, username, password))
        return self._get_user()

    @api_slot()
    def signOut(self) -> None:
        self.end()

    @api_slot(str, str, str)
    def changePassword(self, current: str, new: str, confirm_new: str) -> None:
        actor = self.actor
        check_confirmation(new, confirm_new)
        auth.change_own_password(self.conn, actor, current, new)

    @api_slot(str)
    def goTo(self, section: str) -> str:
        """Show a section, if this user's role may use it."""
        self.actor  # noqa: B018 - raises when nobody is signed in
        if section not in (entry[0] for entry in self._allowed_sections()):
            raise PermissionDeniedError("You don't have permission to do that.")
        self._section = section
        self._notify_later(self.sectionChanged)
        return section
