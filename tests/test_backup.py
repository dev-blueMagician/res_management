"""Backups: WAL contents, pruning that only touches our files, and safe restores."""

from __future__ import annotations

import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest

from restaurant.core import (
    Actor,
    ConflictError,
    PermissionDeniedError,
    Role,
    ValidationError,
    utc_now,
)
from restaurant.db import connection
from restaurant.db.connection import connect
from restaurant.services import audit, backup, inventory, menu, sales

BOSS = Actor(1, "boss", "Boss", Role.MANAGER)
CASHIER = Actor(2, "cash", "Cash", Role.CASHIER)
CLERK = Actor(3, "clerk", "Clerk", Role.STOCK_CLERK)
OLD_NAME = "restaurant-backup-20200101-000000.db"
WELL_FORMED_HASH = "scrypt$32768$8$3$c2FsdHNhbHRzYWx0$aGFzaGhhc2hoYXNoaGFzaA=="


def _add_staff(conn, username, role="manager") -> int:
    return conn.execute(
        "INSERT INTO staff (username, full_name, password_hash, role, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (username, username.title(), WELL_FORMED_HASH, role, utc_now()),
    ).lastrowid


def _usernames(path) -> set[str]:
    with closing(sqlite3.connect(path)) as conn:
        return {row[0] for row in conn.execute("SELECT username FROM staff")}


def _names(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.iterdir())


@pytest.fixture
def db_path(tmp_path) -> Path:
    path = tmp_path / "data" / "restaurant.db"
    path.parent.mkdir()
    with closing(connect(path)) as conn:
        _add_staff(conn, "boss")
        _add_staff(conn, "cash", "cashier")
        _add_staff(conn, "clerk", "stock_clerk")
    return path


@pytest.fixture
def backup_dir(tmp_path) -> Path:
    return tmp_path / "backups"


@pytest.fixture
def good_backup(db_path, backup_dir) -> Path:
    """A backup made while only the three original staff exist."""
    with closing(connect(db_path)) as conn:
        return Path(backup.auto_backup(conn, backup_dir)["path"])


def _add_staff_to_file(path, username):
    with closing(connect(path)) as conn:
        _add_staff(conn, username)


# --- making backups ---


def test_create_backup_produces_a_dated_usable_file(conn, manager, backup_dir):
    result = backup.create_backup(conn, manager, backup_dir)
    path = Path(result["path"])
    assert path.parent == backup_dir
    assert path.name.startswith(f"restaurant-backup-{datetime.now():%Y%m%d}-")
    assert _usernames(path) == {"manager"}


def test_create_backup_creates_the_folder(conn, manager, tmp_path):
    result = backup.create_backup(conn, manager, tmp_path / "a" / "b")
    assert Path(result["path"]).is_file()


def test_create_backup_is_audited(conn, manager, backup_dir):
    path = Path(backup.create_backup(conn, manager, backup_dir)["path"])
    row = conn.execute("SELECT * FROM audit_log").fetchone()
    assert row["action"] == "backup.created"
    assert row["staff_id"] == manager.id
    assert row["details"] == path.name


def test_only_managers_can_create_a_backup(conn, cashier, stock_clerk, backup_dir):
    for actor in (cashier, stock_clerk):
        with pytest.raises(PermissionDeniedError):
            backup.create_backup(conn, actor, backup_dir)
    assert not backup_dir.exists()


def test_auto_backup_needs_no_actor_and_writes_nothing_to_the_database(conn, backup_dir):
    path = Path(backup.auto_backup(conn, backup_dir)["path"])
    assert path.is_file()
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_backup_includes_contents_still_in_the_wal(db_path, backup_dir):
    wal = db_path.with_name(db_path.name + "-wal")
    with closing(connect(db_path)) as conn:
        _add_staff(conn, "walonly")
        assert wal.stat().st_size > 0  # not checkpointed: the main file lacks this row
        path = Path(backup.auto_backup(conn, backup_dir)["path"])
        assert "walonly" in _usernames(path)


