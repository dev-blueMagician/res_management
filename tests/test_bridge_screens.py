"""Bridge slots added for the home, sales and reports screens."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import QCoreApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from restaurant.bridge.api import Api


@pytest.fixture(scope="session")
def qapp() -> QCoreApplication:
    QQuickStyle.setStyle("Basic")
    return QApplication.instance() or QApplication([])


@pytest.fixture
def api(qapp, conn, tmp_path) -> Iterator[Api]:
    bridge = Api(conn, ":memory:", tmp_path / "backups")
    yield bridge
    QTest.qWait(0)  # deliver the session's queued notifications while it still exists
    bridge.session.shutdown()


def test_open_report_navigates_and_hands_over_the_name_once(api, manager):
    api.session.start(manager)
    result = api.reports.openReport("low_stock")
    assert result == {"ok": True, "data": "low_stock"}
    assert api.session.currentSection == "reports"
    assert api.reports.takeRequestedReport() == {"ok": True, "data": "low_stock"}
    assert api.reports.takeRequestedReport() == {"ok": True, "data": ""}


def test_nothing_is_requested_until_asked(api, manager):
    api.session.start(manager)
    assert api.reports.takeRequestedReport() == {"ok": True, "data": ""}


def test_stock_clerk_may_open_stock_reports_but_not_sales_reports(api, stock_clerk):
    api.session.start(stock_clerk)
    assert api.reports.openReport("low_stock")["ok"] is True
    assert api.reports.takeRequestedReport()["data"] == "low_stock"

    assert api.session.goTo("home")["ok"] is True
    QTest.qWait(0)
    refused = api.reports.openReport("sales_summary")
    assert refused["ok"] is False
    assert "permission" in refused["error"]
    assert api.session.currentSection == "home"
    assert api.reports.takeRequestedReport()["data"] == ""


def test_cashier_may_not_open_any_report(api, cashier):
    api.session.start(cashier)
    assert api.reports.openReport("low_stock")["ok"] is False
    assert api.reports.openReport("sales_summary")["ok"] is False
    assert api.session.currentSection == "home"


def test_manager_is_told_about_an_unknown_report(api, manager):
    api.session.start(manager)
    result = api.reports.openReport("no_such_report")
    assert result["ok"] is False
    assert "doesn't exist" in result["error"]
    assert api.session.currentSection == "home"


def test_signed_out_gets_nothing(api):
    assert api.reports.openReport("low_stock")["ok"] is False
    assert api.reports.takeRequestedReport()["ok"] is False
