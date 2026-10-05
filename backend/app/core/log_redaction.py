"""Maskiert Geheimnisse in Query-Strings des uvicorn-Access-Logs.

DynDNS-Clients (und falsch konfigurierte Skripte) schicken Passwoerter/Token gelegentlich
im Query-String. Das Access-Log bleibt an (Betreiber nutzen es); dieser Filter ersetzt
die Werte bekannter Geheimnis-Parameter durch ``***``. Header (Authorization) loggt
uvicorn ohnehin nicht.
"""
import logging
import re

SENSITIVE_PARAMS = ("password", "pass", "pwd", "pw", "token", "key", "secret", "apikey", "api_key", "auth")
_RE = re.compile(r"(?i)([?&](?:%s)=)[^&#\s]*" % "|".join(SENSITIVE_PARAMS))


def redact_query(path: str) -> str:
    """Ersetzt die Werte sensibler Query-Parameter durch ``***``."""
    if not isinstance(path, str) or "?" not in path:
        return path
    return _RE.sub(r"\1***", path)


class RedactQueryFilter(logging.Filter):
    """Filter fuer ``uvicorn.access``: args = (client_addr, method, path_with_query, http_version, status)."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            a = record.args
            if isinstance(a, tuple) and len(a) >= 3 and isinstance(a[2], str) and "?" in a[2]:
                record.args = a[:2] + (redact_query(a[2]),) + a[3:]
        except Exception:  # noqa: BLE001 - Logging darf nie brechen
            pass
        return True


def install_access_log_redaction() -> None:
    """Haengt den Filter an ``uvicorn.access`` (idempotent)."""
    lg = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactQueryFilter) for f in lg.filters):
        lg.addFilter(RedactQueryFilter())