def test_backup_is_one_self_contained_file(db_path, backup_dir):
    with closing(connect(db_path)) as conn:
        path = Path(backup.auto_backup(conn, backup_dir)["path"])
    assert _names(backup_dir) == [path.name]
    with closing(sqlite3.connect(path)) as copy:
        assert copy.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert copy.execute("PRAGMA user_version").fetchone()[0] >= 1
    assert _names(backup_dir) == [path.name]


def test_two_backups_in_the_same_second_both_survive(conn, backup_dir, monkeypatch):
    class FrozenClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 1, 2, 3, 4, 5)

    monkeypatch.setattr(backup, "datetime", FrozenClock)
    first = Path(backup.auto_backup(conn, backup_dir)["path"])
    second = Path(backup.auto_backup(conn, backup_dir)["path"])
    third = Path(backup.auto_backup(conn, backup_dir)["path"])
    assert first.name == "restaurant-backup-20260102-030405.db"
    assert second.name == "restaurant-backup-20260102-030405-1.db"
    assert third.name == "restaurant-backup-20260102-030405-2.db"
    assert first.is_file() and second.is_file() and third.is_file()


def test_unwritable_location_is_a_clear_error_and_nothing_is_audited(conn, manager, tmp_path):
    not_a_folder = tmp_path / "file.txt"
    not_a_folder.write_text("x")
    with pytest.raises(ValidationError, match="could not be saved"):
        backup.create_backup(conn, manager, not_a_folder)
    assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


def test_failed_backup_deletes_nothing_and_leaves_no_partial_file(conn, backup_dir, monkeypatch):
    backup_dir.mkdir()
    for day in range(1, 9):
        (backup_dir / f"restaurant-backup-202001{day:02d}-000000.db").write_bytes(b"old")
    before = _names(backup_dir)

    def fail(source, destination):
        destination.write_bytes(b"partial")
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(backup, "_copy_database", fail)
    with pytest.raises(ValidationError):
        backup.auto_backup(conn, backup_dir)
    assert _names(backup_dir) == before


# --- pruning ---


def _fake_backups(directory: Path, count: int) -> list[str]:
    directory.mkdir(exist_ok=True)
    names = [f"restaurant-backup-202001{day:02d}-000000.db" for day in range(1, count + 1)]
    for name in names:
        (directory / name).write_bytes(b"old")
    return names


def test_seven_newest_backups_are_kept(conn, backup_dir):
    names = _fake_backups(backup_dir, 9)
    newest = Path(backup.auto_backup(conn, backup_dir)["path"]).name
    assert _names(backup_dir) == sorted([*names[3:], newest])


def test_fewer_than_keep_deletes_nothing(conn, backup_dir):
    names = _fake_backups(backup_dir, 5)
    newest = Path(backup.auto_backup(conn, backup_dir)["path"]).name
    assert _names(backup_dir) == sorted([*names, newest])


def test_manual_backup_prunes_too(conn, manager, backup_dir):
    names = _fake_backups(backup_dir, 8)
    newest = Path(backup.create_backup(conn, manager, backup_dir)["path"]).name
    assert _names(backup_dir) == sorted([*names[2:], newest])


def test_keep_can_be_changed(conn, backup_dir):
    names = _fake_backups(backup_dir, 4)
    newest = Path(backup.auto_backup(conn, backup_dir, keep=2)["path"]).name
    assert _names(backup_dir) == sorted([names[-1], newest])


def test_numbered_backups_sort_after_their_base_name(conn, backup_dir):
    backup_dir.mkdir()
    base = "restaurant-backup-20200101-000000"
    for name in (f"{base}.db", f"{base}-1.db", f"{base}-2.db", f"{base}-10.db"):
        (backup_dir / name).write_bytes(b"old")
    newest = Path(backup.auto_backup(conn, backup_dir, keep=3)["path"]).name
    assert _names(backup_dir) == sorted([f"{base}-2.db", f"{base}-10.db", newest])


