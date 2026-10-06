"""WS-W2-NACHARBEIT (Welle 3): Restpunkte aus Welle 2 ohne MariaDB.

- L1: ``PUT /settings/servers/{id}`` – URL-Wechsel mit gespeichertem API-Key verlangt die Neueingabe des Keys
  (``guard_secret_retarget``, 400 ``secret_reentry_required``); die Maske wird nie als Key gespeichert.
- L2: ``GET /settings/servers/{id}/api-key`` liefert den Klartext nur, wenn der Audit-Eintrag geschrieben wurde
  (sonst 503).
- Antraege WS-F9F11-BE: ``repair`` sichtbar fuer Nicht-Admins (``audit.PUBLIC_DETAIL_KEYS``), Rollback-Sperre
  ``dyndns_repair`` fuer DynDNS-Reparaturen, Metrik-Labels ``badip``/``numhost``.

Die Settings-Endpunkte laufen ueber die SQLite-Infrastruktur aus ``test_secrets_routes``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from test_secrets_routes import FOREIGN, _add_server, _enc, detached_audits, pdns_state, sdb, settings_client  # noqa: F401
from app.core import metrics as prom
from app.core import secrets as secret_store
from app.core.secret_mask import SECRET_MASK
from app.services import record_history as rh
from app.services.audit import PUBLIC_DETAIL_KEYS, public_details


def _key_of(sdb, sid: int) -> str:
    raw = sdb.raw_value("SELECT api_key FROM server_configs WHERE id = :i", {"i": sid})
    return secret_store.decrypt_value(raw, label="t") if raw else raw


def _url_of(sdb, sid: int) -> str:
    return sdb.raw_value("SELECT url FROM server_configs WHERE id = :i", {"i": sid})


# ---------------------------------------------------------------------------------------------
# L1: Retarget-Schutz fuer den PowerDNS-API-Key
# ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("api_key", [None, "", "   ", SECRET_MASK])
def test_server_url_change_requires_api_key(settings_client, pdns_state, api_key):
    sdb = settings_client.sdb
    _add_server(sdb, 11, "ns1", _enc("geheimer-key"), active=False)
    body = {"url": "https://boese.example:8081"}
    if api_key is not None:
        body["api_key"] = api_key
    r = settings_client.put("/api/v1/settings/servers/11", json=body)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "secret_reentry_required" and r.json()["fields"] == ["url"]
    assert "geheimer-key" not in r.text
    assert _url_of(sdb, 11) == "http://ns1:8081" and _key_of(sdb, 11) == "geheimer-key"
    assert sdb.audits("SERVER_UPDATE") == []


def test_server_url_change_with_new_key_is_saved(settings_client, pdns_state):
    sdb = settings_client.sdb
    _add_server(sdb, 12, "ns2", _enc("alt"), active=False)
    r = settings_client.put("/api/v1/settings/servers/12", json={"url": "https://neu.example:8081/", "api_key": "neu"})
    assert r.status_code == 200, r.text
    assert _url_of(sdb, 12) == "https://neu.example:8081" and _key_of(sdb, 12) == "neu"
    (audit,) = sdb.audits("SERVER_UPDATE")
    assert audit.details["changed"]["api_key"] == "changed" and "neu" not in str(audit.details["changed"]["api_key"])


def test_server_same_url_and_other_fields_keep_key_without_reentry(settings_client, pdns_state):
    sdb = settings_client.sdb
    _add_server(sdb, 13, "ns3", _enc("bleibt"), active=False)
    # gleiche URL (Schreibweise/Schlussstrich egal) und andere Felder: kein Key noetig
    r = settings_client.put("/api/v1/settings/servers/13",
                            json={"url": "HTTP://NS3:8081/", "description": "neu", "api_key": SECRET_MASK})
    assert r.status_code == 200, r.text
    assert _key_of(sdb, 13) == "bleibt"   # die Maske wird nie als Key gespeichert
    r = settings_client.put("/api/v1/settings/servers/13", json={"display_name": "NS 3"})
    assert r.status_code == 200 and _key_of(sdb, 13) == "bleibt"


def test_server_url_change_without_readable_key_is_allowed(settings_client, pdns_state):
    sdb = settings_client.sdb
    _add_server(sdb, 14, "ns-fremd", FOREIGN.encrypt("x"), active=False)   # nicht entschluesselbar
    _add_server(sdb, 15, "ns-leer", "", active=False)                       # kein Key gespeichert
    for sid in (14, 15):
        r = settings_client.put(f"/api/v1/settings/servers/{sid}", json={"url": "https://anders.example:8081"})
        assert r.status_code == 200, r.text
        assert _url_of(sdb, sid) == "https://anders.example:8081"


# ---------------------------------------------------------------------------------------------
# L2: Reveal nur mit Audit-Eintrag
# ---------------------------------------------------------------------------------------------
def test_reveal_without_audit_row_returns_503(settings_client, monkeypatch):
    from app.routers import settings as settings_router

    _add_server(settings_client.sdb, 16, "ns1", _enc("pdns-key-16"))

    async def _failing_audit(*a, **kw):
        return None   # so meldet write_audit einen abgefangenen Fehler

    monkeypatch.setattr(settings_router, "write_audit", _failing_audit)
    r = settings_client.get("/api/v1/settings/servers/16/api-key")
    assert r.status_code == 503, r.text
    assert "pdns-key-16" not in r.text
    assert r.json()["detail"] == settings_router.REVEAL_AUDIT_FAILED_DETAIL


def test_reveal_with_audit_row_still_works(settings_client):
    _add_server(settings_client.sdb, 17, "ns1", _enc("pdns-key-17"))
    r = settings_client.get("/api/v1/settings/servers/17/api-key")
    assert r.status_code == 200 and r.json()["api_key"] == "pdns-key-17"
    assert len(settings_client.sdb.audits("REVEAL_API_KEY")) == 1


# ---------------------------------------------------------------------------------------------
# Antraege WS-F9F11-BE
# ---------------------------------------------------------------------------------------------
def test_repair_flag_is_public_detail():
    assert "repair" in PUBLIC_DETAIL_KEYS
    details = {"version": 2, "zone": "example.com.", "repair": True, "client_ip": "192.0.2.1",
               "token_prefix": "dnsmgr_dyn_ab", "changes": []}
    out = public_details(details, admin=False)
    assert out["repair"] is True
    assert "client_ip" not in out and "token_prefix" not in out


def _dyndns_log(*, repair, action="DYNDNS_UPDATE"):
    snap = lambda v: {"ttl": 60, "records": [{"content": v, "disabled": False}], "comments": []}  # noqa: E731
    change = {"name": "home.example.com.", "type": "A", "before": snap("192.0.2.1"), "after": snap("192.0.2.2")}
    details = {"version": 2, "zone": "example.com.", "changes": [change], "after_source": "reread",
               "fanout": {"ns2": "saved"}}
    if repair is not None:
        details["repair"] = repair
    return SimpleNamespace(id=5, status="success", resource_type="record", action=action, details=details,
                           zone_name="example.com.")


def test_rollback_blocked_for_dyndns_repair():
    assert rh.rollback_block_reason(_dyndns_log(repair=True)) == "dyndns_repair"
    assert "dyndns_repair" in rh.BLOCK_MESSAGES
    # normale DynDNS-Aenderungen und andere Aktionen mit repair bleiben ruecksetzbar
    assert rh.rollback_block_reason(_dyndns_log(repair=None)) is None
    assert rh.rollback_block_reason(_dyndns_log(repair=False)) is None
    assert rh.rollback_block_reason(_dyndns_log(repair=True, action="UPDATE")) is None
    # fehlgeschlagene Eintraege behalten ihren Grund
    log = _dyndns_log(repair=True)
    log.status = "error"
    assert rh.rollback_block_reason(log) == "failed_action"


def _val(name, labels=None):
    return prom.REGISTRY.get_sample_value(name, labels or {}) or 0.0


def test_dyndns_metrics_count_badip_and_numhost():
    assert {"badip", "numhost"} <= set(prom.DYNDNS_RESULTS)
    before = {r: _val("pdnsmgr_dyndns_updates_total", {"result": r}) for r in ("badip", "numhost", "other")}
    prom.record_dyndns("badip")
    prom.record_dyndns("numhost")
    assert _val("pdnsmgr_dyndns_updates_total", {"result": "badip"}) == before["badip"] + 1
    assert _val("pdnsmgr_dyndns_updates_total", {"result": "numhost"}) == before["numhost"] + 1
    assert _val("pdnsmgr_dyndns_updates_total", {"result": "other"}) == before["other"]


def test_dyndns_router_counts_request_errors_by_code():
    from app.routers import dyndns as dyndns_router

    before = _val("pdnsmgr_dyndns_updates_total", {"result": "numhost"})
    resp = dyndns_router._request_error("text", "numhost", 200, dyndns_router.MSG_NUMHOST)
    assert resp.body == b"numhost"
    assert _val("pdnsmgr_dyndns_updates_total", {"result": "numhost"}) == before + 1
