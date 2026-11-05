"""Staff management: permissions, FR-S4, auditing and never exposing hashes."""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing

import pytest

from restaurant.core import (
    Actor,
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    Role,
    ValidationError,
)
from restaurant.db.connection import connect, transaction
from restaurant.services import audit, auth, staff

PASSWORD = "correct horse"


def _audit_rows(conn):
    return [dict(row) for row in conn.execute("SELECT * FROM audit_log ORDER BY id")]


def _row(conn, staff_id):
    return dict(conn.execute("SELECT * FROM staff WHERE id = ?", (staff_id,)).fetchone())


def _break_audit(monkeypatch):
    def fail(*args):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(audit, "record", fail)


@pytest.fixture
def second_manager(conn) -> Actor:
    cursor = conn.execute(
        "INSERT INTO staff (username, full_name, password_hash, role, created_at) "
        "VALUES ('second', 'Second', 'x', 'manager', 'now')"
    )
    return Actor(cursor.lastrowid, "second", "Second", Role.MANAGER)


# --- permissions ---


_CALLS = {
    "list_staff": lambda conn, actor, target: staff.list_staff(conn, actor),
    "create_staff": lambda conn, actor, target: staff.create_staff(
        conn, actor, "new", "New", "cashier", PASSWORD
    ),
    "update_staff": lambda conn, actor, target: staff.update_staff(
        conn, actor, target, full_name="Changed"
    ),
    "deactivate_staff": lambda conn, actor, target: staff.deactivate_staff(conn, actor, target),
    "reactivate_staff": lambda conn, actor, target: staff.reactivate_staff(conn, actor, target),
    "reset_password": lambda conn, actor, target: staff.reset_password(
        conn, actor, target, PASSWORD
    ),
}


@pytest.mark.parametrize("call", _CALLS.values(), ids=_CALLS.keys())
def test_cashier_and_stock_clerk_are_refused(conn, manager, cashier, stock_clerk, call):
    before = [dict(row) for row in conn.execute("SELECT * FROM staff")]
    for actor in (cashier, stock_clerk):
        with pytest.raises(PermissionDeniedError):
            call(conn, actor, manager.id)
    assert [dict(row) for row in conn.execute("SELECT * FROM staff")] == before
    assert _audit_rows(conn) == []


@pytest.mark.parametrize("call", _CALLS.values(), ids=_CALLS.keys())
def test_manager_is_allowed(conn, manager, cashier, call):
    call(conn, manager, cashier.id)


# --- listing ---


def test_list_includes_inactive_staff_and_never_the_hash(conn, manager, cashier):
    staff.deactivate_staff(conn, manager, cashier.id)
    rows = staff.list_staff(conn, manager)
    assert {row["username"]: row["active"] for row in rows} == {"manager": 1, "cashier": 0}
    assert all("password_hash" not in row for row in rows)
    assert set(rows[0]) == {"id", "username", "full_name", "role", "active", "created_at"}


def test_list_is_ordered_by_name(conn, manager):
    staff.create_staff(conn, manager, "zed", "aaron", "cashier", PASSWORD)
    staff.create_staff(conn, manager, "amy", "Zoe", "cashier", PASSWORD)
    names = [row["full_name"] for row in staff.list_staff(conn, manager)]
    assert names == ["aaron", "Manager", "Zoe"]


# --- creating ---


@pytest.mark.parametrize("role", [Role.CASHIER, "cashier", "stock_clerk", "manager"])
def test_create_stores_account_that_can_sign_in(conn, manager, role):
    created = staff.create_staff(conn, manager, " amy ", " Amy A ", role, PASSWORD)
    assert created["username"] == "amy"
    assert created["full_name"] == "Amy A"
    assert created["role"] == Role(role).value
    assert created["active"] == 1
    assert "password_hash" not in created
    assert auth.authenticate(conn, "amy", PASSWORD).id == created["id"]


def test_create_is_audited_without_password(conn, manager):
    staff.create_staff(conn, manager, "amy", "Amy", "cashier", PASSWORD)
    row = _audit_rows(conn)[-1]
    assert row["action"] == "staff.created"
    assert row["staff_id"] == manager.id
    assert "amy" in row["details"] and "cashier" in row["details"]
    assert PASSWORD not in row["details"]


