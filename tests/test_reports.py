"""Reports and CSV export. Test data is inserted directly with SQL or through sales."""

from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone, tzinfo
from decimal import Decimal

import pytest

from restaurant.core import ConflictError, PermissionDeniedError, ValidationError
from restaurant.services import reports, sales

RANGE = ("2026-06-01", "2026-06-30")
IN_RANGE = "2026-06-15T12:00:00+00:00"
UTC = timezone.utc


class Eastern(tzinfo):
    """US Eastern for 2026, written out because Windows has no zoneinfo database.

    Clocks go 02:00 to 03:00 on 8 March and 02:00 to 01:00 on 1 November.
    """

    def utcoffset(self, dt):
        wall = dt.replace(tzinfo=None, fold=0)
        if datetime(2026, 3, 8, 2) <= wall < datetime(2026, 11, 1, 1):
            return timedelta(hours=-4)
        if datetime(2026, 11, 1, 1) <= wall < datetime(2026, 11, 1, 2):
            return timedelta(hours=-5 if dt.fold else -4)
        return timedelta(hours=-5)

    def dst(self, dt):
        return timedelta(0)

    def tzname(self, dt):
        return "Eastern"


class Havana(tzinfo):
    """Cuba for 2026 spring: midnight itself is skipped, 00:00 to 01:00 on 8 March."""

    def utcoffset(self, dt):
        wall = dt.replace(tzinfo=None, fold=0)
        # A skipped wall time takes the offset from before the change (PEP 495, fold=0).
        return timedelta(hours=-4 if wall >= datetime(2026, 3, 8, 1) else -5)

    def dst(self, dt):
        return timedelta(0)

    def tzname(self, dt):
        return "Havana"


def add_category(conn, name="Mains"):
    return conn.execute("INSERT INTO menu_categories (name) VALUES (?)", (name,)).lastrowid


def add_item(conn, category_id, name="Burger", price=1000):
    return conn.execute(
        "INSERT INTO menu_items (category_id, name, price_cents) VALUES (?, ?, ?)",
        (category_id, name, price),
    ).lastrowid


def add_ingredient(conn, name="Beef", cost=500, low=1000, active=1):
    return conn.execute(
        "INSERT INTO ingredients (name, unit, unit_cost_cents, low_milli, active) "
        "VALUES (?, 'kg', ?, ?, ?)",
        (name, cost, low, active),
    ).lastrowid


def add_movement(conn, ingredient_id, staff_id, delta, kind=None, created_at=IN_RANGE, note=""):
    kind = kind or ("received" if delta > 0 else "wasted")
    return conn.execute(
        "INSERT INTO stock_movements (ingredient_id, movement_type, delta_milli, staff_id, "
        "note, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (ingredient_id, kind, delta, staff_id, note, created_at),
    ).lastrowid


def sale_at(conn, actor, lines, method="cash", when=IN_RANGE):
    sale = sales.record_sale(
        conn, actor, [{"menu_item_id": i, "quantity": q} for i, q in lines], method
    )
    set_time(conn, sale["id"], when)
    return sale


def set_time(conn, sale_id, when):
    if isinstance(when, datetime):
        when = when.astimezone(UTC).isoformat(timespec="seconds")
    conn.execute("UPDATE sales SET created_at = ? WHERE id = ?", (when, sale_id))


@pytest.fixture
def shop(conn, manager, cashier):
    """Mixed sales with a tax rate that rounds, including one voided sale."""
    conn.execute("UPDATE settings SET value = '825' WHERE key = 'tax_rate_bp'")
    mains, drinks = add_category(conn, "Mains"), add_category(conn, "Drinks")
    burger = add_item(conn, mains, "Burger", 1299)
    pasta = add_item(conn, mains, "Pasta", 1575)
    cola = add_item(conn, drinks, "Cola", 333)
    sale_at(conn, cashier, [(burger, 2), (cola, 3)], "cash")
    sale_at(conn, cashier, [(pasta, 1), (cola, 1)], "card")
    sale_at(conn, manager, [(burger, 1)], "other")
    sale_at(conn, manager, [(cola, 7)], "card")
    voided = sale_at(conn, cashier, [(burger, 5), (pasta, 5)], "cash")
    sales.void_sale(conn, manager, voided["id"], "Wrong order")
    return {"burger": burger, "pasta": pasta, "cola": cola, "mains": mains, "voided": voided}


