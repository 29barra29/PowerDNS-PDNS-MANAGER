"""DynDNS-Endpunkte (F9 3.1-3.6, Bauplan B.13: ROUTER_ORDER 130, ``root_routers=[compat_router]``).

Update-Endpunkte mit eigener Token-Pruefung (Route-Policy ``own_auth``, keine Cookies, keine Panel-Tokens):

- ``GET /nic/update`` (dyndns2-kompatibel, Textantwort; Root-Route, nur GET),
- ``GET|POST /api/v1/dyndns/update`` (JSON), ``GET /api/v1/dyndns/whoami``.

Die Pipeline (``_handle_update``) wirft nie eine HTTPException, sondern antwortet immer selbst – so committet
``DbWrite`` die ``last_*``-Felder, Token-Sperren und Audits vor dem Senden der Antwort. Reihenfolge laut Bauplan
[S6]: Cross-Site -> global aus -> Geheimnis im Query -> **Token** -> Token-Limit (gueltiger Token) bzw. IP-Sperre
(nur ohne gueltigen Token) -> Hostnamen/IPs -> je Hostname ``dyndns_service.perform_update``.

Token-Verwaltung nur per Browser-Session (``get_session_user``; Admin-Uebersicht ``get_admin_session_user``),
``GET /dyndns/info`` mit ``get_current_user``. Zonennamen in Fehlertexten nur mit Leserecht [S5].
"""
from __future__ import annotations

import ipaddress
import json
import logging
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy import select

from app.core import metrics as prom
from app.core.auth import get_admin_session_user, get_current_user, get_session_user, is_effective_admin, \
    set_auth_context
from app.core.client_ip import get_client_ip
from app.core.config import settings
from app.core.database import DbRead, DbWrite
from app.core.rate_limit import ip_key
from app.core.request_origin import is_cross_site_browser_request
from app.core.timeutil import utcnow
from app.models.models import DynDnsToken, User
from app.schemas.dyndns import (
    MAX_HOSTNAMES,
    DynDnsHostResult,
    DynDnsTokenCreate,
    DynDnsTokenUpdate,
    DynDnsUpdateBody,
    DynDnsUpdateResponse,
)
from app.services import dyndns as dyndns_service
from app.services import ptr as ptr_service
from app.services import zone_index
from app.services.audit import write_audit

logger = logging.getLogger(__name__)

ROUTER_ORDER = 130

router = APIRouter(prefix="/dyndns", tags=["DynDNS"])
compat_router = APIRouter(tags=["DynDNS"])
root_routers = [compat_router]

Flavor = Literal["text", "json"]

AUTH_HEADER_TEXT = 'Basic realm="PDNS Manager DynDNS", charset="UTF-8"'
AUTH_HEADER_JSON = 'Basic realm="PDNS Manager DynDNS", charset="UTF-8", Bearer'
MAX_BODY_BYTES = 16 * 1024

MSG_BADAUTH = "Ungueltiger oder fehlender DynDNS-Token"
MSG_TOKEN_IN_QUERY = ("Token im Query-String ist nicht erlaubt (landet in Logs). Ein gueltiger Token wurde deshalb "
                      "deaktiviert – bitte neu erzeugen und per Basic-Auth oder Bearer senden.")
MSG_CROSS_SITE = "Browser-Anfragen von fremden Seiten sind nicht erlaubt"
MSG_DISABLED = "DynDNS ist vom Administrator deaktiviert"
MSG_RATE = "Zu viele Anfragen – bitte spaeter erneut versuchen"
MSG_ABUSE = "Zu viele Anfragen – der Token ueberschreitet das Limit um ein Vielfaches"
MSG_NO_HOST = "Kein gueltiger Hostname angegeben"
MSG_NUMHOST = f"Zu viele Hostnamen (maximal {MAX_HOSTNAMES})"
MSG_INTERNAL = "Interner Fehler – bitte spaeter erneut versuchen"
MSG_BAD_JSON = "Ungueltiger JSON-Body"
MSG_TOKEN_NOT_FOUND = "Token nicht gefunden"
MSG_FOREIGN_TOKEN = "Fremde DynDNS-Tokens koennen nur aktiviert/deaktiviert oder geloescht werden"
MSG_ONE_TIME = "Dieser Token wird nur einmal angezeigt – bitte sicher speichern."