def test_pruning_only_deletes_files_matching_our_pattern(conn, backup_dir):
    names = _fake_backups(backup_dir, 8)
    others = [
        "menu.db",
        "notes.txt",
        "restaurant-backup.db",
        "restaurant-backup-latest.db",
        "restaurant-backup-2020-01-01.db",
        "restaurant-backup-20200101-000000.db.bak",
        "restaurant-backup-20200101-000000.DB.txt",
        "my-restaurant-backup-20200101-000000.db",
        "restaurant-backup-20200101-000000-x.db",
        "restaurant-pre-restore-20200101-000000.db",
    ]
    for name in others:
        (backup_dir / name).write_bytes(b"precious")
    folder = backup_dir / "restaurant-backup-20000101-000000.db"
    folder.mkdir()
    newest = Path(backup.auto_backup(conn, backup_dir)["path"]).name
    expected = sorted([*names[2:], newest, *others, folder.name])
    assert _names(backup_dir) == expected
    assert all((backup_dir / name).read_bytes() == b"precious" for name in others)


def test_clock_set_back_never_deletes_the_new_backup(conn, backup_dir):
    backup_dir.mkdir()
    for year in range(2090, 2100):
        (backup_dir / f"restaurant-backup-{year}0101-000000.db").write_bytes(b"future")
    path = Path(backup.auto_backup(conn, backup_dir)["path"])
    assert path.is_file()
    assert len(_names(backup_dir)) == 7


def test_a_file_that_cannot_be_deleted_does_not_fail_the_backup(conn, backup_dir, monkeypatch):
    names = _fake_backups(backup_dir, 8)
    real_unlink = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name == names[0]:
            raise PermissionError("in use")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    path = Path(backup.auto_backup(conn, backup_dir)["path"])
    assert path.is_file()
    assert (backup_dir / names[0]).exists()
    assert not (backup_dir / names[1]).exists()


# --- restoring ---


def test_restore_brings_back_the_old_data_and_connect_works(db_path, good_backup):
    _add_staff_to_file(db_path, "newer")
    assert "newer" in _usernames(db_path)
    result = backup.restore_backup(db_path, good_backup, BOSS)
    with closing(connect(db_path)) as conn:
        names = {row[0] for row in conn.execute("SELECT username FROM staff")}
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert names == {"boss", "cash", "clerk"}
    assert result["restored_from"] == str(good_backup)


def test_restore_does_not_change_the_backup_file(db_path, good_backup):
    before = good_backup.read_bytes()
    backup.restore_backup(db_path, good_backup, BOSS)
    assert good_backup.read_bytes() == before
    assert _names(good_backup.parent) == [good_backup.name]


def test_restore_makes_a_safety_copy_of_the_current_database(db_path, good_backup):
    _add_staff_to_file(db_path, "newer")
    result = backup.restore_backup(db_path, good_backup, BOSS)
    safety = Path(result["safety_copy"])
    assert safety.parent == db_path.parent
    assert "newer" in _usernames(safety)
    assert "newer" not in _usernames(db_path)


def test_safety_copies_are_never_counted_or_pruned_as_backups(db_path, good_backup):
    safety = Path(backup.restore_backup(db_path, good_backup, BOSS)["safety_copy"])
    assert backup._BACKUP_NAME.fullmatch(safety.name) is None


def test_restore_leaves_no_temporary_files(db_path, good_backup):
    result = backup.restore_backup(db_path, good_backup, BOSS)
    assert _names(db_path.parent) == sorted(
        [db_path.name, Path(result["safety_copy"]).name]
    )


def _crash_with_rows_in_wal(tmp_path: Path) -> Path:
    """A database as left by a crash: committed rows only in its -wal, beside a -shm."""
    live = tmp_path / "live.db"
    crashed = tmp_path / "crashed" / "restaurant.db"
    crashed.parent.mkdir()
    with closing(connect(live)) as conn:
        _add_staff(conn, "boss")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        _add_staff(conn, "walonly")
        for suffix in ("", "-wal", "-shm"):
            shutil.copyfile(f"{live}{suffix}", f"{crashed}{suffix}")
    return crashed


def test_restore_removes_stale_wal_and_shm_and_the_safety_copy_has_their_rows(
    tmp_path, good_backup
):
    crashed = _crash_with_rows_in_wal(tmp_path)
    assert Path(f"{crashed}-wal").stat().st_size > 0
    result = backup.restore_backup(crashed, good_backup, BOSS)
    assert not Path(f"{crashed}-wal").exists()
    assert not Path(f"{crashed}-shm").exists()
    assert "walonly" in _usernames(result["safety_copy"])
    with closing(connect(crashed)) as conn:
        names = {row[0] for row in conn.execute("SELECT username FROM staff")}
    assert names == {"boss", "cash", "clerk"}


