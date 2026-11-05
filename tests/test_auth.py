"""Passwords, the first manager account and signing in."""

from __future__ import annotations

import base64
import hashlib
import re
import sqlite3
import threading
import time
from contextlib import closing

import pytest

from restaurant.core import (
    Actor,
    AuthenticationError,
    ConflictError,
    Role,
    ValidationError,
)
from restaurant.db.connection import connect, transaction
from restaurant.services import audit, auth

PASSWORD = "correct horse"


def _audit_rows(conn):
    return [dict(row) for row in conn.execute("SELECT * FROM audit_log ORDER BY id")]


def _staff_count(conn):
    return conn.execute("SELECT COUNT(*) FROM staff").fetchone()[0]


@pytest.fixture
def owner(conn) -> dict:
    return auth.create_first_manager(conn, "owner", "Olive Owner", PASSWORD)


# --- hashing ---


def test_hash_is_salted_and_stores_its_parameters():
    first, second = auth.hash_password(PASSWORD), auth.hash_password(PASSWORD)
    assert first != second
    assert PASSWORD not in first
    assert re.fullmatch(r"scrypt\$32768\$8\$3\$[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+", first)


def test_verify_accepts_right_password_and_rejects_wrong_one():
    stored = auth.hash_password(PASSWORD)
    assert auth.verify_password(PASSWORD, stored) is True
    assert auth.verify_password(PASSWORD + "x", stored) is False
    assert auth.verify_password("", stored) is False


def test_verify_uses_parameters_from_the_stored_hash():
    salt = bytes(16)
    digest = hashlib.scrypt(PASSWORD.encode(), salt=salt, n=2**4, r=1, p=1, dklen=20)
    stored = f"scrypt$16$1$1${_b64(salt)}${_b64(digest)}"
    assert auth.verify_password(PASSWORD, stored) is True
    assert auth.verify_password("something else", stored) is False


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode()


def test_verify_compares_in_constant_time(monkeypatch):
    calls = []
    real = auth.hmac.compare_digest
    monkeypatch.setattr(
        auth.hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b)
    )
    auth.verify_password(PASSWORD, auth.hash_password(PASSWORD))
    assert calls == [1]


@pytest.mark.parametrize(
    "stored",
    [
        "x",
        "",
        "bcrypt$16$1$1$AAAA$AAAA",
        "scrypt$16$1$1$AAAA",
        "scrypt$abc$1$1$AAAA$AAAA",
        "scrypt$15$1$1$AAAA$AAAA",  # n must be a power of two
        "scrypt$16$1$1$not base64$AAAA",
        "scrypt$16$1$1$AAAA$",
        "scrypt$99999999999999999999999999$1$1$AAAA$AAAA",
    ],
)
def test_verify_rejects_unusable_stored_hash_after_doing_the_same_work(monkeypatch, stored):
    costs = _spy_on_scrypt(monkeypatch)
    assert auth.verify_password(PASSWORD, stored) is False
    assert costs[-1] == (2**15, 8, 3)


def _spy_on_scrypt(monkeypatch) -> list[tuple[int, int, int]]:
    """Record the (n, r, p) of each scrypt call that actually ran."""
    costs: list[tuple[int, int, int]] = []
    real = hashlib.scrypt

    def spy(password, *, salt, n, r, p, maxmem, dklen):
        result = real(password, salt=salt, n=n, r=r, p=p, maxmem=maxmem, dklen=dklen)
        costs.append((n, r, p))
        return result

    monkeypatch.setattr(auth.hashlib, "scrypt", spy)
    return costs


# --- first manager ---


def test_has_accounts_changes_after_first_manager(conn):
    assert auth.has_accounts(conn) is False
    auth.create_first_manager(conn, "owner", "Olive Owner", PASSWORD)
    assert auth.has_accounts(conn) is True


def test_no_default_account_exists_on_a_new_database(conn):
    assert _staff_count(conn) == 0


def test_first_manager_is_an_active_manager_without_hash_in_result(conn, owner):
    assert owner["username"] == "owner"
    assert owner["full_name"] == "Olive Owner"
    assert owner["role"] == "manager"
    assert owner["active"] == 1
    assert "password_hash" not in owner
    assert not any(PASSWORD in str(value) for value in owner.values())