UPDATE_PARAMS = ("hostname", "myip", "myipv4", "myipv6")


# =====================================================================================================================
# Antworten
# =====================================================================================================================
def _headers(*, retry_after: Optional[int] = None, auth: Optional[str] = None) -> dict[str, str]:
    h = {"Cache-Control": "no-store"}
    if retry_after:
        h["Retry-After"] = str(max(1, int(retry_after)))
    if auth:
        h["WWW-Authenticate"] = auth
    return h


def _request_error(flavor: Flavor, code: str, status: int, detail: str, *, retry_after: Optional[int] = None,
                   auth: bool = False) -> Response:
    """Request-weiter Fehler: Text = genau eine Zeile mit dem Code, JSON = ``{"result", "detail"}``."""
    prom.record_dyndns(code)
    header = (AUTH_HEADER_TEXT if flavor == "text" else AUTH_HEADER_JSON) if auth else None
    headers = _headers(retry_after=retry_after, auth=header)
    if flavor == "text":
        return PlainTextResponse(code, status_code=status, headers=headers)
    return JSONResponse({"result": code, "detail": detail}, status_code=status, headers=headers)


def _line(result: dict) -> str:
    code = result["result"]
    if code in ("good", "nochg") and result.get("ips"):
        return f"{code} {','.join(result['ips'])}"
    return code


def _hosts_response(flavor: Flavor, results: list[dict], *, client_ip: Optional[str], ip_source: Optional[str]) -> Response:
    codes = [r["result"] for r in results]
    for code in codes:
        prom.record_dyndns(code)
    all_911 = bool(codes) and all(c == "911" for c in codes)
    status = 503 if all_911 else 200
    headers = _headers(retry_after=dyndns_service.RETRY_AFTER_SERVER_ERROR if all_911 else None)
    if flavor == "text":
        return PlainTextResponse("\n".join(_line(r) for r in results), status_code=status, headers=headers)
    body = DynDnsUpdateResponse(
        result=dyndns_service.worst(codes), client_ip=client_ip, ip_source=ip_source,
        hosts=[DynDnsHostResult(**r) for r in results],
    )
    return JSONResponse(body.model_dump(), status_code=status, headers=headers)


# =====================================================================================================================
# Pipeline
# =====================================================================================================================
async def _authenticate(request: Request, db, flavor: Flavor, client_ip: Optional[str], hostname_hint: Optional[str]):
    """Schritte 1-4 und Rate-Limits. Rueckgabe ``(token, owner, None)`` oder ``(None, None, Response)``."""
    ipk = ip_key(client_ip)
    # 1 Cross-Site-Browser (Basic-Credentials werden vom Browser pro Origin gecacht)
    if is_cross_site_browser_request(request):
        return None, None, _request_error(flavor, "badauth", 403, MSG_CROSS_SITE)
    # 2 global abgeschaltet
    if not await dyndns_service.is_enabled(db):
        return None, None, _request_error(flavor, "badauth", 403, MSG_DISABLED)
    # 3 Geheimnis im Query -> ablehnen, gueltigen Token sofort deaktivieren
    suspicious = dyndns_service.query_secret_values(request)
    if suspicious is not None:
        await dyndns_service.revoke_tokens_in_query(db, suspicious, client_ip=client_ip)
        dyndns_service.record_auth_failure(ipk)
        return None, None, _request_error(flavor, "badauth", 401, MSG_TOKEN_IN_QUERY, auth=True)
    # 4 Token zuerst [S6]
    plaintext = dyndns_service.extract_credentials(request)
    token = await dyndns_service.verify_token(db, plaintext, remote_ip=client_ip) if plaintext else None
    owner = await dyndns_service.load_owner(db, token) if token is not None else None
    if token is None or owner is None:
        retry = dyndns_service.ip_lock_retry_after(ipk)
        if retry:
            return None, None, _request_error(flavor, "911", 429, MSG_RATE, retry_after=retry)
        dyndns_service.record_auth_failure(ipk)
        reason, user_id = "missing", None
        if token is not None:
            reason, user_id = "owner_inactive", token.user_id
        elif plaintext:
            known = await dyndns_service.find_token_by_plaintext(db, plaintext)
            reason = "inactive_token" if known is not None else "unknown_token"
            user_id = known.user_id if known is not None else None
        await dyndns_service.audit_auth_failure(ipk=ipk, client_ip=client_ip, reason=reason, hostname=hostname_hint,
                                                user_id=user_id)
        return None, None, _request_error(flavor, "badauth", 401, MSG_BADAUTH, auth=True)

    set_auth_context(request, "dyndns_token", None, username=owner.username, client_ip=client_ip)
    # Nur das Token-Limit gilt fuer gueltige Tokens (eine gesperrte IP kann weiter aktualisieren) [S6]
    code, retry = dyndns_service.token_rate_state(token.id)
    if code is not None:
        token.last_result = code
        detail = MSG_ABUSE if code == "abuse" else MSG_RATE
        logger.info("DynDNS %s: Rate-Limit (%s)", token.token_prefix, code)
        return None, None, _request_error(flavor, code, 429, detail, retry_after=retry)
    return token, owner, None


