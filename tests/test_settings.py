"""Restaurant settings: reading, validation, permissions and auditing."""

from __future__ import annotations

import sqlite3

import pytest

from restaurant.core import PermissionDeniedError, ValidationError
from restaurant.services import audit, settings

DEFAULTS = {"restaurant_name": "My Restaurant", "currency_symbol": "$", "tax_rate_bp": 0}


def _audit_rows(conn):
    return [dict(row) for row in conn.execute("SELECT * FROM audit_log ORDER BY id")]


def test_defaults_come_back_with_tax_rate_as_an_integer(conn):
    assert settings.get_all(conn) == DEFAULTS
    assert type(settings.get_all(conn)["tax_rate_bp"]) is int


def test_every_role_can_read(conn, manager, cashier, stock_clerk):
    for actor in (manager, cashier, stock_clerk):
        assert settings.get_all(conn, actor) == DEFAULTS


def test_update_changes_the_given_settings_only(conn, manager):
    result = settings.update(conn, manager, tax_rate_bp=825)
    assert result == {**DEFAULTS, "tax_rate_bp": 825}
    assert settings.get_all(conn) == result
    result = settings.update(conn, manager, restaurant_name=" Bistro ", currency_symbol=" kr. ")
    assert result == {"restaurant_name": "Bistro", "currency_symbol": "kr.", "tax_rate_bp": 825}


def test_tax_rate_is_stored_as_text_without_a_decimal_point(conn, manager):
    settings.update(conn, manager, tax_rate_bp=825)
    row = conn.execute("SELECT value FROM settings WHERE key = 'tax_rate_bp'").fetchone()
    assert row["value"] == "825"


def test_cashier_and_stock_clerk_cannot_update(conn, cashier, stock_clerk):
    for actor in (cashier, stock_clerk):
        with pytest.raises(PermissionDeniedError):
            settings.update(conn, actor, tax_rate_bp=825)
    assert settings.get_all(conn) == DEFAULTS
    assert _audit_rows(conn) == []


@pytest.mark.parametrize("tax_rate_bp", [0, 1, 9999, 10_000])
def test_tax_rate_limits_are_accepted(conn, manager, tax_rate_bp):
    settings.update(conn, manager, tax_rate_bp=1)  # so that 0 and 1 are also tested as changes
    assert settings.update(conn, manager, tax_rate_bp=tax_rate_bp)["tax_rate_bp"] == tax_rate_bp


@pytest.mark.parametrize("tax_rate_bp", [-1, 10_001, 8.25, 825.0, "825", "", True, False, [825]])
def test_bad_tax_rates_are_rejected(conn, manager, tax_rate_bp):
    with pytest.raises(ValidationError):
        settings.update(conn, manager, tax_rate_bp=tax_rate_bp)
    assert settings.get_all(conn) == DEFAULTS


@pytest.mark.parametrize("name", ["", "   ", "\t\n", "a" * 101, "Bad\nName", 42])
def test_bad_restaurant_names_are_rejected(conn, manager, name):
    with pytest.raises(ValidationError):
        settings.update(conn, manager, restaurant_name=name)
    assert settings.get_all(conn) == DEFAULTS


@pytest.mark.parametrize("symbol", ["ABCD", "$$$$", "a\nb", 5])
def test_bad_currency_symbols_are_rejected(conn, manager, symbol):
    with pytest.raises(ValidationError):
        settings.update(conn, manager, currency_symbol=symbol)
    assert settings.get_all(conn) == DEFAULTS


@pytest.mark.parametrize("symbol", ["", "€", "R$", "kr.", "  $  "])
def test_currency_symbols_up_to_three_characters_are_accepted(conn, manager, symbol):
    result = settings.update(conn, manager, currency_symbol=symbol)
    assert result["currency_symbol"] == symbol.strip()


def test_one_invalid_field_rejects_the_whole_update(conn, manager):
    with pytest.raises(ValidationError):
        settings.update(conn, manager, restaurant_name="Bistro", tax_rate_bp=-1)
    assert settings.get_all(conn) == DEFAULTS
    assert _audit_rows(conn) == []


def test_changes_are_audited_with_old_and_new_values(conn, manager):
    settings.update(conn, manager, restaurant_name="Bistro", tax_rate_bp=825)
    rows = _audit_rows(conn)
    assert len(rows) == 1
    assert rows[0]["action"] == "settings.updated"
    assert rows[0]["staff_id"] == manager.id
    assert "restaurant_name: My Restaurant to Bistro" in rows[0]["details"]
    assert "tax_rate_bp: 0 to 825" in rows[0]["details"]
    assert "currency_symbol" not in rows[0]["details"]


def test_update_without_a_real_change_writes_no_audit(conn, manager):
    assert settings.update(conn, manager) == DEFAULTS
    assert settings.update(conn, manager, tax_rate_bp=0, currency_symbol="$") == DEFAULTS
    assert _audit_rows(conn) == []


def test_update_rolls_back_every_field_when_audit_fails(conn, manager, monkeypatch):
    def fail(*args):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(audit, "record", fail)
    with pytest.raises(sqlite3.OperationalError):
        settings.update(conn, manager, restaurant_name="Bistro", tax_rate_bp=825)
    assert conn.in_transaction is False
    assert settings.get_all(conn) == DEFAULTS


@pytest.mark.parametrize("bad", [10_001, -1, 825.0, True, "825"])
def test_tax_rate_error_speaks_in_percent_not_basis_points(conn, manager, bad):
    with pytest.raises(ValidationError) as error:
        settings.update(conn, manager, tax_rate_bp=bad)
    assert "basis points" not in str(error.value)
    assert "0 to 100" in str(error.value)


def test_get_text_reads_one_setting_and_does_not_care_about_the_others(conn):
    conn.execute("UPDATE settings SET value = 'abc' WHERE key = 'tax_rate_bp'")
    assert settings.get_text(conn, "currency_symbol") == "$"
    conn.execute("DELETE FROM settings")
    assert settings.get_text(conn, "currency_symbol") == ""
