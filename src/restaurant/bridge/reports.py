"""Every report behind one slot pair: run it for the screen, or export it to CSV (FR-R)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import NamedTuple

from restaurant.bridge.base import (
    BridgeObject,
    api_slot,
    local_path,
    read_fields,
    to_qml,
    whole_number,
)
from restaurant.core import Permission, ValidationError, require
from restaurant.services import reports


class Column(NamedTuple):
    key: str  # the raw value, which is what the CSV file gets
    header: str
    display: str = ""  # the preformatted value for the screen; defaults to ``key``
    numeric: bool = False

    def for_qml(self) -> dict:
        return {
            "key": self.key,
            "header": self.header,
            "display": self.display or self.key,
            "numeric": self.numeric,
        }


def _scaled(key: str, header: str) -> Column:
    """A *_cents or *_milli column, shown through its preformatted *_text value."""
    return Column(key, header, f"{key.rsplit('_', 1)[0]}_text", True)


def _count(key: str, header: str) -> Column:
    return Column(key, header, numeric=True)


_SALE_TOTALS = (
    _count("sale_count", "Sales"),
    _scaled("subtotal_cents", "Subtotal"),
    _scaled("tax_cents", "Tax"),
    _scaled("total_cents", "Total"),
    _count("void_count", "Voids"),
    _scaled("voided_total_cents", "Voided total"),
)
_LINE_TOTALS = (
    _count("quantity", "Quantity"),
    _scaled("total_cents", "Total excl. tax"),
    _count("voided_quantity", "Voided quantity"),
    _scaled("voided_total_cents", "Voided total"),
)
_STOCK_COLUMNS = (
    Column("name", "Ingredient"),
    Column("unit", "Unit"),
    _scaled("on_hand_milli", "On hand"),
    _scaled("low_milli", "Low-stock level"),
    Column("status", "Status"),
)


class Report(NamedTuple):
    title: str
    function: Callable
    permission: Permission
    columns: tuple[Column, ...]
    dated: bool = True


_SALES = Permission.VIEW_SALES_REPORTS
_STOCK = Permission.VIEW_STOCK_REPORTS

REPORTS: dict[str, Report] = {
    "sales_summary": Report("Sales summary", reports.sales_summary, _SALES, _SALE_TOTALS),
    "sales_by_item": Report(
        "Sales by item",
        reports.sales_by_item,
        _SALES,
        (Column("name", "Item"), Column("category", "Category"), *_LINE_TOTALS),
    ),
    "sales_by_category": Report(
        "Sales by category",
        reports.sales_by_category,
        _SALES,
        (Column("category", "Category"), *_LINE_TOTALS),
    ),
    "sales_by_payment_method": Report(
        "Sales by payment method",
        reports.sales_by_payment_method,
        _SALES,
        (Column("payment_method", "Payment method", "payment_method_text"), *_SALE_TOTALS),
    ),
    "sales_by_cashier": Report(
        "Sales by cashier",
        reports.sales_by_cashier,
        _SALES,
        (Column("full_name", "Cashier"), Column("username", "Username"), *_SALE_TOTALS),
    ),
    "stock_levels": Report(
        "Stock levels", reports.stock_levels, _STOCK, _STOCK_COLUMNS, dated=False
    ),
    "stock_value": Report(
        "Stock value",
        reports.stock_value,
        _STOCK,
        (
            Column("name", "Ingredient"),
            Column("unit", "Unit"),
            _scaled("on_hand_milli", "On hand"),
            _scaled("unit_cost_cents", "Unit cost"),
            _scaled("value_cents", "Value"),
        ),
        dated=False,
    ),
    "low_stock": Report("Low stock", reports.low_stock, _STOCK, _STOCK_COLUMNS, dated=False),
    "movement_history": Report(
        "Stock movements",
        reports.movement_history,
        _STOCK,
        (
            Column("created_at_text", "Time"),
            Column("ingredient", "Ingredient"),
            Column("movement_type", "Type", "movement_type_text"),
            _scaled("delta_milli", "Change"),
            Column("unit", "Unit"),
            Column("staff_name", "By"),
            Column("sale_id", "Sale"),
            Column("note", "Note"),
        ),
    ),
}


def _with_status(row: dict) -> dict:
    status = "Below zero" if row["is_negative"] else "Low" if row["is_low"] else "OK"
    return {**row, "status": status}


class ReportsBridge(BridgeObject):
    def __init__(self, api) -> None:
        super().__init__(api)
        self._requested_report = ""

    @api_slot(str)
    def openReport(self, name: str) -> str:
        """Go to the Reports section on one report, such as the home screen's low-stock link."""
        actor = self.actor
        report = REPORTS.get(name)
        if report is None:
            raise ValidationError("That report doesn't exist.")
        require(actor, report.permission)
        self._requested_report = name
        self.api.session.goTo("reports")
        return name

    @api_slot()
    def takeRequestedReport(self) -> str:
        """The report openReport asked for, or "". The Reports screen reads it once."""
        self.actor  # noqa: B018 - raises when nobody is signed in
        name, self._requested_report = self._requested_report, ""
        return name

    @api_slot()
    def listReports(self) -> list:
        actor = self.actor
        return [
            {"name": name, "title": report.title, "dated": report.dated}
            for name, report in REPORTS.items()
            if actor.can(report.permission)
        ]

    @api_slot(str, "QVariantMap")
    def run(self, name: str, params: dict) -> dict:
        """``{name, title, columns, rows, summary}``; summary is {} for most reports."""
        report, rows, summary = self._load(name, params)
        return {
            "name": name,
            "title": report.title,
            "columns": [column.for_qml() for column in report.columns],
            "rows": rows,
            "summary": summary,
        }

    @api_slot(str, "QVariantMap", str)
    def exportCsv(self, name: str, params: dict, path: str) -> dict:
        report, rows, summary = self._load(name, params)
        destination = local_path(path)
        columns = [(column.key, column.header) for column in report.columns]
        total_row = [_stock_value_total(report, summary)] if name == "stock_value" else []
        reports.write_csv(destination, columns, [*rows, *total_row])
        return {"path": str(Path(destination)), "row_count": len(rows)}

    @api_slot("QVariant")
    def localDate(self, days_from_today: object) -> str:
        """A local calendar date as YYYY-MM-DD, for date-range defaults."""
        days = whole_number(days_from_today, "number of days")
        return (date.today() + timedelta(days=days)).isoformat()

    def _load(self, name: str, params: dict) -> tuple[Report, list[dict], dict]:
        actor = self.actor
        report = REPORTS.get(name)
        if report is None:
            raise ValidationError("That report doesn't exist.")
        args: list[object] = []
        keywords: dict[str, object] = {}
        if report.dated:
            allowed = ["start_date", "end_date"]
            if name == "movement_history":
                allowed.append("ingredient_id")
            given = read_fields(params, *allowed)
            args = [given.get("start_date", ""), given.get("end_date", "")]
            if given.get("ingredient_id"):
                keywords["ingredient_id"] = whole_number(given["ingredient_id"], "ingredient")
        else:
            read_fields(params)
        result = report.function(self.conn, actor, *args, **keywords)
        return (report, *self._rows_and_summary(name, result))

    def _rows_and_summary(self, name: str, result: object) -> tuple[list[dict], dict]:
        symbol = self.currency_symbol()
        if name == "sales_summary":
            summary = to_qml(result, symbol)
            return [summary], summary
        if name == "stock_value":
            total = {"total_value_cents": result["total_value_cents"]}
            return to_qml(result["items"], symbol), to_qml(total, symbol)
        if name in ("stock_levels", "low_stock"):
            result = [_with_status(row) for row in result]
        return to_qml(result, symbol), {}


def _stock_value_total(report: Report, summary: dict) -> dict:
    row: dict[str, object] = {column.key: None for column in report.columns}
    row["name"] = "Total"
    row["value_cents"] = summary["total_value_cents"]
    return row