# Sales reports


def test_summary_excludes_voids_and_counts_them_separately(conn, manager, shop):
    summary = reports.sales_summary(conn, manager, *RANGE)

    subtotal = 2 * 1299 + 3 * 333 + 1575 + 333 + 1299 + 7 * 333
    assert summary["sale_count"] == 4
    assert summary["subtotal_cents"] == subtotal
    assert summary["total_cents"] == summary["subtotal_cents"] + summary["tax_cents"]
    assert summary["void_count"] == 1
    assert summary["voided_total_cents"] == shop["voided"]["total_cents"]


def test_empty_range_gives_zeros(conn, manager):
    assert reports.sales_summary(conn, manager, *RANGE) == {
        "sale_count": 0,
        "subtotal_cents": 0,
        "tax_cents": 0,
        "total_cents": 0,
        "void_count": 0,
        "voided_total_cents": 0,
    }
    assert reports.sales_by_item(conn, manager, *RANGE) == []
    assert reports.sales_by_category(conn, manager, *RANGE) == []
    assert reports.sales_by_payment_method(conn, manager, *RANGE) == []
    assert reports.sales_by_cashier(conn, manager, *RANGE) == []


def test_breakdowns_reconcile_with_the_summary(conn, manager, shop):
    summary = reports.sales_summary(conn, manager, *RANGE)
    by_item = reports.sales_by_item(conn, manager, *RANGE)
    by_category = reports.sales_by_category(conn, manager, *RANGE)
    by_payment = reports.sales_by_payment_method(conn, manager, *RANGE)
    by_cashier = reports.sales_by_cashier(conn, manager, *RANGE)

    assert sum(r["total_cents"] for r in by_item) == summary["subtotal_cents"]
    assert sum(r["total_cents"] for r in by_category) == summary["subtotal_cents"]
    assert sum(r["total_cents"] for r in by_payment) == summary["total_cents"]
    assert sum(r["total_cents"] for r in by_cashier) == summary["total_cents"]
    for rows in (by_payment, by_cashier):
        assert sum(r["sale_count"] for r in rows) == summary["sale_count"]
        assert sum(r["tax_cents"] for r in rows) == summary["tax_cents"]
        assert sum(r["void_count"] for r in rows) == summary["void_count"]
        assert sum(r["voided_total_cents"] for r in rows) == summary["voided_total_cents"]


def test_by_item_counts_live_and_voided_quantities_separately(conn, manager, shop):
    rows = {r["name"]: r for r in reports.sales_by_item(conn, manager, *RANGE)}

    assert rows["Burger"]["category"] == "Mains"
    assert rows["Burger"]["quantity"] == 3
    assert rows["Burger"]["total_cents"] == 3 * 1299
    assert rows["Burger"]["voided_quantity"] == 5
    assert rows["Burger"]["voided_total_cents"] == 5 * 1299
    assert rows["Cola"]["quantity"] == 11
    assert rows["Cola"]["voided_quantity"] == 0


def test_by_category(conn, manager, shop):
    rows = {r["category"]: r for r in reports.sales_by_category(conn, manager, *RANGE)}

    assert rows["Mains"]["quantity"] == 4
    assert rows["Mains"]["total_cents"] == 3 * 1299 + 1575
    assert rows["Mains"]["voided_quantity"] == 10
    assert rows["Drinks"]["total_cents"] == 11 * 333


