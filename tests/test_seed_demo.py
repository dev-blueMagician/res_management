"""tools/seed_demo.py: consistent data through the real services, and refusing real databases."""

from __future__ import annotations

import importlib.util
import re
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from restaurant.core import Actor, AuthenticationError, Role, calculate_tax_cents, utc_now
from restaurant.db.connection import connect
from restaurant.services import auth, backup, reports, sales

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 3, 20, 21, 30, tzinfo=timezone.utc)


def _load_tool():
    spec = importlib.util.spec_from_file_location("seed_demo", ROOT / "tools" / "seed_demo.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


seed_demo = _load_tool()


@pytest.fixture(scope="module")
def seeded_template(tmp_path_factory) -> Path:
    """Seeded once for this module: the data is read by many tests, and scrypt is slow."""
    path = tmp_path_factory.mktemp("seed") / "template.db"
    with closing(connect(path)) as conn:
        place = seed_demo.seed(conn, days=21, now=NOW)
        seed_demo.check_consistency(conn)
        place_passwords = place.passwords
    path.with_suffix(".passwords").write_text(repr(place_passwords), encoding="utf-8")
    return path


@pytest.fixture
def seeded(seeded_template) -> sqlite3.Connection:
    with closing(connect(seeded_template)) as conn:
        yield conn


def _ledger_problems(conn) -> list:
    return conn.execute(
        "SELECT i.name FROM ingredients i WHERE i.on_hand_milli <> "
        "(SELECT COALESCE(SUM(delta_milli), 0) FROM stock_movements m WHERE m.ingredient_id = i.id)"
    ).fetchall()


def test_one_account_per_role(seeded):
    rows = seeded.execute("SELECT role, COUNT(*) FROM staff GROUP BY role ORDER BY role").fetchall()
    assert [tuple(row) for row in rows] == [("cashier", 1), ("manager", 1), ("stock_clerk", 1)]


def test_a_realistic_restaurant(seeded):
    count = lambda table: seeded.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: E731
    assert count("menu_categories") >= 5
    assert count("menu_items") >= 15
    assert count("ingredients") >= 10
    assert count("recipe_items") >= 20
    assert count("sales") >= 21 * 20
    assert seeded.execute("SELECT COUNT(*) FROM sales WHERE voided_at IS NOT NULL").fetchone()[0] >= 3
    kinds = {row[0] for row in seeded.execute("SELECT DISTINCT movement_type FROM stock_movements")}
    assert kinds == {"received", "adjusted", "wasted", "sold", "void_reversal"}
    methods = {row[0] for row in seeded.execute("SELECT DISTINCT payment_method FROM sales")}
    assert methods == {"cash", "card", "other"}


def test_ledger_matches_on_hand(seeded):
    assert _ledger_problems(seeded) == []
    assert seeded.execute("SELECT COUNT(*) FROM stock_movements").fetchone()[0] > 500


def test_totals_add_up_on_every_sale(seeded):
    for sale in seeded.execute("SELECT * FROM sales").fetchall():
        lines = seeded.execute(
            "SELECT unit_price_cents, quantity, line_total_cents FROM sale_items WHERE sale_id = ?",
            (sale["id"],),
        ).fetchall()
        assert lines, sale["id"]
        assert all(line[2] == line[0] * line[1] for line in lines)
        assert sale["subtotal_cents"] == sum(line[2] for line in lines)
        assert sale["tax_cents"] == calculate_tax_cents(sale["subtotal_cents"], sale["tax_rate_bp"])
        assert sale["total_cents"] == sale["subtotal_cents"] + sale["tax_cents"]


def test_sales_are_backdated_across_the_whole_period_and_none_in_the_future(seeded):
    first, last = seeded.execute("SELECT MIN(created_at), MAX(created_at) FROM sales").fetchone()
    assert first < (NOW - timedelta(days=18)).isoformat()
    assert last <= NOW.isoformat()
    stamps = [
        row[0]
        for table in ("sales", "stock_movements", "audit_log")
        for row in seeded.execute(f"SELECT created_at FROM {table}")
    ]
    assert max(stamps) <= NOW.isoformat()
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", stamp) for stamp in stamps)


def test_a_sales_movements_share_its_time_and_ids_run_in_time_order(seeded):
    assert seeded.execute(
        "SELECT COUNT(*) FROM sales s JOIN stock_movements m ON m.sale_id = s.id "
        "WHERE m.movement_type = 'sold' AND m.created_at <> s.created_at"
    ).fetchone()[0] == 0
    stamps = [row[0] for row in seeded.execute("SELECT created_at FROM sales ORDER BY id")]
    assert stamps == sorted(stamps)


def test_voids_return_exactly_what_the_sale_took(seeded):
    voided = seeded.execute("SELECT id FROM sales WHERE voided_at IS NOT NULL").fetchall()
    assert voided
    for row in voided:
        sold = seeded.execute(
            "SELECT ingredient_id, delta_milli FROM stock_movements "
            "WHERE sale_id = ? AND movement_type = 'sold' ORDER BY ingredient_id",
            (row[0],),
        ).fetchall()
        returned = seeded.execute(
            "SELECT ingredient_id, -delta_milli FROM stock_movements "
            "WHERE sale_id = ? AND movement_type = 'void_reversal' ORDER BY ingredient_id",
            (row[0],),
        ).fetchall()
        assert [tuple(r) for r in sold] == [tuple(r) for r in returned]


def test_old_sales_keep_the_old_price_after_the_price_rise(seeded):
    prices = {
        row[0]
        for row in seeded.execute(
            "SELECT DISTINCT unit_price_cents FROM sale_items WHERE name_as_sold = 'Margherita'"
        )
    }
    assert prices == {1150, 1225}


def test_the_menu_shows_each_state_the_app_marks(seeded):
    no_recipe = seeded.execute(
        "SELECT COUNT(*) FROM menu_items i WHERE NOT EXISTS "
        "(SELECT 1 FROM recipe_items r WHERE r.menu_item_id = i.id)"
    ).fetchone()[0]
    assert no_recipe >= 2
    assert seeded.execute("SELECT COUNT(*) FROM menu_items WHERE active = 0").fetchone()[0] == 1
    assert seeded.execute("SELECT COUNT(*) FROM menu_categories WHERE active = 0").fetchone()[0] == 1


def test_reports_reconcile_over_the_seeded_data(seeded):
    manager = Actor(1, "maria", "Maria Lopez", Role.MANAGER)
    span = (NOW - timedelta(days=30)).date(), (NOW + timedelta(days=1)).date()
    summary = reports.sales_summary(seeded, manager, *span, timezone.utc)
    assert summary["sale_count"] > 0
    assert summary["subtotal_cents"] == sum(
        row["total_cents"] for row in reports.sales_by_item(seeded, manager, *span, timezone.utc)
    )
    assert summary["total_cents"] == sum(
        row["total_cents"] for row in reports.sales_by_payment_method(seeded, manager, *span, timezone.utc)
    )
    assert summary["total_cents"] == sum(
        row["total_cents"] for row in reports.sales_by_cashier(seeded, manager, *span, timezone.utc)
    )


def test_a_full_period_leaves_low_and_negative_stock_to_find(seeded):
    flags = seeded.execute(
        "SELECT SUM(on_hand_milli <= low_milli), SUM(on_hand_milli < 0) FROM ingredients"
    ).fetchone()
    assert flags[0] >= 2
    assert flags[1] >= 1


def test_the_seeded_database_is_a_valid_backup(seeded_template, tmp_path):
    """The strict restore checks are the best independent judge of consistency."""
    target = tmp_path / "restaurant.db"
    connect(target).close()
    backup.restore_backup(target, seeded_template, Actor(1, "maria", "Maria", Role.MANAGER))
    with closing(connect(target)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] > 0


def test_the_data_is_the_same_every_run_apart_from_passwords_and_salts(seeded_template, tmp_path):
    other = tmp_path / "again.db"
    with closing(connect(other)) as conn:
        seed_demo.seed(conn, days=21, now=NOW)

    def contents(path):
        with closing(sqlite3.connect(path)) as conn:
            return {
                table: conn.execute(f"SELECT * FROM {table}").fetchall()
                for table in ("sales", "sale_items", "stock_movements", "ingredients", "menu_items")
            }

    assert contents(other) == contents(seeded_template)


def test_it_restores_the_real_clock_afterwards(tmp_path):
    with closing(connect(tmp_path / "x.db")) as conn:
        seed_demo.seed(conn, days=1, now=NOW)
    for module in (sales, auth):
        assert module.utc_now is utc_now


# --- the command line --------------------------------------------------------------------


def test_main_prints_each_password_it_set_and_they_work(tmp_path, capsys):
    path = tmp_path / "new" / "folder" / "restaurant.db"
    assert seed_demo.main([str(path), "--days", "2"]) == 0
    output = capsys.readouterr().out
    found = re.findall(r"(manager|cashier|stock_clerk)\s+username: (\S+)\s+password: (\S+)", output)
    assert {role for role, _, _ in found} == {"manager", "cashier", "stock_clerk"}
    with closing(connect(path)) as conn:
        for _role, username, password in found:
            assert auth.authenticate(conn, username, password).username == username
            with pytest.raises(AuthenticationError):
                auth.authenticate(conn, username, password + "x")
        stored = "\n".join(
            str(tuple(row))
            for table in ("audit_log", "settings", "staff")
            for row in conn.execute(f"SELECT * FROM {table}")
        )
    assert all(password not in stored for _, _, password in found)


def test_passwords_differ_between_runs(tmp_path, capsys):
    pattern = r"password: (\S+)"
    seed_demo.main([str(tmp_path / "a.db"), "--days", "1"])
    first = re.findall(pattern, capsys.readouterr().out)
    seed_demo.main([str(tmp_path / "b.db"), "--days", "1"])
    second = re.findall(pattern, capsys.readouterr().out)
    assert len(first) == len(second) == 3
    assert not set(first) & set(second)


def test_no_demo_password_or_seed_tool_reference_in_the_app():
    app = ROOT / "src" / "restaurant"
    for path in app.rglob("*"):
        if path.is_file() and path.suffix in (".py", ".qml", ".sql"):
            assert "seed_demo" not in path.read_text(encoding="utf-8"), path


def test_a_database_with_staff_is_refused_and_untouched(tmp_path, capsys):
    path = tmp_path / "restaurant.db"
    with closing(connect(path)) as conn:
        auth.create_first_manager(conn, "owner", "Owner", "real-password")
        before = conn.execute("SELECT * FROM audit_log").fetchall()

    assert seed_demo.main([str(path)]) == seed_demo.EXIT_REFUSED

    captured = capsys.readouterr()
    assert "already has staff" in captured.err
    assert "password" not in captured.out
    with closing(connect(path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM ingredients").fetchone()[0] == 0
        assert [tuple(r) for r in conn.execute("SELECT * FROM audit_log")] == [tuple(r) for r in before]


def test_running_twice_refuses_the_second_time(tmp_path, capsys):
    path = tmp_path / "restaurant.db"
    assert seed_demo.main([str(path), "--days", "1"]) == 0
    capsys.readouterr()
    assert seed_demo.main([str(path), "--days", "1"]) == seed_demo.EXIT_REFUSED


def test_a_file_that_is_not_a_database_is_refused(tmp_path, capsys):
    path = tmp_path / "notes.db"
    path.write_bytes(b"not a database at all" * 100)
    assert seed_demo.main([str(path)]) == seed_demo.EXIT_REFUSED
    assert path.read_bytes() == b"not a database at all" * 100
    assert "Refused" in capsys.readouterr().err


@pytest.mark.parametrize("days", ["0", "-1", "367"])
def test_bad_day_counts_are_refused(tmp_path, days):
    with pytest.raises(SystemExit):
        seed_demo.main([str(tmp_path / "x.db"), "--days", days])
    assert not (tmp_path / "x.db").exists()


def test_check_consistency_notices_a_broken_ledger(tmp_path):
    with closing(connect(tmp_path / "x.db")) as conn:
        seed_demo.seed(conn, days=1, now=NOW)
        seed_demo.check_consistency(conn)
        conn.execute("UPDATE ingredients SET on_hand_milli = on_hand_milli + 1")
        with pytest.raises(RuntimeError, match="ledger"):
            seed_demo.check_consistency(conn)


def test_check_consistency_notices_a_wrong_total(tmp_path):
    with closing(connect(tmp_path / "x.db")) as conn:
        seed_demo.seed(conn, days=2, now=NOW)
        conn.execute(
            "UPDATE sales SET subtotal_cents = subtotal_cents + 1, total_cents = total_cents + 1 "
            "WHERE id = (SELECT MIN(id) FROM sales)"
        )
        with pytest.raises(RuntimeError, match="subtotal"):
            seed_demo.check_consistency(conn)