def test_stale_wal_from_a_failed_earlier_restore_is_not_replayed(db_path, good_backup):
    # Sidecars left over beside the temporary copy must not reach the restored data.
    temp = db_path.with_name(db_path.name + ".restore-tmp")
    Path(f"{temp}-wal").write_bytes(b"junk" * 1000)
    Path(f"{temp}-shm").write_bytes(b"junk" * 1000)
    backup.restore_backup(db_path, good_backup, BOSS)
    assert not Path(f"{temp}-wal").exists()
    assert _usernames(db_path) == {"boss", "cash", "clerk"}


def test_restore_is_recorded_in_the_restored_database(db_path, good_backup):
    backup.restore_backup(db_path, good_backup, BOSS)
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM audit_log ORDER BY id DESC").fetchone()
    assert row["action"] == "backup.restored"
    assert row["staff_id"] == BOSS.id
    assert good_backup.name in row["details"]
    assert "boss" in row["details"]


def test_restore_by_someone_who_is_not_in_the_backup(db_path, good_backup):
    _add_staff_to_file(db_path, "newer")
    newer = Actor(4, "newer", "Newer", Role.MANAGER)
    backup.restore_backup(db_path, good_backup, newer)
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM audit_log ORDER BY id DESC").fetchone()
        foreign_key_problems = conn.execute("PRAGMA foreign_key_check").fetchall()
    assert row["staff_id"] is None
    assert "newer" in row["details"]
    assert foreign_key_problems == []