async def _run_update(request: Request, db, params: dict, flavor: Flavor, client_ip: Optional[str]) -> Response:
    hostname_param = params.get("hostname")
    token, owner, err = await _authenticate(request, db, flavor, client_ip, hostname_param)
    if err is not None:
        return err

    raw_hosts = [h.strip() for h in str(hostname_param or "").split(",") if h.strip()]
    if not raw_hosts:
        hosts = dyndns_service.token_hostnames(token)
        if len(hosts) != 1:
            token.last_result = "notfqdn"
            return _request_error(flavor, "notfqdn", 200 if flavor == "text" else 400, MSG_NO_HOST)
        raw_hosts = hosts
    if len(raw_hosts) > MAX_HOSTNAMES:
        token.last_result = "numhost"
        return _request_error(flavor, "numhost", 200 if flavor == "text" else 400, MSG_NUMHOST)

    try:
        ips = dyndns_service.parse_ips(params.get("myip"), params.get("myipv4"), params.get("myipv6"), client_ip,
                                       allow_private=await dyndns_service.allow_private(db))
    except dyndns_service.DynDnsError as exc:
        token.last_result = "badip"
        await dyndns_service.maybe_audit_failure(
            key=(token.id, "*", "badip"), ttl_sec=dyndns_service.FAILURE_AUDIT_TTL, action="DYNDNS_UPDATE",
            resource_type="record", resource_name=raw_hosts[0][:255], user_id=owner.id, error_message=exc.detail,
            details={"token_id": token.id, "token_name": token.name, "token_prefix": token.token_prefix,
                     "hostname": raw_hosts[0][:255], "client_ip": client_ip, "result": "badip", "reason": "ip"},
        )
        return _request_error(flavor, "badip", 200 if flavor == "text" else 400, exc.detail)

    results = []
    for raw in raw_hosts:
        results.append(await dyndns_service.perform_update(db, token=token, owner=owner, hostname_raw=raw, ips=ips,
                                                           client_ip=client_ip))
    codes = [r["result"] for r in results]
    token.last_result = dyndns_service.worst(codes)
    for r in results:
        if r["result"] == "good":
            logger.info("DynDNS %s: %s -> %s", token.token_prefix, r["hostname"], ",".join(r["ips"]))
        else:
            logger.debug("DynDNS %s: %s -> %s", token.token_prefix, r["hostname"], r["result"])
    return _hosts_response(flavor, results, client_ip=client_ip, ip_source=ips.source)


async def _handle_update(request: Request, db, params: dict, *, flavor: Flavor) -> Response:
    """Gesamte Update-Pipeline; antwortet immer selbst (nie HTTPException), interne Fehler -> 911/503."""
    client_ip = get_client_ip(request)
    set_auth_context(request, None, client_ip=client_ip)
    try:
        return await _run_update(request, db, params, flavor, client_ip)
    except Exception:  # noqa: BLE001 - Router bekommen immer eine dyndns2-Antwort
        logger.exception("DynDNS-Update fehlgeschlagen")
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return _request_error(flavor, "911", 503, MSG_INTERNAL, retry_after=dyndns_service.RETRY_AFTER_SERVER_ERROR)


