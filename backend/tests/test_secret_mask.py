"""Tests fuer core/secret_mask.py: Maske, resolve_secret_update (F8 Nr. 11) und Retarget-Schutz [S3]."""
import pytest

from app.core import secrets as secret_store
from app.core.secret_mask import (
    LDAP_TARGET_FIELDS,
    OIDC_TARGET_FIELDS,
    SECRET_MASK,
    SMTP_TARGET_FIELDS,
    SecretReentryRequired,
    changed_target_fields,
    guard_secret_retarget,
    pick_targets,
    resolve_secret_update,
    secret_input_action,
)


@pytest.fixture(autouse=True)
def _reset_secret_state():
    secret_store.reset_for_tests()
    yield
    secret_store.reset_for_tests()


SMTP_BEFORE = {"host": "mail.example.com", "port": 587, "username": "relay", "encryption": "starttls"}


# --- Maske / resolve_secret_update (F8 §9.1 Nr. 11) -------------------------------------------

def test_mask_constant():
    assert SECRET_MASK == "••••••••"


@pytest.mark.parametrize(
    ("new", "old", "expected"),
    [
        (None, "a", "a"),
        (SECRET_MASK, "a", "a"),
        (" ", "a", ""),
        ("", "a", ""),
        ("neu", "a", "neu"),
        (None, None, ""),
        # Ergaenzungen
        (f"  {SECRET_MASK} ", "a", "a"),   # Maske mit Leerraum gilt als Maske
        (" neu ", "a", " neu "),           # neuer Wert wird nicht gestrippt
        (SECRET_MASK, None, ""),
    ],
)
def test_resolve_secret_update(new, old, expected):
    assert resolve_secret_update(new, old) == expected


def test_resolve_secret_update_keeps_unreadable_placeholder():
    """Behalten eines unlesbaren Altwerts liefert den Platzhalter (Aufrufer schreibt dann nicht)."""
    result = resolve_secret_update(None, secret_store.UNREADABLE)
    assert secret_store.is_unreadable(result)
    assert result == ""
    result = resolve_secret_update(SECRET_MASK, secret_store.UNREADABLE)
    assert secret_store.is_unreadable(result)
    # Loeschen/neu setzen ist auch bei unlesbarem Altwert normal
    assert not secret_store.is_unreadable(resolve_secret_update("", secret_store.UNREADABLE))
    assert resolve_secret_update("neu", secret_store.UNREADABLE) == "neu"


@pytest.mark.parametrize(
    ("new", "action"),
    [(None, "keep"), (SECRET_MASK, "keep"), ("", "clear"), ("  ", "clear"), ("x", "set")],
)
def test_secret_input_action(new, action):
    assert secret_input_action(new) == action


# --- guard_secret_retarget (Bauplan B.2) -----------------------------------------------------

def test_reentry_error_is_value_error_with_german_text():
    exc = SecretReentryRequired(["host"])
    assert isinstance(exc, ValueError)
    assert "Zieladresse geändert" in str(exc)
    assert "erneut eingeben" in str(exc)
    assert exc.changed_fields == ("host",)


@pytest.mark.parametrize("secret_in", [None, SECRET_MASK])
def test_same_target_with_mask_is_ok(secret_in):
    guard_secret_retarget(targets_before=SMTP_BEFORE, targets_after=dict(SMTP_BEFORE), secret_in=secret_in)


@pytest.mark.parametrize("secret_in", [None, SECRET_MASK, f" {SECRET_MASK} "])
def test_changed_target_with_mask_requires_reentry(secret_in):
    after = dict(SMTP_BEFORE, host="evil.example.net")
    with pytest.raises(SecretReentryRequired) as ei:
        guard_secret_retarget(targets_before=SMTP_BEFORE, targets_after=after, secret_in=secret_in)
    assert ei.value.changed_fields == ("host",)
    # nie Werte in der Meldung
    assert "evil" not in str(ei.value)


def test_changed_target_with_new_secret_is_ok():
    after = dict(SMTP_BEFORE, host="mail2.example.com")
    guard_secret_retarget(targets_before=SMTP_BEFORE, targets_after=after, secret_in="neues-passwort")