def test_restore_when_the_id_belongs_to_someone_else_in_the_backup(db_path, good_backup):
    backup.restore_backup(db_path, good_backup, Actor(2, "impostor", "I", Role.MANAGER))
    with closing(connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM audit_log ORDER BY id DESC").fetchone()
    assert row["staff_id"] is None


@pytest.mark.parametrize("actor", [CASHIER, CLERK], ids=["cashier", "clerk"])
def test_only_managers_can_restore(db_path, good_backup, actor):
    before = db_path.read_bytes()
    with pytest.raises(PermissionDeniedError):
        backup.restore_backup(db_path, good_backup, actor)
    assert db_path.read_bytes() == before
    assert _names(db_path.parent) == [db_path.name]


def test_restore_works_when_the_database_file_is_missing(db_path, good_backup):
    db_path.unlink()
    result = backup.restore_backup(db_path, good_backup, BOSS)
    assert result["safety_copy"] is None
    assert _usernames(db_path) == {"boss", "cash", "clerk"}


def test_restore_replaces_a_damaged_database_and_keeps_its_raw_bytes(db_path, good_backup):
    db_path.write_bytes(b"this is not a database at all, sorry" * 200)
    damaged = db_path.read_bytes()
    result = backup.restore_backup(db_path, good_backup, BOSS)
    assert Path(result["safety_copy"]).read_bytes() == damaged
    assert _usernames(db_path) == {"boss", "cash", "clerk"}


def test_restore_over_a_damaged_database_removes_its_sidecars(tmp_path, good_backup):
    # SQLite can't open this database, so closing it won't have cleared them.
    crashed = _crash_with_rows_in_wal(tmp_path)
    wal = Path(f"{crashed}-wal")
    wal_bytes = wal.read_bytes()
    crashed.write_bytes(b"this is not a database at all, sorry" * 200)
    result = backup.restore_backup(crashed, good_backup, BOSS)
    assert not wal.exists()
    assert not Path(f"{crashed}-shm").exists()
    assert Path(f'{result["safety_copy"]}-wal').read_bytes() == wal_bytes
    assert _usernames_with_wal(crashed) == {"boss", "cash", "clerk"}


# --- bad backups leave everything untouched ---


def _garbage(path: Path) -> None:
    path.write_bytes(b"definitely not sqlite" * 500)


def _empty(path: Path) -> None:
    path.write_bytes(b"")


def _truncated(path: Path) -> None:
    path.write_bytes(path.read_bytes()[:6000])


def _damaged_page(path: Path) -> None:
    data = bytearray(path.read_bytes())
    page_size = int.from_bytes(data[16:18], "big")
    data[page_size : page_size * 3] = b"\xff" * (page_size * 2)
    path.write_bytes(bytes(data))


def _damaged_empty_index(path: Path) -> None:
    """Damage only integrity_check notices: it reports a row, and nothing else fails."""
    with closing(sqlite3.connect(path)) as conn:
        root = conn.execute(
            "SELECT rootpage FROM sqlite_master WHERE name = 'idx_sale_items_sale_id'"
        ).fetchone()[0]
    data = bytearray(path.read_bytes())
    page_size = int.from_bytes(data[16:18], "big")
    data[page_size * (root - 1) : page_size * root] = bytes(page_size)
    path.write_bytes(bytes(data))


def _newer_version(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA user_version = 9999")


def _version_zero(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA user_version = 0")


def _missing_table(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TABLE sale_items")
        conn.commit()


def _other_sqlite_database(path: Path) -> None:
    path.unlink()
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("CREATE TABLE photos (id INTEGER)")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()


def _wrong_columns(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TABLE audit_log")
        conn.execute("CREATE TABLE audit_log (id INTEGER PRIMARY KEY)")
        conn.commit()


def _edit(path: Path, *statements: str) -> None:
    """Change a backup the way a person with a SQLite tool could: no rules enforced."""
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        for statement in statements:
            conn.execute(statement)


def _wrong_columns_in(table: str):
    def spoil(path: Path) -> None:
        _edit(path, f"DROP TABLE {table}", f"CREATE TABLE {table} (x)")

    spoil.__name__ = f"_wrong_columns_in_{table}"
    return spoil


def _extra_column(path: Path) -> None:
    _edit(path, "ALTER TABLE sales ADD COLUMN note TEXT")


def _extra_table(path: Path) -> None:
    _edit(path, "CREATE TABLE extra (x)")


def _extra_index(path: Path) -> None:
    _edit(path, "CREATE INDEX idx_extra ON staff (full_name)")


def _view_instead_of_table(path: Path) -> None:
    _edit(path, "DROP TABLE settings", "CREATE VIEW settings AS SELECT 'a' AS key, 'b' AS value")


def _stock_triggers_dropped(path: Path) -> None:
    _edit(
        path,
        "DROP TRIGGER trg_stock_movements_apply",
        "DROP TRIGGER trg_stock_movements_no_update",
        "DROP TRIGGER trg_stock_movements_no_delete",
    )


def _one_stock_trigger_replaced(path: Path) -> None:
    _edit(
        path,
        "DROP TRIGGER trg_stock_movements_apply",
        "CREATE TRIGGER trg_stock_movements_apply AFTER INSERT ON stock_movements "
        "BEGIN UPDATE ingredients SET on_hand_milli = 0; END",
    )


def _privilege_trigger_added(path: Path) -> None:
    _edit(
        path,
        "CREATE TRIGGER evil AFTER INSERT ON audit_log BEGIN UPDATE staff SET role = 'manager'; END",
    )


def _orphan_sale_item(path: Path) -> None:
    _edit(
        path,
        "INSERT INTO sale_items (sale_id, menu_item_id, name_as_sold, category_as_sold, "
        "unit_price_cents, quantity, line_total_cents) VALUES (999, 999, 'a', 'b', 1, 1, 1)",
    )


def _on_hand_differs_from_ledger(path: Path) -> None:
    _edit(path, "INSERT INTO ingredients (name, unit, on_hand_milli) VALUES ('Beef', 'kg', 123456)")


def _with_trigger_lifted(path: Path, trigger: str, *statements: str) -> None:
    """Plant a bad value, leaving the schema exactly as it was: only the value is wrong."""
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (trigger,)).fetchone()[0]
    _edit(path, f"DROP TRIGGER {trigger}", *statements, sql)


def _float_price(path: Path) -> None:
    _with_trigger_lifted(
        path,
        "trg_menu_items_whole_numbers_insert",
        "INSERT INTO menu_categories (id, name) VALUES (1, 'C')",
        "INSERT INTO menu_items (category_id, name, price_cents) VALUES (1, 'floaty', 12.5)",
    )


def _text_low_stock_level(path: Path) -> None:
    _with_trigger_lifted(
        path,
        "trg_ingredients_whole_numbers_insert",
        "INSERT INTO ingredients (name, unit, low_milli) VALUES ('Beef', 'kg', 'abc')",
    )


def _blob_in_a_text_column(path: Path) -> None:
    _edit(path, "UPDATE staff SET full_name = CAST('x' AS BLOB)")


def _broken_check_constraint(path: Path) -> None:
    with closing(sqlite3.connect(path, isolation_level=None)) as conn:
        conn.execute("PRAGMA ignore_check_constraints = ON")
        conn.execute("UPDATE staff SET active = 7 WHERE username = 'clerk'")


def _everyone_inactive(path: Path) -> None:
    _edit(path, "UPDATE staff SET active = 0")


def _only_active_manager_gone(path: Path) -> None:
    _edit(path, "UPDATE staff SET role = 'cashier' WHERE role = 'manager'")


def _manager_hash_unusable(path: Path) -> None:
    _edit(path, "UPDATE staff SET password_hash = 'x'")


def _no_staff_at_all(path: Path) -> None:
    _edit(path, "DELETE FROM staff")


def _no_settings(path: Path) -> None:
    _edit(path, "DELETE FROM settings")


def _setting_missing(key: str):
    def spoil(path: Path) -> None:
        _edit(path, f"DELETE FROM settings WHERE key = '{key}'")

    spoil.__name__ = f"_setting_missing_{key}"
    return spoil


def _bad_tax_rate(value: str):
    def spoil(path: Path) -> None:
        _edit(path, f"UPDATE settings SET value = '{value}' WHERE key = 'tax_rate_bp'")

    spoil.__name__ = f"_bad_tax_rate_{value}"
    return spoil


def _blank_restaurant_name(path: Path) -> None:
    _edit(path, "UPDATE settings SET value = '  ' WHERE key = 'restaurant_name'")


@pytest.mark.parametrize(
    "spoil",
    [
        _wrong_columns_in("sales"),
        _wrong_columns_in("sale_items"),
        _wrong_columns_in("menu_items"),
        _wrong_columns_in("recipe_items"),
        _wrong_columns_in("ingredients"),
        _wrong_columns_in("stock_movements"),
        _extra_column,
        _extra_table,
        _extra_index,
        _view_instead_of_table,
        _stock_triggers_dropped,
        _one_stock_trigger_replaced,
        _privilege_trigger_added,
        _orphan_sale_item,
        _on_hand_differs_from_ledger,
        _float_price,
        _text_low_stock_level,
        _blob_in_a_text_column,
        _broken_check_constraint,
        _everyone_inactive,
        _only_active_manager_gone,
        _manager_hash_unusable,
        _no_staff_at_all,
        _no_settings,
        _setting_missing("restaurant_name"),
        _setting_missing("currency_symbol"),
        _setting_missing("tax_rate_bp"),
        _bad_tax_rate("abc"),
        _bad_tax_rate("-1"),
        _bad_tax_rate("10001"),
        _bad_tax_rate("8.25"),
        _blank_restaurant_name,
    ],
    ids=lambda spoil: spoil.__name__.lstrip("_"),
)
def test_tampered_backup_is_refused_and_changes_nothing(db_path, good_backup, spoil):
    _add_staff_to_file(db_path, "newer")
    spoil(good_backup)
    before = _names(db_path.parent)

    with pytest.raises(ValidationError):
        backup.restore_backup(db_path, good_backup, BOSS)

    assert _names(db_path.parent) == before  # no safety copy, no temporary file
    assert "newer" in _usernames(db_path)


def test_backup_with_real_data_restores_and_stays_consistent(db_path, backup_dir):
    with closing(connect(db_path)) as conn:
        cashier = Actor(2, "cash", "Cash", Role.CASHIER)
        category = menu.create_category(conn, BOSS, "Mains")
        item = menu.create_item(conn, BOSS, category["id"], "Burger", 1250)
        beef = inventory.create_ingredient(conn, BOSS, "Beef", "kg", 900, 1000)
        inventory.receive_stock(conn, BOSS, beef["id"], 5000, "Delivery")
        menu.set_recipe(conn, BOSS, item["id"], [{"ingredient_id": beef["id"], "qty_milli": 200}])
        line = {"menu_item_id": item["id"], "quantity": 2}
        sales.record_sale(conn, cashier, [line], "cash")
        voided = sales.record_sale(conn, cashier, [line], "card")
        sales.void_sale(conn, BOSS, voided["id"], "Wrong order")
        saved = Path(backup.create_backup(conn, BOSS, backup_dir)["path"])

    backup.restore_backup(db_path, saved, BOSS)

    with closing(connect(db_path)) as conn:
        assert conn.execute("SELECT on_hand_milli FROM ingredients").fetchone()[0] == 4600
        assert conn.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 2
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def _schema_objects(conn) -> list[tuple]:
    return [
        tuple(row)
        for row in conn.execute("SELECT type, name, tbl_name FROM sqlite_master ORDER BY name")
    ]


def test_a_version_1_backup_is_checked_against_version_1_then_upgraded(db_path, tmp_path):
    old = tmp_path / "version1.db"
    with closing(sqlite3.connect(old, isolation_level=None)) as conn:
        connection.migrate(conn, target=1)
        _add_staff(conn, "oldboss")

    backup.restore_backup(db_path, old, BOSS)

    assert _usernames(db_path) == {"oldboss"}
    with closing(sqlite3.connect(db_path)) as conn, closing(connect(":memory:")) as fresh:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == backup._latest_schema_version()
        assert _schema_objects(conn) == _schema_objects(fresh)


def test_a_version_1_backup_holding_a_float_is_refused(db_path, tmp_path):
    old = tmp_path / "version1.db"
    with closing(sqlite3.connect(old, isolation_level=None)) as conn:
        connection.migrate(conn, target=1)
        _add_staff(conn, "oldboss")
        category = conn.execute("INSERT INTO menu_categories (name) VALUES ('C')").lastrowid
        conn.execute(
            "INSERT INTO menu_items (category_id, name, price_cents) VALUES (?, 'floaty', 12.5)",
            (category,),
        )
    before = db_path.read_bytes()
    with pytest.raises(ValidationError):
        backup.restore_backup(db_path, old, BOSS)
    assert db_path.read_bytes() == before


def test_a_refused_backup_says_why_in_plain_words(db_path, good_backup):
    _everyone_inactive(good_backup)
    with pytest.raises(ValidationError, match="active manager"):
        backup.restore_backup(db_path, good_backup, BOSS)


@pytest.mark.parametrize(
    "spoil",
    [
        _garbage,
        _empty,
        _truncated,
        _damaged_page,
        _damaged_empty_index,
        _newer_version,
        _version_zero,
        _missing_table,
        _other_sqlite_database,
        _wrong_columns,
    ],
)
def test_bad_backup_is_refused_and_changes_nothing(tmp_path, good_backup, spoil):
    crashed = _crash_with_rows_in_wal(tmp_path)
    spoil(good_backup)
    spoiled = good_backup.read_bytes()
    files = [crashed, Path(f"{crashed}-wal"), Path(f"{crashed}-shm")]
    before = [file.read_bytes() for file in files]

    with pytest.raises(ValidationError):
        backup.restore_backup(crashed, good_backup, BOSS)

    assert [file.read_bytes() for file in files] == before
    assert _names(crashed.parent) == sorted(file.name for file in files)  # no safety copy, no temp
    assert good_backup.read_bytes() == spoiled
    assert _usernames_with_wal(crashed) == {"boss", "walonly"}


def _usernames_with_wal(path: Path) -> set[str]:
    with closing(connect(path)) as conn:
        return {row[0] for row in conn.execute("SELECT username FROM staff")}


def test_damage_fixtures_exercise_both_ways_integrity_check_can_fail(good_backup, tmp_path):
    # Guards the test above: one kind of damage raises, the other reports a row.
    raising = tmp_path / "raising.db"
    shutil.copyfile(good_backup, raising)
    _damaged_page(raising)
    with closing(sqlite3.connect(raising)) as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("PRAGMA integrity_check").fetchall()
    reporting = tmp_path / "reporting.db"
    shutil.copyfile(good_backup, reporting)
    _damaged_empty_index(reporting)
    with closing(sqlite3.connect(reporting)) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"


@pytest.mark.parametrize("make_path", [lambda folder: folder / "missing.db", lambda folder: folder])
def test_unreadable_backup_path_is_refused(db_path, tmp_path, make_path):
    before = db_path.read_bytes()
    with pytest.raises(ValidationError, match="could not be read"):
        backup.restore_backup(db_path, make_path(tmp_path), BOSS)
    assert db_path.read_bytes() == before
    assert _names(db_path.parent) == [db_path.name]


def test_newer_backup_message_says_why(db_path, good_backup):
    _newer_version(good_backup)
    with pytest.raises(ValidationError, match="newer version"):
        backup.restore_backup(db_path, good_backup, BOSS)


def test_failure_to_make_safety_copy_cancels_the_restore(db_path, good_backup, monkeypatch):
    _add_staff_to_file(db_path, "newer")
    before = db_path.read_bytes()

    def fail(source, destination):
        raise sqlite3.OperationalError("disk full")

    def fail_copy(*args):
        raise OSError("disk full")

    monkeypatch.setattr(backup, "_copy_database", fail)
    monkeypatch.setattr(backup.shutil, "copy2", fail_copy)
    with pytest.raises(ConflictError, match="safety copy"):
        backup.restore_backup(db_path, good_backup, BOSS)
    assert db_path.read_bytes() == before
    assert "newer" in _usernames(db_path)


def test_failed_replace_keeps_the_current_database(db_path, good_backup, monkeypatch):
    _add_staff_to_file(db_path, "newer")

    def fail(*args):
        raise PermissionError("in use")

    monkeypatch.setattr(backup.os, "replace", fail)
    with pytest.raises(ConflictError, match="in use"):
        backup.restore_backup(db_path, good_backup, BOSS)
    monkeypatch.undo()
    assert "newer" in _usernames(db_path)
    assert not Path(f"{db_path}.restore-tmp").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to replace an open file")
def test_restore_is_refused_while_the_database_is_open(db_path, good_backup):
    with closing(connect(db_path)) as conn:
        _add_staff(conn, "newer")
        with pytest.raises(ConflictError, match="in use"):
            backup.restore_backup(db_path, good_backup, BOSS)
        assert conn.execute("SELECT 1 FROM staff WHERE username = 'newer'").fetchone()
        assert not Path(f"{db_path}.restore-tmp").exists()


# --- migrating older backups ---


def test_older_backup_is_migrated_before_it_replaces_the_database(
    db_path, good_backup, tmp_path, monkeypatch
):
    next_version = _add_migration(tmp_path, monkeypatch, "CREATE TABLE extra (id INTEGER);")

    backup.restore_backup(db_path, good_backup, BOSS)

    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == next_version
        assert conn.execute("SELECT COUNT(*) FROM extra").fetchone()[0] == 0


def test_failing_migration_refuses_the_backup(db_path, good_backup, tmp_path, monkeypatch):
    before = db_path.read_bytes()
    next_version = _add_migration(
        tmp_path, monkeypatch, "ALTER TABLE staff ADD COLUMN username TEXT;"  # already exists
    )
    monkeypatch.setattr(backup, "_latest_schema_version", lambda: next_version)
    with pytest.raises(ValidationError):
        backup.restore_backup(db_path, good_backup, BOSS)
    assert db_path.read_bytes() == before


def _add_migration(tmp_path: Path, monkeypatch, sql: str) -> int:
    """Point the app at a copy of its schema folder with one more migration in it."""
    schema = tmp_path / "schema"
    schema.mkdir()
    for file in connection.SCHEMA_DIR.glob("*.sql"):
        shutil.copy(file, schema)
    version = len(list(schema.glob("*.sql"))) + 1
    (schema / f"{version:03d}_extra.sql").write_text(sql)
    monkeypatch.setattr(connection, "SCHEMA_DIR", schema)
    return version


def test_audit_failure_during_restore_keeps_the_current_database(
    db_path, good_backup, monkeypatch
):
    _add_staff_to_file(db_path, "newer")

    def fail(*args):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(ValidationError):
        backup.restore_backup(db_path, good_backup, BOSS)
    assert "newer" in _usernames(db_path)