def _query_params(request: Request) -> dict:
    return {k: request.query_params.get(k) for k in UPDATE_PARAMS}


@compat_router.get("/nic/update", include_in_schema=False)
async def nic_update(request: Request, db: DbWrite):
    """dyndns2-kompatibler Update-Endpunkt (FRITZ!Box, ddclient, inadyn); Token per Basic-Auth oder Bearer."""
    return await _handle_update(request, db, _query_params(request), flavor="text")


@router.get("/update", response_model=DynDnsUpdateResponse)
async def dyndns_update_get(request: Request, db: DbWrite):
    """DynDNS-Update mit JSON-Antwort (Query wie ``/nic/update``)."""
    return await _handle_update(request, db, _query_params(request), flavor="json")


@router.post("/update", response_model=DynDnsUpdateResponse)
async def dyndns_update_post(request: Request, db: DbWrite):
    """DynDNS-Update mit optionalem JSON-Body (``DynDnsUpdateBody``, ueberschreibt die Query-Werte)."""
    params = _query_params(request)
    raw = await request.body()
    if raw and raw.strip():
        try:
            if len(raw) > MAX_BODY_BYTES:
                raise ValueError("zu gross")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("kein Objekt")
            body = DynDnsUpdateBody.model_validate(data)
        except ValueError:
            return JSONResponse({"result": "badrequest", "detail": MSG_BAD_JSON}, status_code=400,
                                headers=_headers())
        for key in UPDATE_PARAMS:
            value = getattr(body, key)
            if value is not None:
                params[key] = value
    return await _handle_update(request, db, params, flavor="json")


@router.get("/whoami")
async def dyndns_whoami(request: Request, db: DbWrite):
    """Prueft einen DynDNS-Token (gleiche Auth-/Rate-Limit-Pipeline wie das Update, keine Aenderung)."""
    client_ip = get_client_ip(request)
    set_auth_context(request, None, client_ip=client_ip)
    try:
        token, owner, err = await _authenticate(request, db, "json", client_ip, None)
        if err is not None:
            return err
        return JSONResponse({
            "ok": True, "token_name": token.name, "token_prefix": token.token_prefix,
            "hostnames": dyndns_service.token_hostnames(token), "allowed_types": dyndns_service.token_types(token),
            "ttl": token.ttl, "update_ptr": bool(token.update_ptr), "client_ip": client_ip,
        }, headers=_headers())
    except Exception:  # noqa: BLE001
        logger.exception("DynDNS-whoami fehlgeschlagen")
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        return _request_error("json", "911", 503, MSG_INTERNAL, retry_after=dyndns_service.RETRY_AFTER_SERVER_ERROR)


# =====================================================================================================================
# Info und Zonen
# =====================================================================================================================
def _peer_is_private(request: Request) -> bool:
    peer = request.client.host if request.client else None
    try:
        ip = ipaddress.ip_address((peer or "").strip())
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


@router.get("/info")
async def dyndns_info(request: Request, db: DbRead, current_user: User = Depends(get_current_user)):
    """Konfiguration fuer die DynDNS-Karte (Basis-URL, Grenzen, global an/aus).

    Nur fuer Admins: ``trust_proxy_headers`` und ein echter Wert fuer ``proxy_warning`` (direkte Peer-IP privat und
    ``TRUST_PROXY_HEADERS`` aus – dann sehen Rate-Limits und IP-Autoerkennung nur den Proxy) [S6].
    """
    from app.services.password_reset_mail import resolve_public_base_url

    admin = is_effective_admin(current_user)
    base_url = await resolve_public_base_url(db)
    out: dict[str, Any] = {
        "enabled": await dyndns_service.is_enabled(db),
        "base_url": base_url or None,
        "allow_private_ips": await dyndns_service.allow_private(db),
        "ttl_min": dyndns_service.MIN_TTL,
        "ttl_max": dyndns_service.MAX_TTL,
        "ttl_default": dyndns_service.DEFAULT_TTL,
        "max_hostnames": dyndns_service.MAX_HOSTNAMES,
        "max_tokens": dyndns_service.MAX_TOKENS_PER_USER,
        "update_path": "/nic/update",
        "api_path": "/api/v1/dyndns/update",
        "proxy_warning": bool(admin and not settings.TRUST_PROXY_HEADERS and _peer_is_private(request)),
    }
    if admin:
        out["trust_proxy_headers"] = bool(settings.TRUST_PROXY_HEADERS)
    return out


