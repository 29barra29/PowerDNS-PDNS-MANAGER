"""Maskiert Geheimnisse in Query-Strings des uvicorn-Access-Logs.

DynDNS-Clients (und falsch konfigurierte Skripte) schicken Passwoerter/Token gelegentlich
im Query-String. Das Access-Log bleibt an (Betreiber nutzen es); dieser Filter ersetzt
die Werte bekannter Geheimnis-Parameter durch ``***``. Header (Authorization) loggt
uvicorn ohnehin nicht.

Seit WS-W3-NACHARBEIT (Antrag A3 aus WS-F9F11-BE-fix2):
- Parameternamen werden wie beim Server URL-dekodiert verglichen (``%70assword=`` ist ``password=``), auch
  mehrfach kodiert und mit ``+``/Leerraum; ausserdem gelten Namen mit typischer Endung als geheim
  (``new_password``, ``access_token``, ``client_secret``, ``x-api-key`` ...).
- Werte, die (dekodiert, Gross-/Kleinschreibung egal) ein Panel-Token-Praefix ``dnsmgr_`` enthalten, werden
  unabhaengig vom Parameternamen maskiert; ``dnsmgr_``-Tokens im Pfad ebenfalls.
Das Ergebnis behaelt die Originalschreibweise aller nicht maskierten Teile.
"""
import logging
import re
from urllib.parse import unquote_plus

SENSITIVE_PARAMS = ("password", "pass", "pwd", "pw", "token", "key", "secret", "apikey", "api_key", "auth")
# Namen mit dieser Endung (nach dem Normalisieren: klein, "-" und "." zu "_") gelten ebenfalls als geheim
_SENSITIVE_SUFFIX = re.compile(r"(?:password|passwd|_pass|_pwd|secret|token|apikey|api_key|_key|_auth)$")
TOKEN_VALUE_MARKER = "dnsmgr_"
MASK = "***"
# dnsmgr_-Token im Pfad (z. B. versehentlich als Pfadsegment) – bis zum naechsten Trenner
_PATH_TOKEN_RE = re.compile(r"(?i)(dnsmgr_)[^/?&#\s]+")
_MAX_DECODE_ROUNDS = 3


def _decode(text: str) -> str:
    """URL-dekodiert (``+`` als Leerzeichen), mehrfach bis stabil – mehrfach kodierte Namen/Werte erkennen."""
    out = text
    for _ in range(_MAX_DECODE_ROUNDS):
        try:
            nxt = unquote_plus(out, errors="replace")
        except Exception:  # noqa: BLE001 - nie werfen
            return out
        if nxt == out:
            break
        out = nxt
    return out


def _normalize_name(raw: str) -> str:
    return re.sub(r"[-.\s]+", "_", _decode(raw).strip().lower())


def is_sensitive_name(raw: str) -> bool:
    """Ist der (rohe, ggf. URL-kodierte) Parametername ein Geheimnis-Parameter?"""
    name = _normalize_name(raw)
    if not name:
        return False
    return name in SENSITIVE_PARAMS or name.replace("_", "") in SENSITIVE_PARAMS \
        or bool(_SENSITIVE_SUFFIX.search(name))


def has_token_value(raw: str) -> bool:
    """Enthaelt der (rohe, ggf. URL-kodierte) Wert ein Panel-/DynDNS-Token (``dnsmgr_``)?"""
    return TOKEN_VALUE_MARKER in raw.lower() or TOKEN_VALUE_MARKER in _decode(raw).lower()


def _redact_pair(pair: str) -> str:
    name, sep, value = pair.partition("=")
    if not sep:
        # Parameter ohne "=": nur ein Token als Name waere ein Geheimnis
        return MASK if has_token_value(name) else pair
    if is_sensitive_name(name) or has_token_value(value):
        return f"{name}={MASK}"
    if has_token_value(name):
        return f"{MASK}={value}"
    return pair


def redact_query(path: str) -> str:
    """Ersetzt die Werte sensibler Query-Parameter (und ``dnsmgr_``-Tokens im Pfad) durch ``***``."""
    if not isinstance(path, str):
        return path
    base, qmark, rest = path.partition("?")
    if TOKEN_VALUE_MARKER in base.lower():
        base = _PATH_TOKEN_RE.sub(r"\1" + MASK, base)
    if not qmark:
        return base
    query, hashmark, fragment = rest.partition("#")
    query = "&".join(_redact_pair(p) for p in query.split("&"))
    return f"{base}?{query}{hashmark}{fragment}"


class RedactQueryFilter(logging.Filter):
    """Filter fuer ``uvicorn.access``: args = (client_addr, method, path_with_query, http_version, status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            a = record.args
            if isinstance(a, tuple) and len(a) >= 3 and isinstance(a[2], str) \
                    and ("?" in a[2] or TOKEN_VALUE_MARKER in a[2].lower()):
                record.args = a[:2] + (redact_query(a[2]),) + a[3:]
        except Exception:  # noqa: BLE001 - Logging darf nie brechen
            pass
        return True


def install_access_log_redaction() -> None:
    """Haengt den Filter an ``uvicorn.access`` (idempotent)."""
    lg = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactQueryFilter) for f in lg.filters):
        lg.addFilter(RedactQueryFilter())