def test_first_manager_stores_hash_not_password(conn, owner):
    stored = conn.execute("SELECT password_hash FROM staff").fetchone()[0]
    assert stored.startswith("scrypt$")
    assert auth.verify_password(PASSWORD, stored)


def test_first_manager_is_audited_without_password(conn, owner):
    rows = _audit_rows(conn)
    assert [row["action"] for row in rows] == ["staff.first_manager_created"]
    assert rows[0]["staff_id"] == owner["id"]
    assert PASSWORD not in rows[0]["details"]


def test_first_manager_refused_when_staff_exist(conn, owner):
    with pytest.raises(ConflictError):
        auth.create_first_manager(conn, "second", "Second", PASSWORD)
    assert _staff_count(conn) == 1


def test_first_manager_refused_when_only_inactive_staff_exist(conn, cashier):
    conn.execute("UPDATE staff SET active = 0")
    with pytest.raises(ConflictError):
        auth.create_first_manager(conn, "second", "Second", PASSWORD)
    assert _staff_count(conn) == 1


@pytest.mark.parametrize(
    ("username", "full_name", "password"),
    [
        ("", "Olive", PASSWORD),
        ("   ", "Olive", PASSWORD),
        ("owner", "", PASSWORD),
        ("owner", "   ", PASSWORD),
        ("owner", "Olive", "short12"),
        ("owner", "Olive", ""),
        ("a" * 51, "Olive", PASSWORD),
        ("owner", "a" * 101, PASSWORD),
        ("own\ner", "Olive", PASSWORD),
        ("owner", "Oli\x00ve", PASSWORD),
    ],
)
def test_first_manager_validates_input(conn, username, full_name, password):
    with pytest.raises(ValidationError):
        auth.create_first_manager(conn, username, full_name, password)
    assert _staff_count(conn) == 0
    assert _audit_rows(conn) == []


def test_first_manager_trims_username_and_name_but_not_password(conn):
    auth.create_first_manager(conn, "  owner ", " Olive ", "  spaced  ")
    row = conn.execute("SELECT username, full_name FROM staff").fetchone()
    assert (row["username"], row["full_name"]) == ("owner", "Olive")
    assert auth.authenticate(conn, "owner", "  spaced  ").username == "owner"
    with pytest.raises(AuthenticationError):
        auth.authenticate(conn, "owner", "spaced")


def test_first_manager_rolls_back_when_audit_fails(conn, monkeypatch):
    def fail(*args):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(sqlite3.OperationalError):
        auth.create_first_manager(conn, "owner", "Olive", PASSWORD)
    assert _staff_count(conn) == 0
    assert conn.in_transaction is False


def test_first_manager_race_creates_exactly_one_account(tmp_path):
    path = tmp_path / "race.db"
    connect(path).close()
    barrier = threading.Barrier(4)
    outcomes: list[str] = []

    def attempt(number: int) -> None:
        with closing(connect(path)) as conn:
            barrier.wait()
            try:
                auth.create_first_manager(conn, f"user{number}", "Someone", PASSWORD)
                outcomes.append("created")
            except ConflictError:
                outcomes.append("refused")

    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(outcomes) == ["created", "refused", "refused", "refused"]
    with closing(connect(path)) as conn:
        assert _staff_count(conn) == 1
        assert conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 1


def test_first_manager_check_happens_inside_the_transaction(tmp_path, monkeypatch):
    """An account committed while we wait for the write lock must be seen."""
    path = tmp_path / "race.db"
    hashed = threading.Event()
    real_hash = auth.hash_password

    def hash_then_signal(password):
        result = real_hash(password)
        hashed.set()
        return result

    monkeypatch.setattr(auth, "hash_password", hash_then_signal)
    outcome: list[object] = []

    def attempt() -> None:
        with closing(connect(path)) as conn:
            try:
                outcome.append(auth.create_first_manager(conn, "late", "Late", PASSWORD))
            except ConflictError as exc:
                outcome.append(exc)

    with closing(connect(path)) as first:
        with transaction(first):  # the other connection will wait for this lock
            first.execute(
                "INSERT INTO staff (username, full_name, password_hash, role, created_at) "
                "VALUES ('early', 'Early', 'x', 'manager', 'now')"
            )
            thread = threading.Thread(target=attempt)
            thread.start()
            assert hashed.wait(timeout=10)
            time.sleep(0.2)  # let it reach the database while the lock is held
        thread.join()
        assert isinstance(outcome[0], ConflictError)
        assert _staff_count(first) == 1