@pytest.mark.parametrize("username", ["amy", "AMY", " Amy "])
def test_duplicate_username_is_a_conflict_ignoring_case(conn, manager, username):
    staff.create_staff(conn, manager, "amy", "Amy", "cashier", PASSWORD)
    audit_count = len(_audit_rows(conn))
    with pytest.raises(ConflictError, match="already taken"):
        staff.create_staff(conn, manager, username, "Other", "cashier", PASSWORD)
    assert conn.in_transaction is False
    assert len(_audit_rows(conn)) == audit_count


def test_inactive_username_stays_taken(conn, manager):
    created = staff.create_staff(conn, manager, "amy", "Amy", "cashier", PASSWORD)
    staff.deactivate_staff(conn, manager, created["id"])
    with pytest.raises(ConflictError):
        staff.create_staff(conn, manager, "amy", "Other", "cashier", PASSWORD)


@pytest.mark.parametrize(
    ("username", "full_name", "role", "password"),
    [
        ("", "Amy", "cashier", PASSWORD),
        ("amy", "  ", "cashier", PASSWORD),
        ("amy", "Amy", "owner", PASSWORD),
        ("amy", "Amy", "", PASSWORD),
        ("amy", "Amy", "cashier", "short12"),
        ("am\ty", "Amy", "cashier", PASSWORD),
    ],
)
def test_create_validates_input(conn, manager, username, full_name, role, password):
    with pytest.raises(ValidationError):
        staff.create_staff(conn, manager, username, full_name, role, password)
    assert len(staff.list_staff(conn, manager)) == 1


def test_create_rolls_back_when_audit_fails(conn, manager, monkeypatch):
    _break_audit(monkeypatch)
    with pytest.raises(sqlite3.OperationalError):
        staff.create_staff(conn, manager, "amy", "Amy", "cashier", PASSWORD)
    assert conn.in_transaction is False
    assert len(staff.list_staff(conn, manager)) == 1


# --- updating ---


def test_update_changes_name_and_role_and_audits_both(conn, manager, cashier):
    updated = staff.update_staff(
        conn, manager, cashier.id, full_name=" New Name ", role="stock_clerk"
    )
    assert updated["full_name"] == "New Name"
    assert updated["role"] == "stock_clerk"
    assert "password_hash" not in updated
    row = _audit_rows(conn)[-1]
    assert row["action"] == "staff.updated"
    assert row["staff_id"] == manager.id
    assert "cashier" in row["details"] and "stock_clerk" in row["details"]
    assert "New Name" in row["details"]


def test_update_leaves_omitted_fields_alone(conn, manager, cashier):
    staff.update_staff(conn, manager, cashier.id, full_name="New Name")
    assert _row(conn, cashier.id)["role"] == "cashier"
    staff.update_staff(conn, manager, cashier.id, role="manager")
    assert _row(conn, cashier.id)["full_name"] == "New Name"


def test_update_with_no_real_change_writes_no_audit(conn, manager, cashier):
    staff.update_staff(conn, manager, cashier.id, full_name=cashier.full_name, role="cashier")
    staff.update_staff(conn, manager, cashier.id)
    assert _audit_rows(conn) == []


@pytest.mark.parametrize(
    "fields", [{"full_name": ""}, {"full_name": "   "}, {"role": "owner"}, {"role": ""}]
)
def test_update_validates_input(conn, manager, cashier, fields):
    before = _row(conn, cashier.id)
    with pytest.raises(ValidationError):
        staff.update_staff(conn, manager, cashier.id, **fields)
    assert _row(conn, cashier.id) == before


def test_update_rolls_back_all_fields_when_audit_fails(conn, manager, cashier, monkeypatch):
    before = _row(conn, cashier.id)
    _break_audit(monkeypatch)
    with pytest.raises(sqlite3.OperationalError):
        staff.update_staff(conn, manager, cashier.id, full_name="New", role="stock_clerk")
    assert conn.in_transaction is False
    assert _row(conn, cashier.id) == before


@pytest.mark.parametrize(
    "call",
    [
        lambda conn, actor: staff.update_staff(conn, actor, 9999, full_name="X"),
        lambda conn, actor: staff.deactivate_staff(conn, actor, 9999),
        lambda conn, actor: staff.reactivate_staff(conn, actor, 9999),
        lambda conn, actor: staff.reset_password(conn, actor, 9999, PASSWORD),
    ],
)
def test_unknown_staff_id_is_not_found(conn, manager, call):
    with pytest.raises(NotFoundError):
        call(conn, manager)
    assert conn.in_transaction is False


