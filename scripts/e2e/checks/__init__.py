"""E2E-Check-Framework fuer PDNS Manager (Runner, ``ctx``-API, Discovery).

Jede Datei ``checks/<ws>.py`` gehoert ihrem Feature-Workstream und definiert eine oder mehrere
der Funktionen

    check_fresh(ctx)            – nach einer Neuinstallation (run-e2e.sh)
    check_upgrade(ctx)          – nach dem Upgrade einer 2.4.1-Datenbank (upgrade-241-to-30.sh)
    check_upgrade_restart(ctx)  – optional: nach einem zweiten Start derselben Upgrade-Instanz

Der Runner wird im Werkzeug-Container (compose-Dienst ``tools``) gestartet:

    python -c 'import checks, sys; sys.exit(checks.main())' fresh [--only base,f5] [--list]

Die vollstaendige Beschreibung der ``ctx``-API steht in ``checks/README.md``. Nur
Standardbibliothek plus ``pymysql`` (liegt im Backend-Image).
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import importlib
import json
import os
import pkgutil
import secrets
import struct
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Any, Callable, Iterable

__all__ = ["CheckFailed", "CheckSkipped", "Ctx", "Response", "main"]

MODES = {
    "fresh": "check_fresh",
    "upgrade": "check_upgrade",
    "upgrade-restart": "check_upgrade_restart",
}
STATE_DIR = os.environ.get("E2E_STATE_DIR", "/state")
_UNSET = object()


class CheckFailed(AssertionError):
    """Ein Check ist fehlgeschlagen (erwartete Bedingung nicht erfuellt)."""


class CheckSkipped(Exception):
    """Ein Check ist in dieser Umgebung nicht anwendbar (wird als SKIP gezaehlt, nicht als Fehler)."""


# ---------------------------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------------------------
class Response:
    """Antwort eines HTTP-Aufrufs (auch bei 4xx/5xx – nie eine Exception)."""

    def __init__(self, method: str, url: str, status: int, headers: dict, body: bytes):
        self.method = method
        self.url = url
        self.status = status
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.body = body
        self._json = _UNSET

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        if self._json is _UNSET:
            try:
                self._json = json.loads(self.text) if self.body else None
            except ValueError as exc:
                raise CheckFailed(f"{self.method} {self.url}: Antwort ist kein JSON ({exc}): {self.text[:300]}")
        return self._json

    def __repr__(self) -> str:
        return f"<Response {self.method} {self.url} -> {self.status}>"


def _api_url(base_url: str, path: str) -> str:
    if path.startswith(("http://", "https://")):
        return path
    if path.startswith("/"):
        return base_url + path
    return f"{base_url}/api/v1/{path}"


def _expect_ok(resp: Response, expect, what: str) -> Response:
    if expect is None:
        return resp
    wanted = (expect,) if isinstance(expect, int) else tuple(expect)
    if resp.status not in wanted:
        raise CheckFailed(f"{what}: erwartet HTTP {'/'.join(map(str, wanted))}, erhalten {resp.status}: {resp.text[:500]}")
    return resp


class HttpSession:
    """HTTP-Client mit eigenem Cookie-Speicher und optionalem Bearer-Token.

    Fuer Session-Logins (Cookie) und fuer Token-Aufrufe. ``path`` ohne fuehrenden ``/`` wird
    relativ zu ``/api/v1/`` aufgeloest (``"zones/ns1"`` -> ``/api/v1/zones/ns1``).
    """

    def __init__(self, base_url: str, token: str | None = None, label: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.label = label
        self.cookies = CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def request(self, method: str, path: str, *, json_body: Any = _UNSET, form: dict | None = None,
                body: bytes | None = None, headers: dict | None = None, token: Any = _UNSET,
                expect=None, timeout: float = 60) -> Response:
        url = _api_url(self.base_url, path)
        hdrs = {"Accept": "application/json"}
        data = None
        if json_body is not _UNSET:
            data = json.dumps(json_body).encode()
            hdrs["Content-Type"] = "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = body
        tok = self.token if token is _UNSET else token
        if tok:
            hdrs["Authorization"] = f"Bearer {tok}"
        hdrs.update(headers or {})
        req = urllib.request.Request(url, data=data, method=method.upper(), headers=hdrs)
        try:
            with self._opener.open(req, timeout=timeout) as r:
                resp = Response(method.upper(), url, r.status, dict(r.headers), r.read())
        except urllib.error.HTTPError as e:
            resp = Response(method.upper(), url, e.code, dict(e.headers or {}), e.read() or b"")
        what = f"{method.upper()} {path}" + (f" [{self.label}]" if self.label else "")
        return _expect_ok(resp, expect, what)

    # Kurzformen
    def api(self, method: str, path: str, **kw) -> Response:
        return self.request(method, path, **kw)

    def get(self, path: str, **kw) -> Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, json: Any = _UNSET, **kw) -> Response:  # noqa: A002 - Lesbarkeit
        return self.request("POST", path, json_body=json, **kw)

    def put(self, path: str, json: Any = _UNSET, **kw) -> Response:  # noqa: A002
        return self.request("PUT", path, json_body=json, **kw)

    def delete(self, path: str, json: Any = _UNSET, **kw) -> Response:  # noqa: A002
        return self.request("DELETE", path, json_body=json, **kw)


# ---------------------------------------------------------------------------------------------
# PowerDNS direkt
# ---------------------------------------------------------------------------------------------
class PdnsClient:
    """Direkter Zugriff auf die PowerDNS-Test-Server (an PDNS Manager vorbei, zum Nachpruefen)."""

    def __init__(self, spec: str):
        self._servers: dict[str, dict] = {}
        for part in (spec or "").split(","):
            part = part.strip()
            if not part:
                continue
            name, rest = part.split("=", 1)
            url, key = rest.split("|", 1)
            self._servers[name.strip()] = {"url": url.strip().rstrip("/"), "api_key": key.strip()}

    @property
    def servers(self) -> list[str]:
        return list(self._servers)

    def url(self, server: str) -> str:
        return self._servers[server]["url"]

    def api_key(self, server: str) -> str:
        return self._servers[server]["api_key"]

    def request(self, server: str, method: str, path: str, json_body: Any = None, expect=None) -> Response:
        srv = self._servers[server]
        url = f"{srv['url']}/api/v1/servers/localhost{path}"
        data = json.dumps(json_body).encode() if json_body is not None else None
        req = urllib.request.Request(url, data=data, method=method.upper(), headers={
            "X-API-Key": srv["api_key"], "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                resp = Response(method.upper(), url, r.status, dict(r.headers), r.read())
        except urllib.error.HTTPError as e:
            resp = Response(method.upper(), url, e.code, dict(e.headers or {}), e.read() or b"")
        return _expect_ok(resp, expect, f"PowerDNS {server} {method.upper()} {path}")

    @staticmethod
    def _zid(zone: str) -> str:
        z = zone if zone.endswith(".") else zone + "."
        return urllib.parse.quote(z, safe=".")

    def zones(self, server: str) -> list[str]:
        return [z["name"] for z in self.request(server, "GET", "/zones", expect=200).json()]

    def zone(self, server: str, zone: str) -> dict | None:
        r = self.request(server, "GET", f"/zones/{self._zid(zone)}")
        if r.status in (404, 422):
            return None
        _expect_ok(r, 200, f"PowerDNS {server} GET zone {zone}")
        return r.json()

    def rrset(self, server: str, zone: str, name: str, rtype: str) -> dict | None:
        z = self.zone(server, zone)
        if z is None:
            return None
        name = name if name.endswith(".") else name + "."
        for rr in z.get("rrsets", []):
            if rr.get("name") == name and rr.get("type") == rtype.upper():
                return rr
        return None

    def contents(self, server: str, zone: str, name: str, rtype: str) -> list[str]:
        rr = self.rrset(server, zone, name, rtype)
        return sorted(r["content"] for r in (rr or {}).get("records", []))

    def export(self, server: str, zone: str) -> str:
        return self.request(server, "GET", f"/zones/{self._zid(zone)}/export", expect=200).text

    def create_zone(self, server: str, zone: str, nameservers: Iterable[str] = (), rrsets: list | None = None) -> None:
        body = {"name": zone, "kind": "Native", "nameservers": list(nameservers)}
        if rrsets:
            body["rrsets"] = rrsets
        self.request(server, "POST", "/zones", body, expect=(201, 409))

    def delete_zone(self, server: str, zone: str) -> bool:
        r = self.request(server, "DELETE", f"/zones/{self._zid(zone)}")
        return r.status in (200, 204)


# ---------------------------------------------------------------------------------------------
# Webhook-Empfaenger
# ---------------------------------------------------------------------------------------------
class Receiver:
    """Zugriff auf den Webhook-Empfaenger (Dienst ``receiver``, siehe webhook-receiver.py)."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._http = HttpSession(self.base_url, label="receiver")

    def url(self, name: str = "default", **params) -> str:
        """Ziel-URL fuer eine Webhook-Konfiguration (aus Sicht des Backends im E2E-Netz)."""
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        return f"{self.base_url}/hook/{name}{q}"

    def deliveries(self, name: str | None = None) -> list[dict]:
        path = "/deliveries" + (f"?path={urllib.parse.quote('/hook/' + name)}" if name else "")
        return self._http.get(path, expect=200).json()

    def clear(self) -> None:
        self._http.delete("/deliveries", expect=200)

    def wait_for(self, name: str | None = None, count: int = 1, timeout: float = 30,
                 predicate: Callable[[dict], bool] | None = None) -> list[dict]:
        """Wartet, bis mindestens ``count`` passende Zustellungen da sind; gibt sie zurueck (sonst CheckFailed)."""
        deadline = time.time() + timeout
        found: list[dict] = []
        while time.time() < deadline:
            found = [d for d in self.deliveries(name) if predicate is None or predicate(d)]
            if len(found) >= count:
                return found
            time.sleep(0.5)
        raise CheckFailed(f"Webhook-Empfaenger: {len(found)} statt {count} Zustellung(en) fuer {name or '*'} nach {timeout} s")

    @staticmethod
    def raw_body(delivery: dict) -> bytes:
        return base64.b64decode(delivery.get("body_b64") or "")

    @staticmethod
    def hmac_sha256(secret: str, raw: bytes) -> str:
        return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------------------------