@router.get("/zones")
async def dyndns_zones(db: DbRead, current_user: User = Depends(get_session_user)):
    """Zonen mit Schreibrecht auf schreibbaren Servern (Auswahl im Token-Dialog), sortiert nach Name."""
    zmap = await zone_index.writable_zone_map(db)
    allowed = await ptr_service.writable_zone_filter(db, current_user)
    zones: dict[str, list[str]] = {}
    for server, names in zmap.items():
        for z in names:
            if allowed is None or z in allowed:
                zones.setdefault(z, []).append(server)
    return {"zones": [{"name": z, "servers": servers} for z, servers in sorted(zones.items())]}


# =====================================================================================================================
# Token-Verwaltung (nur Browser-Session)
# =====================================================================================================================
async def _load_token(db, token_id: int) -> DynDnsToken:
    token = (await db.execute(select(DynDnsToken).where(DynDnsToken.id == token_id))).scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=404, detail=MSG_TOKEN_NOT_FOUND)
    return token


@router.get("/tokens")
async def list_dyndns_tokens(db: DbRead, current_user: User = Depends(get_session_user)):
    """Eigene DynDNS-Tokens, neueste zuerst, mit Zustand je Hostname."""
    rows = (await db.execute(
        select(DynDnsToken).where(DynDnsToken.user_id == current_user.id).order_by(DynDnsToken.id.desc())
    )).scalars().all()
    tokens = []
    zone_names = await dyndns_service.zone_names_snapshot(db) if rows else None
    acl_cache: dict = {}
    for t in rows:
        status = await dyndns_service.hostname_status(db, t, current_user, zone_names=zone_names, acl_cache=acl_cache)
        tokens.append(dyndns_service.serialize_token(t, hostname_status=status))
    return {"tokens": tokens, "max_tokens": dyndns_service.MAX_TOKENS_PER_USER}


@router.post("/tokens", status_code=201)
async def create_dyndns_token(data: DynDnsTokenCreate, db: DbWrite, current_user: User = Depends(get_session_user)):
    """Legt einen Token an; der Klartext steht genau einmal in der Antwort."""
    if not await dyndns_service.is_enabled(db):
        raise HTTPException(status_code=403, detail=MSG_DISABLED)
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Token-Name darf nicht leer sein")
    if await dyndns_service.count_tokens_of_user(db, current_user.id) >= dyndns_service.MAX_TOKENS_PER_USER:
        raise HTTPException(status_code=400,
                            detail=f"Maximal {dyndns_service.MAX_TOKENS_PER_USER} DynDNS-Tokens pro Benutzer")
    validated = await dyndns_service.validate_hostnames_for_user(db, current_user, data.hostnames)
    token, plain = await dyndns_service.create_token(
        db, user=current_user, name=name, hostnames=[v["hostname"] for v in validated],
        allowed_types=list(data.allowed_types), ttl=data.ttl, update_ptr=data.update_ptr,
    )
    await write_audit(db, "DYNDNS_TOKEN_CREATE", "dyndns_token", token.name, user_id=current_user.id,
                      details=dyndns_service.audit_token_details(token))
    status = [{"hostname": v["hostname"], "zone": v["zone"], "status": "ok"} for v in validated]
    return {"token": dyndns_service.serialize_token(token, hostname_status=status), "plaintext_token": plain,
            "warning": MSG_ONE_TIME}