# --- FR-S4: the last active manager ---


def test_last_manager_cannot_deactivate_themselves(conn, manager, cashier):
    with pytest.raises(ConflictError, match="last active manager"):
        staff.deactivate_staff(conn, manager, manager.id)
    assert _row(conn, manager.id)["active"] == 1
    assert conn.in_transaction is False


@pytest.mark.parametrize("role", ["cashier", "stock_clerk"])
def test_last_manager_cannot_change_their_own_role(conn, manager, role):
    with pytest.raises(ConflictError, match="last active manager"):
        staff.update_staff(conn, manager, manager.id, role=role)
    assert _row(conn, manager.id)["role"] == "manager"


def test_rejected_role_change_also_discards_name_change(conn, manager):
    with pytest.raises(ConflictError):
        staff.update_staff(conn, manager, manager.id, full_name="Renamed", role="cashier")
    assert _row(conn, manager.id)["full_name"] == "Manager"
    assert _audit_rows(conn) == []


def test_last_manager_can_still_be_renamed(conn, manager):
    updated = staff.update_staff(conn, manager, manager.id, full_name="Renamed", role="manager")
    assert updated["full_name"] == "Renamed"


def test_inactive_managers_do_not_count_as_another_manager(conn, manager, second_manager):
    staff.deactivate_staff(conn, manager, second_manager.id)
    with pytest.raises(ConflictError):
        staff.deactivate_staff(conn, manager, manager.id)
    with pytest.raises(ConflictError):
        staff.update_staff(conn, manager, manager.id, role="cashier")


def test_active_non_managers_do_not_count_as_another_manager(conn, manager, cashier, stock_clerk):
    with pytest.raises(ConflictError):
        staff.deactivate_staff(conn, manager, manager.id)


def test_with_two_managers_one_may_go_but_not_both(conn, manager, second_manager):
    staff.deactivate_staff(conn, manager, second_manager.id)
    assert _row(conn, second_manager.id)["active"] == 0
    staff.reactivate_staff(conn, manager, second_manager.id)
    staff.update_staff(conn, manager, manager.id, role="cashier")
    assert _row(conn, manager.id)["role"] == "cashier"
    with pytest.raises(ConflictError):
        staff.update_staff(conn, second_manager, second_manager.id, role="cashier")
    with pytest.raises(ConflictError):
        staff.deactivate_staff(conn, second_manager, second_manager.id)


def test_inactive_manager_may_change_role_without_affecting_the_rule(
    conn, manager, second_manager
):
    staff.deactivate_staff(conn, manager, second_manager.id)
    staff.update_staff(conn, manager, second_manager.id, role="cashier")
    assert _row(conn, second_manager.id)["role"] == "cashier"
    assert _row(conn, manager.id)["active"] == 1


def test_non_managers_can_always_be_deactivated(conn, manager, cashier):
    assert staff.deactivate_staff(conn, manager, cashier.id)["active"] == 0


def test_rule_is_checked_inside_the_transaction(tmp_path):
    """A manager demoted by another connection while we wait for the lock must count."""
    path = tmp_path / "race.db"
    with closing(connect(path)) as first:
        first.execute(
            "INSERT INTO staff (username, full_name, password_hash, role, created_at) VALUES "
            "('a', 'A', 'x', 'manager', 'now'), ('b', 'B', 'x', 'manager', 'now')"
        )
        actor = Actor(1, "a", "A", Role.MANAGER)
        outcome: list[object] = []
        started = threading.Event()

        def deactivate_a() -> None:
            with closing(connect(path)) as second:
                started.set()
                try:
                    staff.deactivate_staff(second, actor, 1)
                    outcome.append("deactivated")
                except ConflictError as exc:
                    outcome.append(exc)

        with transaction(first):  # the other connection now waits for this lock
            first.execute("UPDATE staff SET role = 'cashier' WHERE id = 2")
            thread = threading.Thread(target=deactivate_a)
            thread.start()
            started.wait()
            time.sleep(0.3)
        thread.join()
        assert isinstance(outcome[0], ConflictError)
        assert first.execute("SELECT active FROM staff WHERE id = 1").fetchone()[0] == 1