def test_changed_target_when_clearing_secret_is_ok():
    after = dict(SMTP_BEFORE, host="mail2.example.com")
    guard_secret_retarget(targets_before=SMTP_BEFORE, targets_after=after, secret_in="")


def test_changed_target_without_stored_secret_is_ok():
    after = dict(SMTP_BEFORE, host="mail2.example.com")
    guard_secret_retarget(targets_before=SMTP_BEFORE, targets_after=after, secret_in=None, secret_stored=False)


@pytest.mark.parametrize(
    ("field", "value"),
    [("port", 465), ("username", "andere"), ("encryption", "ssl")],
)
def test_every_smtp_target_field_counts(field, value):
    after = dict(SMTP_BEFORE, **{field: value})
    with pytest.raises(SecretReentryRequired) as ei:
        guard_secret_retarget(targets_before=SMTP_BEFORE, targets_after=after, secret_in=SECRET_MASK)
    assert ei.value.changed_fields == (field,)


def test_normalization_hosts_ports_bools():
    before = {"host": "Mail.Example.com.", "port": "587", "use_tls": True, "username": "relay"}
    after = {"host": "  mail.example.COM ", "port": 587, "use_tls": "true", "username": " relay "}
    assert changed_target_fields(before, after) == []
    guard_secret_retarget(targets_before=before, targets_after=after, secret_in=None)


def test_username_case_is_significant():
    before = {"username": "Relay"}
    after = {"username": "relay"}
    assert changed_target_fields(before, after) == ["username"]


def test_missing_field_in_after_counts_as_unchanged():
    guard_secret_retarget(targets_before=SMTP_BEFORE, targets_after={"host": "mail.example.com"}, secret_in=None)


def test_new_field_with_value_counts_as_change():
    with pytest.raises(SecretReentryRequired):
        guard_secret_retarget(targets_before={}, targets_after={"host": "x.example"}, secret_in=None)


def test_ldap_server_urls_order_and_case_insensitive_host():
    before = {"server_urls": ["ldaps://dc1.example.com:636", "ldaps://dc2.example.com:636"], "bind_dn": "CN=svc,DC=ex"}
    reordered = {"server_urls": ["LDAPS://DC2.example.com:636", "ldaps://dc1.example.com:636"], "bind_dn": "CN=svc,DC=ex"}
    assert changed_target_fields(before, reordered) == []
    added = {"server_urls": ["ldaps://dc1.example.com:636", "ldaps://evil.example.net:636"], "bind_dn": "CN=svc,DC=ex"}
    with pytest.raises(SecretReentryRequired) as ei:
        guard_secret_retarget(targets_before=before, targets_after=added, secret_in=SECRET_MASK)
    assert ei.value.changed_fields == ("server_urls",)


def test_oidc_issuer_path_is_significant_host_case_not():
    before = {"issuer": "https://idp.example.com/realms/A", "client_id": "pdns", "token_auth_method": "client_secret_basic"}
    same = dict(before, issuer="HTTPS://IDP.example.com/realms/A")
    assert changed_target_fields(before, same) == []
    other_realm = dict(before, issuer="https://idp.example.com/realms/a")
    assert changed_target_fields(before, other_realm) == ["issuer"]
    with pytest.raises(SecretReentryRequired):
        guard_secret_retarget(targets_before=before, targets_after=dict(before, client_id="x"), secret_in=None)


def test_prefixed_setting_keys_are_normalized_too():
    before = {"smtp_host": "Mail.Example.com", "ldap_server_urls": ["ldaps://DC1.example.com"]}
    after = {"smtp_host": "mail.example.com", "ldap_server_urls": ["ldaps://dc1.example.com"]}
    assert changed_target_fields(before, after) == []


def test_pick_targets_and_field_lists():
    values = {"host": "h", "port": 25, "password": "geheim", "from_email": "a@b"}
    assert pick_targets(values, SMTP_TARGET_FIELDS) == {"host": "h", "port": 25}
    assert "issuer" in OIDC_TARGET_FIELDS and "server_urls" in LDAP_TARGET_FIELDS
