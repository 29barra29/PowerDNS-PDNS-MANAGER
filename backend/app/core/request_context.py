"""Request-gebundener Auth-Kontext (ContextVars).

Bewusst ohne App-Imports, damit Models, Services und der Audit-Helfer ihn ohne
Zirkularimport nutzen koennen. Gesetzt wird der Kontext von ``core.auth``
(``set_auth_context``) zu Beginn jeder authentifizierten Anfrage; ausserhalb von
Requests (Hintergrund-Worker, Startup, Tests) sind alle Werte ``None`` – das
Verhalten entspricht dann einer Browser-Session.

ContextVar-Isolation: jeder Request laeuft in einer eigenen Task (uvicorn und
Starlette kopieren den Kontext); FastAPI loest async-Dependencies in derselben Task
wie den Endpunkt auf.
"""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import FrozenSet, Optional

AUTH_VIA_VALUES = ("session", "panel_token", "dyndns_token", "acme_token")


@dataclass(frozen=True)
class TokenScope:
    """Einschraenkungen eines Panel-API-Tokens (F14)."""

    token_id: int
    name: str
    token_prefix: str
    zones: Optional[FrozenSet[str]]  # None = alle Zonen des Besitzers; normalisiert (lower + Trailing-Dot)
    permission: str  # "manage" | "read"
    allow_admin: bool

    @property
    def read_only(self) -> bool:
        """Fail-safe: alles ausser "manage" gilt als Lesezugriff."""
        return self.permission != "manage"

    @property
    def zone_limited(self) -> bool:
        return self.zones is not None

    def covers_zone(self, zone_normalized: str) -> bool:
        return self.zones is None or zone_normalized in self.zones


# "session" | "panel_token" | "dyndns_token" | "acme_token" | None
auth_via_ctx: ContextVar[Optional[str]] = ContextVar("auth_via", default=None)
current_token_scope: ContextVar[Optional[TokenScope]] = ContextVar("current_token_scope", default=None)
# Fuer Audit-Spalten actor_username/client_ip (E-F7-1)
actor_username_ctx: ContextVar[Optional[str]] = ContextVar("actor_username", default=None)
client_ip_ctx: ContextVar[Optional[str]] = ContextVar("client_ip", default=None)


def get_auth_via() -> Optional[str]:
    return auth_via_ctx.get()


def get_token_scope() -> Optional[TokenScope]:
    return current_token_scope.get()


def get_actor_username() -> Optional[str]:
    return actor_username_ctx.get()


def get_client_ip_ctx() -> Optional[str]:
    return client_ip_ctx.get()


def audit_auth_context() -> Optional[dict]:
    """Zusatz fuer ``AuditLog.details["auth"]``.

    ``None`` bei Browser-Session oder ohne Request-Kontext; sonst
    ``{"via", "token_id", "token_name", "token_prefix"}`` (Token-Felder nur, wenn ein
    Panel-Token-Scope gesetzt ist).
    """
    via = auth_via_ctx.get()
    if via in (None, "session"):
        return None
    out: dict = {"via": via}
    scope = current_token_scope.get()
    if scope is not None:
        out.update(token_id=scope.token_id, token_name=scope.name, token_prefix=scope.token_prefix)
    return out


def reset_request_context() -> None:
    """Setzt alle Kontextwerte der aktuellen Task auf ``None`` (Beginn einer Anfrage, Tests)."""
    auth_via_ctx.set(None)
    current_token_scope.set(None)
    actor_username_ctx.set(None)
    client_ip_ctx.set(None)