def test_by_payment_method_keeps_a_method_with_only_voids(conn, manager, cashier):
    item = add_item(conn, add_category(conn))
    voided = sale_at(conn, cashier, [(item, 1)], "other")
    sales.void_sale(conn, manager, voided["id"], "Mistake")
    sale_at(conn, cashier, [(item, 2)], "card")

    rows = {r["payment_method"]: r for r in reports.sales_by_payment_method(conn, manager, *RANGE)}

    assert rows["card"]["total_cents"] == 2000
    assert rows["card"]["void_count"] == 0
    assert rows["other"]["sale_count"] == 0
    assert rows["other"]["total_cents"] == 0
    assert rows["other"]["void_count"] == 1


def test_by_cashier_credits_the_cashier_not_the_voiding_manager(conn, manager, cashier, shop):
    rows = {r["staff_id"]: r for r in reports.sales_by_cashier(conn, manager, *RANGE)}

    assert rows[cashier.id]["username"] == cashier.username
    assert rows[cashier.id]["full_name"] == cashier.full_name
    assert rows[cashier.id]["sale_count"] == 2
    assert rows[cashier.id]["void_count"] == 1
    assert rows[manager.id]["sale_count"] == 2
    assert rows[manager.id]["void_count"] == 0


def test_reports_use_as_sold_values_after_menu_changes(conn, manager, shop):
    before = reports.sales_by_item(conn, manager, *RANGE)
    conn.execute("UPDATE menu_items SET name = 'Renamed', price_cents = 1 WHERE id = ?", (shop["burger"],))
    conn.execute("UPDATE menu_categories SET name = 'Moved' WHERE id = ?", (shop["mains"],))

    assert reports.sales_by_item(conn, manager, *RANGE) == before
    assert "Moved" not in {r["category"] for r in reports.sales_by_category(conn, manager, *RANGE)}


def test_renamed_item_shows_as_sold_under_each_name(conn, manager):
    item = add_item(conn, add_category(conn), "Old name", 100)
    sale_at(conn, manager, [(item, 1)])
    conn.execute("UPDATE menu_items SET name = 'New name', price_cents = 200 WHERE id = ?", (item,))
    sale_at(conn, manager, [(item, 1)])

    rows = {r["name"]: r["total_cents"] for r in reports.sales_by_item(conn, manager, *RANGE)}

    assert rows == {"Old name": 100, "New name": 200}


# Date ranges


def test_start_and_end_dates_are_inclusive_at_the_local_boundaries(conn, manager):
    tz = timezone(timedelta(hours=9))
    item = add_item(conn, add_category(conn))
    sale_at(conn, manager, [(item, 1)], when=datetime(2026, 5, 31, 23, 59, 59, tzinfo=tz))
    sale_at(conn, manager, [(item, 2)], when=datetime(2026, 6, 1, 0, 0, 0, tzinfo=tz))
    sale_at(conn, manager, [(item, 4)], when=datetime(2026, 6, 2, 23, 59, 59, tzinfo=tz))
    sale_at(conn, manager, [(item, 8)], when=datetime(2026, 6, 3, 0, 0, 0, tzinfo=tz))

    report = reports.sales_by_item(conn, manager, date(2026, 6, 1), date(2026, 6, 2), tz)

    assert report[0]["quantity"] == 6


def test_timezone_decides_which_day_a_sale_belongs_to(conn, manager):
    item = add_item(conn, add_category(conn))
    sale_at(conn, manager, [(item, 1)], when="2026-06-02T02:00:00+00:00")

    seoul = timezone(timedelta(hours=9))  # 11:00 on 2 June
    new_york = timezone(timedelta(hours=-4))  # 22:00 on 1 June
    day = ("2026-06-02", "2026-06-02")
    assert reports.sales_summary(conn, manager, *day, tz=seoul)["sale_count"] == 1
    assert reports.sales_summary(conn, manager, *day, tz=new_york)["sale_count"] == 0