# --- signing in ---


def test_authenticate_returns_actor(conn, owner):
    actor = auth.authenticate(conn, "owner", PASSWORD)
    assert actor == Actor(owner["id"], "owner", "Olive Owner", Role.MANAGER)
    assert isinstance(actor.role, Role)


def test_authenticate_ignores_username_case_and_surrounding_space(conn, owner):
    assert auth.authenticate(conn, "  OWNER ", PASSWORD).username == "owner"


def test_password_is_case_sensitive(conn, owner):
    with pytest.raises(AuthenticationError):
        auth.authenticate(conn, "owner", PASSWORD.upper())


def _inactive_account(conn):
    auth.create_first_manager(conn, "gone", "Gone", PASSWORD)
    conn.execute("UPDATE staff SET active = 0 WHERE username = 'gone'")


def _failure_message(conn, username, password) -> str:
    with pytest.raises(AuthenticationError) as caught:
        auth.authenticate(conn, username, password)
    return str(caught.value)


def test_every_sign_in_failure_has_the_same_message(conn):
    _inactive_account(conn)
    messages = {
        _failure_message(conn, "nobody", PASSWORD),
        _failure_message(conn, "gone", "wrong password"),
        _failure_message(conn, "gone", PASSWORD),
    }
    conn.execute("UPDATE staff SET active = 1")
    messages.add(_failure_message(conn, "gone", "wrong password"))
    assert len(messages) == 1


def test_every_sign_in_path_does_one_full_hash(conn, monkeypatch):
    _inactive_account(conn)
    conn.execute(  # a stored hash that can't be parsed
        "INSERT INTO staff (username, full_name, password_hash, role, created_at) "
        "VALUES ('cashier', 'Cashier', 'x', 'cashier', 'now')"
    )
    costs = _spy_on_scrypt(monkeypatch)
    expected = [(2**15, 8, 3)]

    attempts = [
        ("nobody", PASSWORD),
        ("gone", "wrong password"),
        ("gone", PASSWORD),
        ("cashier", PASSWORD),
    ]
    for username, password in attempts:
        costs.clear()
        with pytest.raises(AuthenticationError):
            auth.authenticate(conn, username, password)
        assert costs == expected, username

    conn.execute("UPDATE staff SET active = 1 WHERE username = 'gone'")
    costs.clear()
    auth.authenticate(conn, "gone", PASSWORD)
    assert costs == expected


def test_success_is_audited(conn, owner):
    auth.authenticate(conn, "owner", PASSWORD)
    last = _audit_rows(conn)[-1]
    assert last["action"] == "auth.sign_in"
    assert last["staff_id"] == owner["id"]
    assert last["details"] == "owner"


def test_wrong_password_is_audited_and_survives_the_error(conn, owner):
    _failure_message(conn, "owner", "not the password")
    last = _audit_rows(conn)[-1]
    assert last["action"] == "auth.sign_in_failed"
    assert last["staff_id"] == owner["id"]
    assert "wrong password" in last["details"]
    assert "not the password" not in last["details"]
    assert conn.in_transaction is False


def test_inactive_account_is_audited(conn):
    _inactive_account(conn)
    _failure_message(conn, "gone", PASSWORD)
    last = _audit_rows(conn)[-1]
    assert last["action"] == "auth.sign_in_failed"
    assert last["staff_id"] is not None
    assert "inactive" in last["details"]


def test_unknown_username_is_audited_without_the_typed_text(conn):
    # Someone may type their password into the username box.
    _failure_message(conn, "my secret password", PASSWORD)
    last = _audit_rows(conn)[-1]
    assert last["action"] == "auth.sign_in_failed"
    assert last["staff_id"] is None
    assert "secret" not in last["details"]