# TOTP (RFC 6238, SHA-1, 6 Stellen, 30 s) ohne Zusatzpaket
# ---------------------------------------------------------------------------------------------
def totp_code(secret: str, for_time: float | None = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    counter = int((time.time() if for_time is None else for_time) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


# ---------------------------------------------------------------------------------------------
# Kontext
# ---------------------------------------------------------------------------------------------
class Ctx:
    """Kontext fuer einen Check-Lauf. Beschreibung der API: checks/README.md."""

    def __init__(self, mode: str):
        env = os.environ
        self.mode = mode
        self.base_url = env.get("E2E_BASE_URL", "http://backend:8000").rstrip("/")
        self.pdns = PdnsClient(env.get("E2E_PDNS", ""))
        self.receiver = Receiver(env.get("E2E_RECEIVER_URL", "http://receiver:8080"))
        self.state: dict = {}
        self.seed: dict = {}
        self.admin_username = "admin"
        self.admin_password = env.get("E2E_ADMIN_PASSWORD", "")
        self.admin_token: str | None = None
        self.user_token: str | None = None
        self.user_id: int | None = None
        self.user_name: str | None = None
        self.user_password: str | None = None
        self.user_totp_secret: str | None = None
        self.current_check: str | None = None
        self._admin_session: HttpSession | None = None
        self._user_session: HttpSession | None = None
        self._cleanups: list[tuple[str, Callable[[], Any]]] = []
        self._totp_used: dict[str, int] = {}
        self._db_conn = None
        self._db_cfg = {
            "host": env.get("E2E_DB_HOST", "db"),
            "port": int(env.get("E2E_DB_PORT", "3306")),
            "user": env.get("E2E_DB_USER", "root"),
            "password": env.get("E2E_DB_PASSWORD", ""),
            "database": env.get("E2E_DB_NAME", "dns_manager"),
        }

    # --- Ausgabe / Zusicherungen ---------------------------------------------------------
    def log(self, msg: str) -> None:
        prefix = f"[{self.current_check}] " if self.current_check else ""
        print(f"    {prefix}{msg}", flush=True)

    @contextlib.contextmanager
    def step(self, title: str):
        """Benannter Teilschritt: erscheint im Log, Fehlermeldungen tragen den Schrittnamen."""
        self.log(f"- {title}")
        try:
            yield
        except CheckFailed as exc:
            raise CheckFailed(f"{title}: {exc}") from None

    def check(self, condition: Any, msg: str) -> None:
        if not condition:
            raise CheckFailed(msg)

    def eq(self, actual: Any, expected: Any, msg: str = "") -> None:
        if actual != expected:
            raise CheckFailed(f"{msg + ': ' if msg else ''}erwartet {expected!r}, erhalten {actual!r}")

    def fail(self, msg: str) -> None:
        raise CheckFailed(msg)

    def skip(self, msg: str) -> None:
        raise CheckSkipped(msg)

    def wait_until(self, fn: Callable[[], Any], timeout: float = 30, interval: float = 0.5, msg: str = "") -> Any:
        """Ruft ``fn`` wiederholt auf, bis es einen wahren Wert liefert (wird zurueckgegeben)."""
        deadline = time.time() + timeout
        last_exc: Exception | None = None
        while time.time() < deadline:
            try:
                val = fn()
                if val:
                    return val
                last_exc = None
            except CheckFailed as exc:
                last_exc = exc
            time.sleep(interval)
        raise CheckFailed(f"Zeitueberschreitung nach {timeout} s: {msg or getattr(fn, '__name__', 'Bedingung')}"
                          + (f" (zuletzt: {last_exc})" if last_exc else ""))

    # --- Namen / Aufraeumen ---------------------------------------------------------------
    def unique(self, prefix: str = "e2e") -> str:
        return f"{prefix}-{secrets.token_hex(4)}"

    def unique_zone(self, prefix: str = "e2e") -> str:
        """Eindeutiger Zonenname unter e2e.test. (mit Punkt am Ende)."""
        return f"{self.unique(prefix)}.e2e.test."

    def cleanup(self, fn: Callable[[], Any], label: str = "") -> None:
        """Registriert eine Aufraeumfunktion; laeuft nach dem Check (auch bei Fehler), LIFO."""
        self._cleanups.append((label or getattr(fn, "__name__", "cleanup"), fn))

    def _run_cleanups(self) -> list[str]:
        problems = []
        while self._cleanups:
            label, fn = self._cleanups.pop()
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 - Aufraeumen darf den Lauf nicht abbrechen
                problems.append(f"{label}: {exc}")
        return problems

    # --- HTTP gegen PDNS Manager -------------------------------------------------------------
    def http(self, token: str | None = None, label: str = "") -> HttpSession:
        """Neuer HTTP-Client (eigene Cookies) – z. B. fuer anonyme Aufrufe oder fremde Tokens."""
        return HttpSession(self.base_url, token=token, label=label)

    def api(self, method: str, path: str, *, json: Any = _UNSET, form: dict | None = None,  # noqa: A002
            body: bytes | None = None, headers: dict | None = None, token: Any = _UNSET,
            expect=None, timeout: float = 60) -> Response:
        """Ruft die API auf; Standard-Authentifizierung ist ``ctx.admin_token`` (``token=None`` = anonym)."""
        tok = self.admin_token if token is _UNSET else token
        label = "admin_token" if token is _UNSET else ("anonym" if tok is None else "token")
        return HttpSession(self.base_url, label=label).request(
            method, path, json_body=json, form=form, body=body, headers=headers, token=tok,
            expect=expect, timeout=timeout)

    def login(self, username: str, password: str, totp_secret: str | None = None,
              expect_ok: bool = True) -> tuple[Response, HttpSession]:
        """Passwort-Login (+ TOTP-Zweitschritt, falls noetig). Gibt (letzte Antwort, Session) zurueck."""
        sess = HttpSession(self.base_url, label=f"session:{username}")
        resp = sess.request("POST", "auth/login", form={"username": username, "password": password})
        if resp.status == 200 and isinstance(resp.json(), dict) and resp.json().get("need_two_factor"):
            if not totp_secret:
                raise CheckFailed(f"Login {username}: 2FA verlangt, aber kein TOTP-Geheimnis bekannt")
            resp = sess.request("POST", "auth/login/2fa", json_body={
                "two_factor_token": resp.json()["two_factor_token"], "totp_code": self.totp(totp_secret)})
        if expect_ok:
            _expect_ok(resp, 200, f"Login {username}")
        return resp, sess

    def session(self, username: str, password: str, totp_secret: str | None = None) -> HttpSession:
        """Eingeloggte Cookie-Session (fuer Endpunkte, die eine Browser-Session verlangen)."""
        return self.login(username, password, totp_secret)[1]

    @property
    def admin_session(self) -> HttpSession:
        if self._admin_session is None:
            self._admin_session = self.session(self.admin_username, self.admin_password)
        return self._admin_session

    @property
    def user_session(self) -> HttpSession:
        if self._user_session is None:
            if not self.user_name:
                raise CheckFailed("Kein Testbenutzer bekannt")
            self._user_session = self.session(self.user_name, self.user_password or "", self.user_totp_secret)
        return self._user_session

    def totp(self, secret: str) -> str:
        """Aktueller TOTP-Code; wartet auf das naechste Zeitfenster, falls der Code schon benutzt wurde
        (das Backend lehnt Wiederverwendung ab)."""
        step = int(time.time() // 30)
        if self._totp_used.get(secret, -1) >= step:
            wait = 30 - (time.time() % 30) + 0.5
            self.log(f"TOTP: warte {wait:.0f} s auf ein neues Zeitfenster")
            time.sleep(wait)
            step = int(time.time() // 30)
        self._totp_used[secret] = step
        return totp_code(secret)

    def set_user_zones(self, user_id: int, zones: dict[str, str]) -> None:
        """Setzt die Zonenrechte eines Benutzers (ersetzt alle bisherigen): {zone: "read"|"manage"}."""
        self.admin_session.put(f"auth/users/{user_id}/zones",
                               json={"zones": list(zones), "zone_permissions": zones}, expect=200)

    # --- Datenbank ---------------------------------------------------------------------------
    def _db(self):
        if self._db_conn is None:
            import pymysql  # im Backend-Image vorhanden (Abhaengigkeit von aiomysql)
            self._db_conn = pymysql.connect(cursorclass=pymysql.cursors.DictCursor, autocommit=True,
                                            charset="utf8mb4", connect_timeout=10, **self._db_cfg)
        return self._db_conn

    def db(self, sql: str, params: Any = None) -> list[dict]:
        """Fuehrt SQL direkt in der Panel-Datenbank aus (autocommit). SELECT -> Liste von Dicts."""
        conn = self._db()
        conn.ping(reconnect=True)
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return list(cur.fetchall() or [])

    def db_value(self, sql: str, params: Any = None) -> Any:
        rows = self.db(sql, params)
        return next(iter(rows[0].values())) if rows else None

    # --- Sonstiges ---------------------------------------------------------------------------
    def backend_log(self) -> str:
        """Container-Log des Backends (Schnappschuss, den das Skript vor dem Lauf abgelegt hat)."""
        path = os.path.join(STATE_DIR, f"backend-{self.mode}.log")
        if not os.path.exists(path):
            path = os.path.join(STATE_DIR, "backend.log")
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                return fh.read()
        except OSError:
            return ""

    def wait_healthy(self, timeout: float = 120) -> None:
        anon = HttpSession(self.base_url, label="health")

        def _ok():
            try:
                return anon.get("/health").status == 200
            except (urllib.error.URLError, OSError):
                return False
        self.wait_until(_ok, timeout=timeout, interval=1, msg="GET /health")


# ---------------------------------------------------------------------------------------------
# Bootstrap je Modus
# ---------------------------------------------------------------------------------------------
def _bootstrap_fresh(ctx: Ctx) -> None:
    if not ctx.admin_password:
        raise CheckFailed("E2E_ADMIN_PASSWORD fehlt")
    admin = ctx.admin_session
    tok = admin.post("auth/me/panel-tokens", json={"name": "e2e-admin", "allow_admin": True}, expect=(200, 201)).json()
    ctx.admin_token = tok["plaintext_token"]
    ctx.user_name = "e2e-user"
    ctx.user_password = "E2e-User-" + secrets.token_urlsafe(9)
    created = admin.post("auth/users", json={
        "username": ctx.user_name, "password": ctx.user_password, "email": "e2e-user@e2e.test",
        "display_name": "E2E User", "role": "user"}, expect=(200, 201)).json()
    ctx.user_id = created.get("id") or (created.get("user") or {}).get("id")
    if not ctx.user_id:
        raise CheckFailed(f"Benutzer angelegt, aber keine ID in der Antwort: {created}")
    utok = ctx.user_session.post("auth/me/panel-tokens", json={"name": "e2e-user"}, expect=(200, 201)).json()
    ctx.user_token = utok["plaintext_token"]


def _bootstrap_upgrade(ctx: Ctx) -> None:
    path = os.path.join(STATE_DIR, "seed.json")
    try:
        with open(path, encoding="utf-8") as fh:
            ctx.seed = json.load(fh)
    except OSError as exc:
        raise CheckFailed(f"Seed-Datei {path} nicht lesbar: {exc}")
    ctx.admin_username = ctx.seed["admin"]["username"]
    ctx.admin_password = ctx.seed["admin"]["password"]
    ctx.admin_token = ctx.seed["tokens"]["admin"]
    alice = ctx.seed["users"]["alice"]
    ctx.user_name = alice["username"]
    ctx.user_password = alice["password"]
    ctx.user_id = alice["id"]
    ctx.user_totp_secret = alice["totp_secret"]
    ctx.user_token = ctx.seed["tokens"]["alice"]


def _load_state(ctx: Ctx) -> None:
    path = os.path.join(STATE_DIR, "ctx-state.json")
    if ctx.mode == "upgrade-restart" and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            ctx.state = json.load(fh)
    # Benutzte TOTP-Zeitfenster gelten ueber Runner-Laeufe hinweg (Replay-Schutz im Backend).
    ctx._totp_used = dict(ctx.state.pop("__totp_used", {}) or {})


def _save(ctx: Ctx, report: dict) -> None:
    if not os.path.isdir(STATE_DIR) or not os.access(STATE_DIR, os.W_OK):
        return
    with open(os.path.join(STATE_DIR, "ctx-state.json"), "w", encoding="utf-8") as fh:
        json.dump({**ctx.state, "__totp_used": ctx._totp_used}, fh, indent=1, default=str)
    with open(os.path.join(STATE_DIR, f"report-{ctx.mode}.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1, default=str)


# ---------------------------------------------------------------------------------------------
# Discovery und Runner
# ---------------------------------------------------------------------------------------------
def discover() -> list[str]:
    """Alle Check-Module (``checks/*.py`` ohne ``_``-Praefix), ``base`` zuerst, Rest alphabetisch."""
    names = [m.name for m in pkgutil.iter_modules(__path__) if not m.name.startswith("_") and not m.ispkg]
    return sorted(names, key=lambda n: (n != "base", n))


def _parse_args(argv: list[str]) -> tuple[str, list[str] | None, bool]:
    if not argv or argv[0] not in MODES:
        raise SystemExit(f"Aufruf: checks <{'|'.join(MODES)}> [--only a,b] [--list]")
    mode, only, list_only = argv[0], None, False
    rest = argv[1:]
    while rest:
        arg = rest.pop(0)
        if arg == "--only" and rest:
            only = [x.strip() for x in rest.pop(0).split(",") if x.strip()]
        elif arg.startswith("--only="):
            only = [x.strip() for x in arg.split("=", 1)[1].split(",") if x.strip()]
        elif arg == "--list":
            list_only = True
        else:
            raise SystemExit(f"Unbekanntes Argument: {arg}")
    return mode, only, list_only


def main(argv: list[str] | None = None) -> int:
    mode, only, list_only = _parse_args(list(sys.argv[1:] if argv is None else argv))
    func_name = MODES[mode]
    names = discover()
    if only:
        unknown = sorted(set(only) - set(names))
        if unknown:
            print(f"Unbekannte Check-Module: {', '.join(unknown)} (vorhanden: {', '.join(names)})")
            return 2
        names = [n for n in names if n in only]

    if list_only:
        for n in names:
            print(n)
        return 0

    ctx = Ctx(mode)
    print(f"== E2E-Checks, Modus {mode} ({func_name}), Module: {', '.join(names) or '-'}", flush=True)
    t0 = time.time()
    try:
        ctx.wait_healthy()
        _load_state(ctx)
        if mode == "fresh":
            _bootstrap_fresh(ctx)
        else:
            _bootstrap_upgrade(ctx)
    except Exception as exc:  # noqa: BLE001
        print(f"BOOTSTRAP FEHLGESCHLAGEN: {exc}", flush=True)
        traceback.print_exc()
        return 2

    results = []
    for name in names:
        ctx.current_check = name
        started = time.time()
        status, detail = "PASS", ""
        try:
            mod = importlib.import_module(f"{__name__}.{name}")
            fn = getattr(mod, func_name, None)
            if fn is None:
                status, detail = "SKIP", f"keine Funktion {func_name}"
            else:
                print(f"-- {name}.{func_name}", flush=True)
                fn(ctx)
        except CheckSkipped as exc:
            status, detail = "SKIP", str(exc)
        except CheckFailed as exc:
            status, detail = "FAIL", str(exc)
        except Exception as exc:  # noqa: BLE001 - jeder Absturz ist ein Fehler des Checks
            status, detail = "ERROR", f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        problems = ctx._run_cleanups()
        if problems:
            detail = (detail + "; " if detail else "") + "Aufraeumen: " + " | ".join(problems)
        duration = time.time() - started
        results.append({"module": name, "status": status, "detail": detail, "seconds": round(duration, 1)})
        print(f"   {status:5} {name} ({duration:.1f} s){' – ' + detail if detail else ''}", flush=True)
    ctx.current_check = None

    failed = [r for r in results if r["status"] in ("FAIL", "ERROR")]
    print(f"== Ergebnis {mode}: {len(results) - len(failed)} ok/skip, {len(failed)} fehlgeschlagen "
          f"({time.time() - t0:.1f} s)", flush=True)
    for r in results:
        print(f"   {r['status']:5} {r['module']}{' – ' + r['detail'] if r['detail'] else ''}")
    _save(ctx, {"mode": mode, "results": results, "failed": len(failed)})
    return 1 if failed else 0