def test_one_day_range_covers_a_single_day(conn, manager):
    bounds = sales.local_dates_to_utc("2026-06-15", "2026-06-15", UTC)
    assert bounds == ("2026-06-15T00:00:00+00:00", "2026-06-16T00:00:00+00:00")


def test_spring_forward_day_is_23_hours():
    assert sales.local_dates_to_utc("2026-03-08", "2026-03-08", Eastern()) == (
        "2026-03-08T05:00:00+00:00",
        "2026-03-09T04:00:00+00:00",
    )


def test_fall_back_day_is_25_hours():
    assert sales.local_dates_to_utc("2026-11-01", "2026-11-01", Eastern()) == (
        "2026-11-01T04:00:00+00:00",
        "2026-11-02T05:00:00+00:00",
    )


def test_day_after_a_midnight_gap_starts_at_the_clock_change():
    # The 8th begins at the instant clocks jump, 05:00 UTC, and is 23 hours long.
    assert sales.local_dates_to_utc("2026-03-08", "2026-03-08", Havana()) == (
        "2026-03-08T05:00:00+00:00",
        "2026-03-09T04:00:00+00:00",
    )


def test_daylight_saving_boundary_decides_report_membership(conn, manager):
    tz = Eastern()
    item = add_item(conn, add_category(conn))
    sale_at(conn, manager, [(item, 1)], when="2026-03-08T04:59:59+00:00")  # 23:59:59 on the 7th
    sale_at(conn, manager, [(item, 2)], when="2026-03-08T05:00:00+00:00")  # 00:00 EST on the 8th
    sale_at(conn, manager, [(item, 4)], when="2026-03-09T03:59:59+00:00")  # 23:59:59 EDT on the 8th
    sale_at(conn, manager, [(item, 8)], when="2026-03-09T04:00:00+00:00")  # 00:00 EDT on the 9th
    # Using the 8th's opening offset (-5) for its end would wrongly take in the 9th's sale.
    report = reports.sales_by_item(conn, manager, "2026-03-08", "2026-03-08", tz)
    assert report[0]["quantity"] == 6


def test_default_timezone_is_the_system_local_one(conn, manager):
    item = add_item(conn, add_category(conn))
    day_start = datetime(2026, 7, 1).astimezone()  # system local midnight
    next_day = datetime(2026, 7, 2).astimezone()
    sale_at(conn, manager, [(item, 1)], when=day_start - timedelta(seconds=1))
    sale_at(conn, manager, [(item, 2)], when=day_start)
    sale_at(conn, manager, [(item, 4)], when=next_day - timedelta(seconds=1))
    sale_at(conn, manager, [(item, 8)], when=next_day)

    report = reports.sales_by_item(conn, manager, "2026-07-01", "2026-07-01")

    assert report[0]["quantity"] == 6


def test_datetime_input_uses_its_calendar_date():
    assert sales.local_dates_to_utc(datetime(2026, 6, 1, 15, 30), date(2026, 6, 1), UTC) == (
        "2026-06-01T00:00:00+00:00",
        "2026-06-02T00:00:00+00:00",
    )


@pytest.mark.parametrize(
    "start, end",
    [
        ("2026-06-02", "2026-06-01"),
        ("not a date", "2026-06-01"),
        ("2026-02-30", "2026-03-01"),
        (None, "2026-06-01"),
        ("2026-06-01", None),
        (20260601, "2026-06-01"),
        ("2026-06-01", date.max),
    ],
)
def test_invalid_date_ranges_are_rejected(conn, manager, start, end):
    with pytest.raises(ValidationError):
        reports.sales_summary(conn, manager, start, end, UTC)


# Permissions


SALES_REPORTS = [
    reports.sales_summary,
    reports.sales_by_item,
    reports.sales_by_category,
    reports.sales_by_payment_method,
    reports.sales_by_cashier,
]