# --- deactivating and reactivating ---


def test_staff_are_never_deleted(conn, manager, cashier):
    staff.deactivate_staff(conn, manager, cashier.id)
    assert conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0] == 2
    assert not hasattr(staff, "delete_staff")


def test_deactivated_staff_cannot_sign_in_until_reactivated(conn, manager):
    created = staff.create_staff(conn, manager, "amy", "Amy", "cashier", PASSWORD)
    result = staff.deactivate_staff(conn, manager, created["id"])
    assert result["active"] == 0 and "password_hash" not in result
    with pytest.raises(AuthenticationError):
        auth.authenticate(conn, "amy", PASSWORD)
    result = staff.reactivate_staff(conn, manager, created["id"])
    assert result["active"] == 1 and "password_hash" not in result
    assert auth.authenticate(conn, "amy", PASSWORD).id == created["id"]


def test_activation_changes_are_audited(conn, manager, cashier):
    staff.deactivate_staff(conn, manager, cashier.id)
    staff.reactivate_staff(conn, manager, cashier.id)
    rows = _audit_rows(conn)
    assert [row["action"] for row in rows] == ["staff.deactivated", "staff.reactivated"]
    assert all(row["details"] == "cashier" and row["staff_id"] == manager.id for row in rows)


def test_repeating_activation_changes_writes_no_audit(conn, manager, cashier):
    staff.reactivate_staff(conn, manager, cashier.id)
    assert _audit_rows(conn) == []
    staff.deactivate_staff(conn, manager, cashier.id)
    staff.deactivate_staff(conn, manager, cashier.id)
    assert len(_audit_rows(conn)) == 1


def test_deactivation_rolls_back_when_audit_fails(conn, manager, cashier, monkeypatch):
    _break_audit(monkeypatch)
    with pytest.raises(sqlite3.OperationalError):
        staff.deactivate_staff(conn, manager, cashier.id)
    assert _row(conn, cashier.id)["active"] == 1
    with pytest.raises(sqlite3.OperationalError):
        staff.deactivate_staff(conn, manager, cashier.id)
    assert conn.in_transaction is False


def test_reactivation_rolls_back_when_audit_fails(conn, manager, cashier, monkeypatch):
    staff.deactivate_staff(conn, manager, cashier.id)
    _break_audit(monkeypatch)
    with pytest.raises(sqlite3.OperationalError):
        staff.reactivate_staff(conn, manager, cashier.id)
    assert _row(conn, cashier.id)["active"] == 0


# --- resetting passwords ---


def test_reset_password_replaces_it_without_the_current_one(conn, manager):
    created = staff.create_staff(conn, manager, "amy", "Amy", "cashier", PASSWORD)
    assert staff.reset_password(conn, manager, created["id"], "brand new secret") is None
    assert auth.authenticate(conn, "amy", "brand new secret").id == created["id"]
    with pytest.raises(AuthenticationError):
        auth.authenticate(conn, "amy", PASSWORD)


def test_reset_password_is_audited_without_password_or_hash(conn, manager, cashier):
    staff.reset_password(conn, manager, cashier.id, "brand new secret")
    row = _audit_rows(conn)[-1]
    assert row["action"] == "staff.password_reset"
    assert row["staff_id"] == manager.id
    assert row["details"] == "cashier"
    assert _row(conn, cashier.id)["password_hash"] not in row["details"]


def test_manager_can_reset_their_own_password(conn, manager):
    staff.reset_password(conn, manager, manager.id, "brand new secret")
    assert auth.authenticate(conn, "manager", "brand new secret").id == manager.id


def test_reset_password_validates_length(conn, manager, cashier):
    before = _row(conn, cashier.id)["password_hash"]
    with pytest.raises(ValidationError):
        staff.reset_password(conn, manager, cashier.id, "short12")
    assert _row(conn, cashier.id)["password_hash"] == before


def test_reset_password_rolls_back_when_audit_fails(conn, manager, cashier, monkeypatch):
    before = _row(conn, cashier.id)["password_hash"]
    _break_audit(monkeypatch)
    with pytest.raises(sqlite3.OperationalError):
        staff.reset_password(conn, manager, cashier.id, "brand new secret")
    assert _row(conn, cashier.id)["password_hash"] == before