def test_no_password_or_hash_ever_reaches_the_audit_log(conn):
    auth.create_first_manager(conn, "owner", "Olive", PASSWORD)
    actor = auth.authenticate(conn, "owner", PASSWORD)
    _failure_message(conn, "owner", "attempted-secret")
    auth.change_own_password(conn, actor, PASSWORD, "brand new secret")
    with pytest.raises(AuthenticationError):
        auth.change_own_password(conn, actor, "attempted-secret2", "another new secret")
    stored = conn.execute("SELECT password_hash FROM staff").fetchone()[0]
    for row in _audit_rows(conn):
        for secret in (PASSWORD, "attempted-secret", "brand new secret", "another new", stored):
            assert secret not in row["details"]


@pytest.mark.parametrize("username", ["", "   ", "own\x00er", "own\ud800er", "x" * 500])
def test_odd_usernames_fail_cleanly(conn, owner, username):
    assert _failure_message(conn, username, PASSWORD)


def test_placeholder_hash_can_never_sign_in(conn, cashier):
    _failure_message(conn, "cashier", "x")


def test_authentication_does_not_hold_a_lock_while_hashing(tmp_path, monkeypatch):
    path = tmp_path / "locks.db"
    with closing(connect(path)) as conn:
        auth.create_first_manager(conn, "owner", "Olive", PASSWORD)
        seen = []
        real = auth.verify_password
        monkeypatch.setattr(
            auth,
            "verify_password",
            lambda *args: seen.append(conn.in_transaction) or real(*args),
        )
        auth.authenticate(conn, "owner", PASSWORD)
        assert seen == [False]


# --- changing your own password ---


def test_change_own_password_requires_current_and_replaces_it(conn, owner):
    actor = auth.authenticate(conn, "owner", PASSWORD)
    auth.change_own_password(conn, actor, PASSWORD, "brand new secret")
    assert auth.authenticate(conn, "owner", "brand new secret") == actor
    _failure_message(conn, "owner", PASSWORD)
    actions = [row["action"] for row in _audit_rows(conn)]
    assert "auth.password_changed" in actions


@pytest.mark.parametrize("role", list(Role))
def test_every_role_can_change_their_own_password(conn, role):
    staff_id = conn.execute(
        "INSERT INTO staff (username, full_name, password_hash, role, created_at) "
        "VALUES ('someone', 'Someone', ?, ?, 'now')",
        (auth.hash_password(PASSWORD), role.value),
    ).lastrowid
    actor = Actor(staff_id, "someone", "Someone", role)
    auth.change_own_password(conn, actor, PASSWORD, "brand new secret")
    assert auth.authenticate(conn, "someone", "brand new secret").role is role


def test_change_own_password_with_wrong_current_changes_nothing_but_is_audited(conn, owner):
    actor = auth.authenticate(conn, "owner", PASSWORD)
    with pytest.raises(AuthenticationError):
        auth.change_own_password(conn, actor, "not it", "brand new secret")
    assert auth.authenticate(conn, "owner", PASSWORD) == actor
    assert "auth.password_change_failed" in [row["action"] for row in _audit_rows(conn)]


def test_change_own_password_validates_new_password(conn, owner):
    actor = auth.authenticate(conn, "owner", PASSWORD)
    with pytest.raises(ValidationError):
        auth.change_own_password(conn, actor, PASSWORD, "short12")
    assert auth.authenticate(conn, "owner", PASSWORD) == actor


def test_new_password_of_exactly_minimum_length_is_accepted(conn, owner):
    actor = auth.authenticate(conn, "owner", PASSWORD)
    auth.change_own_password(conn, actor, PASSWORD, "12345678")
    assert auth.authenticate(conn, "owner", "12345678") == actor


def test_change_own_password_refused_for_deactivated_account(conn, owner):
    actor = auth.authenticate(conn, "owner", PASSWORD)
    conn.execute("UPDATE staff SET active = 0")
    with pytest.raises(AuthenticationError):
        auth.change_own_password(conn, actor, PASSWORD, "brand new secret")
    conn.execute("UPDATE staff SET active = 1")
    assert auth.authenticate(conn, "owner", PASSWORD) == actor


def test_change_own_password_rolls_back_when_audit_fails(conn, owner, monkeypatch):
    actor = auth.authenticate(conn, "owner", PASSWORD)

    def fail(*args):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(sqlite3.OperationalError):
        auth.change_own_password(conn, actor, PASSWORD, "brand new secret")
    monkeypatch.undo()
    assert auth.authenticate(conn, "owner", PASSWORD) == actor