@pytest.mark.parametrize("report", SALES_REPORTS)
def test_sales_reports_are_manager_only(conn, manager, cashier, stock_clerk, report):
    report(conn, manager, *RANGE)
    for actor in (cashier, stock_clerk):
        with pytest.raises(PermissionDeniedError):
            report(conn, actor, *RANGE)


@pytest.mark.parametrize(
    "report, args",
    [
        (reports.stock_levels, ()),
        (reports.stock_value, ()),
        (reports.low_stock, ()),
        (reports.movement_history, RANGE),
    ],
)
def test_stock_reports_are_for_managers_and_stock_clerks(
    conn, manager, cashier, stock_clerk, report, args
):
    report(conn, manager, *args)
    report(conn, stock_clerk, *args)
    with pytest.raises(PermissionDeniedError):
        report(conn, cashier, *args)


# Stock reports


def test_stock_levels(conn, manager, stock_clerk):
    beef = add_ingredient(conn, "Beef", low=1000)
    salt = add_ingredient(conn, "Salt", low=1000)
    add_ingredient(conn, "Apples", low=0)
    add_movement(conn, beef, manager.id, 5000)
    add_movement(conn, salt, manager.id, 500)

    rows = reports.stock_levels(conn, stock_clerk)

    assert [r["name"] for r in rows] == ["Apples", "Beef", "Salt"]
    by_name = {r["name"]: r for r in rows}
    assert by_name["Beef"]["on_hand_milli"] == 5000
    assert by_name["Beef"]["is_low"] is False
    assert by_name["Salt"]["is_low"] is True
    assert by_name["Apples"]["is_low"] is True  # zero is at the zero threshold


def test_inactive_ingredients_show_only_while_they_hold_stock(conn, manager):
    add_ingredient(conn, "Empty", active=0)
    held = add_ingredient(conn, "Held", active=0)
    add_movement(conn, held, manager.id, 1000)

    assert [r["name"] for r in reports.stock_levels(conn, manager)] == ["Held"]
    assert [r["name"] for r in reports.stock_value(conn, manager)["items"]] == ["Held"]
    assert reports.low_stock(conn, manager) == []


def test_low_stock_is_at_or_below_threshold_with_negatives_flagged(conn, manager):
    above = add_ingredient(conn, "Above", low=1000)
    at = add_ingredient(conn, "At", low=1000)
    below = add_ingredient(conn, "Below", low=1000)
    negative = add_ingredient(conn, "Negative", low=1000)
    gone = add_ingredient(conn, "Inactive", low=1000, active=0)
    add_movement(conn, above, manager.id, 1001)
    add_movement(conn, at, manager.id, 1000)
    add_movement(conn, below, manager.id, 999)
    add_movement(conn, negative, manager.id, -5)
    add_movement(conn, gone, manager.id, 1)

    rows = reports.low_stock(conn, manager)

    assert [r["name"] for r in rows] == ["Negative", "Below", "At"]
    assert [r["is_negative"] for r in rows] == [True, False, False]


@pytest.mark.parametrize(
    "on_hand, cost, expected",
    [
        (1500, 500, 750),
        (1, 500, 1),  # 0.5 cents rounds up
        (1, 499, 0),  # 0.499 cents rounds down
        (3, 500, 2),  # 1.5 cents rounds up
        (333, 1, 0),
        (500, 1, 1),  # exactly half
        (0, 500, 0),
        (-1500, 500, 0),  # negative on-hand is worth nothing
        (10**15, 10**6, 10**18),  # beyond what a float could hold exactly
    ],
)
def test_stock_value_uses_integer_half_up_rounding(conn, manager, on_hand, cost, expected):
    ingredient = add_ingredient(conn, cost=cost)
    if on_hand:
        add_movement(conn, ingredient, manager.id, on_hand)

    report = reports.stock_value(conn, manager)

    assert report["items"][0]["value_cents"] == expected
    assert isinstance(report["items"][0]["value_cents"], int)
    assert report["total_value_cents"] == expected