@router.put("/tokens/{token_id}")
async def update_dyndns_token(token_id: int, data: DynDnsTokenUpdate, db: DbWrite,
                              current_user: User = Depends(get_session_user)):
    """Eigentuemer: alle Felder (Hostnamen gegen die eigenen Rechte). Admin bei fremdem Token: nur ``is_active``."""
    token = await _load_token(db, token_id)
    owner = token.user_id == current_user.id
    if not owner and not is_effective_admin(current_user):
        raise HTTPException(status_code=404, detail=MSG_TOKEN_NOT_FOUND)
    fields = data.model_dump(exclude_unset=True)
    if not owner and any(k != "is_active" for k, v in fields.items() if v is not None):
        raise HTTPException(status_code=403, detail=MSG_FOREIGN_TOKEN)

    changed: dict[str, dict] = {}

    def _set(field: str, value: Any) -> None:
        old = getattr(token, field)
        if old != value:
            changed[field] = {"from": old, "to": value}
            setattr(token, field, value)

    if data.name is not None:
        name = data.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Token-Name darf nicht leer sein")
        _set("name", name[:100])
    if data.hostnames is not None:
        validated = await dyndns_service.validate_hostnames_for_user(db, current_user, data.hostnames)
        _set("hostnames", [v["hostname"] for v in validated])
    if data.allowed_types is not None:
        _set("allowed_types", list(data.allowed_types))
    if data.ttl is not None:
        _set("ttl", int(data.ttl))
    if data.update_ptr is not None:
        _set("update_ptr", bool(data.update_ptr))
    if data.is_active is not None:
        _set("is_active", bool(data.is_active))
    if "hostnames" in changed:
        dyndns_service.prune_stale(token)
    if changed:
        token.updated_at = utcnow()
        details = dyndns_service.audit_token_details(token)
        details["changed"] = changed
        await write_audit(db, "DYNDNS_TOKEN_UPDATE", "dyndns_token", token.name, user_id=current_user.id,
                          details=details)
    status = await dyndns_service.hostname_status(db, token, current_user) if owner else None
    return dyndns_service.serialize_token(token, hostname_status=status)


@router.post("/tokens/{token_id}/rotate")
async def rotate_dyndns_token(token_id: int, db: DbWrite, current_user: User = Depends(get_session_user)):
    """Neues Secret (nur Eigentuemer); der bisherige Token ist sofort ungueltig."""
    token = await _load_token(db, token_id)
    if token.user_id != current_user.id:
        raise HTTPException(status_code=404, detail=MSG_TOKEN_NOT_FOUND)
    plain = await dyndns_service.rotate_token(db, token)
    await write_audit(db, "DYNDNS_TOKEN_ROTATE", "dyndns_token", token.name, user_id=current_user.id,
                      details=dyndns_service.audit_token_details(token))
    status = await dyndns_service.hostname_status(db, token, current_user)
    return {"token": dyndns_service.serialize_token(token, hostname_status=status), "plaintext_token": plain,
            "warning": MSG_ONE_TIME}


@router.delete("/tokens/{token_id}")
async def delete_dyndns_token(token_id: int, db: DbWrite, current_user: User = Depends(get_session_user)):
    """Loescht einen Token (Eigentuemer oder Admin, endgueltig)."""
    token = await _load_token(db, token_id)
    if token.user_id != current_user.id and not is_effective_admin(current_user):
        raise HTTPException(status_code=404, detail=MSG_TOKEN_NOT_FOUND)
    details = dyndns_service.audit_token_details(token)
    name = token.name
    await db.delete(token)
    await db.flush()
    await write_audit(db, "DYNDNS_TOKEN_DELETE", "dyndns_token", name, user_id=current_user.id, details=details)
    return {"message": "DynDNS-Token geloescht"}


@router.get("/admin/tokens")
async def list_all_dyndns_tokens(db: DbRead, admin: User = Depends(get_admin_session_user)):
    """Alle DynDNS-Tokens mit Besitzer (ohne Zustand je Hostname), neueste zuerst."""
    rows = (await db.execute(select(DynDnsToken).order_by(DynDnsToken.id.desc()))).scalars().all()
    ids = sorted({t.user_id for t in rows})
    names: dict[int, str] = {}
    if ids:
        names = {int(uid): uname for uid, uname in
                 (await db.execute(select(User.id, User.username).where(User.id.in_(ids)))).all()}
    return {"tokens": [dyndns_service.serialize_token(t, owner_username=names.get(t.user_id, "")) for t in rows]}