def test_stock_value_total_is_the_sum_of_rounded_rows(conn, manager):
    for name in ("A", "B", "C"):
        add_movement(conn, add_ingredient(conn, name, cost=500), manager.id, 1)  # 0.5 cents each
    add_movement(conn, add_ingredient(conn, "Short", cost=9000), manager.id, -1000)

    report = reports.stock_value(conn, manager)

    assert [i["value_cents"] for i in report["items"]] == [1, 1, 1, 0]
    assert report["total_value_cents"] == 3


def test_movement_history_lists_range_newest_first_with_names(conn, manager, stock_clerk):
    beef = add_ingredient(conn, "Beef")
    salt = add_ingredient(conn, "Salt")
    tz = timezone(timedelta(hours=-5))
    add_movement(conn, beef, manager.id, 1, created_at="2026-06-01T04:59:59+00:00")
    first = add_movement(conn, beef, manager.id, 2, created_at="2026-06-01T05:00:00+00:00")
    second = add_movement(conn, salt, manager.id, 3, created_at="2026-06-03T04:59:59+00:00", note="Delivery")
    add_movement(conn, beef, manager.id, 4, created_at="2026-06-03T05:00:00+00:00")

    rows = reports.movement_history(conn, stock_clerk, "2026-06-01", "2026-06-02", tz)

    assert [r["id"] for r in rows] == [second, first]
    assert rows[0]["ingredient"] == "Salt"
    assert rows[0]["unit"] == "kg"
    assert rows[0]["movement_type"] == "received"
    assert rows[0]["delta_milli"] == 3
    assert rows[0]["staff_name"] == manager.full_name
    assert rows[0]["note"] == "Delivery"


def test_movement_history_filters_by_ingredient(conn, manager):
    beef = add_ingredient(conn, "Beef")
    salt = add_ingredient(conn, "Salt")
    add_movement(conn, beef, manager.id, 1)
    add_movement(conn, salt, manager.id, 2)

    rows = reports.movement_history(conn, manager, *RANGE, ingredient_id=salt)

    assert [r["ingredient"] for r in rows] == ["Salt"]


def test_movement_history_includes_sale_and_void_movements(conn, manager):
    item = add_item(conn, add_category(conn))
    beef = add_ingredient(conn, "Beef")
    conn.execute(
        "INSERT INTO recipe_items (menu_item_id, ingredient_id, qty_milli) VALUES (?, ?, 100)",
        (item, beef),
    )
    sale = sales.record_sale(conn, manager, [{"menu_item_id": item, "quantity": 1}], "cash")
    sales.void_sale(conn, manager, sale["id"], "Mistake")

    today = datetime.now(UTC).date()
    rows = reports.movement_history(conn, manager, today, today, UTC)

    assert sorted(r["movement_type"] for r in rows) == ["sold", "void_reversal"]
    assert {r["sale_id"] for r in rows} == {sale["id"]}


# CSV export


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as file:
        return list(csv.reader(file))


def test_csv_starts_with_a_utf8_bom_and_keeps_non_ascii(tmp_path):
    path = tmp_path / "report.csv"
    reports.write_csv(path, ["name"], [{"name": "불고기 Crème"}])

    assert path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert read_csv(path) == [["name"], ["불고기 Crème"]]


def test_csv_uses_headers_when_given(tmp_path):
    path = tmp_path / "report.csv"
    reports.write_csv(path, [("name", "Item"), "quantity"], [{"name": "Tea", "quantity": 2}])

    assert read_csv(path) == [["Item", "quantity"], ["Tea", "2"]]


@pytest.mark.parametrize(
    "text, expected",
    [
        ("=1+1", "'=1+1"),
        ("+1", "'+1"),
        ("-1", "'-1"),
        ("@SUM(A1)", "'@SUM(A1)"),
        ('=HYPERLINK("http://x","y")', "'=HYPERLINK(\"http://x\",\"y\")"),
        ("  =1+1", "'  =1+1"),
        ("\t=1+1", "'\t=1+1"),
        ("\r=1+1", "'\r=1+1"),
        ("Fish = good", "Fish = good"),
        ("Half-price", "Half-price"),
        ("", ""),
    ],
)
def test_csv_neutralizes_formulas_in_text_cells(tmp_path, text, expected):
    path = tmp_path / "report.csv"
    reports.write_csv(path, ["name"], [{"name": text}])

    assert read_csv(path)[1] == [expected]


def test_csv_neutralizes_a_real_menu_item_name_from_a_report(conn, manager, tmp_path):
    item = add_item(conn, add_category(conn, "@Mains"), "=cmd|' /C calc'!A0", 500)
    sale_at(conn, manager, [(item, 1)])
    path = tmp_path / "items.csv"

    columns = ["name", "category", "quantity", "total_cents"]
    reports.write_csv(path, columns, reports.sales_by_item(conn, manager, *RANGE))

    assert read_csv(path)[1] == ["'=cmd|' /C calc'!A0", "'@Mains", "1", "5.00"]


@pytest.mark.parametrize(
    "key, value, expected",
    [
        ("total_cents", 1250, "12.50"),
        ("total_cents", 5, "0.05"),
        ("total_cents", 0, "0.00"),
        ("total_cents", -1250, "-12.50"),
        ("total_cents", 123456789, "1234567.89"),
        ("on_hand_milli", 1500, "1.500"),
        ("on_hand_milli", -1, "-0.001"),
        ("on_hand_milli", 0, "0.000"),
        ("quantity", -3, "-3"),
        ("quantity", 3, "3"),
        ("rate", Decimal("-1.50"), "-1.50"),
        ("note", None, ""),
        ("is_low", True, "1"),
        ("is_low", False, "0"),
    ],
)
def test_csv_numbers_are_plain_decimals_never_quoted(tmp_path, key, value, expected):
    path = tmp_path / "report.csv"
    reports.write_csv(path, [key], [{key: value}])

    assert read_csv(path)[1] == [expected]


def test_csv_negative_stock_stays_numeric_from_a_real_report(conn, manager, tmp_path):
    add_movement(conn, add_ingredient(conn, "Beef"), manager.id, -1500)
    path = tmp_path / "stock.csv"

    reports.write_csv(path, ["name", "on_hand_milli"], reports.stock_levels(conn, manager))

    assert read_csv(path)[1] == ["Beef", "-1.500"]


def test_csv_refuses_floats_and_leaves_no_file(tmp_path):
    path = tmp_path / "report.csv"
    with pytest.raises(TypeError):
        reports.write_csv(path, ["total_cents"], [{"total_cents": 1.5}])
    assert not path.exists()


def test_csv_write_failure_is_a_staff_friendly_error(tmp_path):
    with pytest.raises(ConflictError, match="Could not write"):
        reports.write_csv(tmp_path / "missing" / "report.csv", ["a"], [{"a": "b"}])


def test_csv_with_no_rows_still_has_headers(tmp_path):
    path = tmp_path / "report.csv"
    reports.write_csv(path, ["name"], [])
    assert read_csv(path) == [["name"]]


@pytest.mark.parametrize(
    "name", ["restaurant.db", "restaurant.db-wal", "restaurant.log", "report.txt", "report", "x.csv.db"]
)
def test_csv_export_only_ever_writes_csv_files(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(b"precious")
    with pytest.raises(ValidationError, match=r"\.csv"):
        reports.write_csv(path, ["name"], [{"name": "Tea"}])
    assert path.read_bytes() == b"precious"


def test_csv_extension_check_ignores_case(tmp_path):
    path = tmp_path / "REPORT.CSV"
    reports.write_csv(path, ["name"], [{"name": "Tea"}])
    assert path.exists()
