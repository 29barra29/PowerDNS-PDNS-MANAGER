"""Maskierung von Geheimnissen in Settings-APIs (F8 §5.3) und Schutz gegen Secret-Retargeting [S3].

Eine Quelle fuer alle Backend-Router (SMTP, Captcha, OIDC/LDAP, Metrik-Token):

- ``SECRET_MASK``: Platzhalter, den GET-Antworten statt eines gesetzten Geheimnisses liefern und den
  das Frontend unveraendert zuruecksendet, wenn der Nutzer das Feld nicht anfasst.
- ``resolve_secret_update(new, old)``: None/Maske -> behalten, "" -> loeschen, sonst neuer Wert.
- ``guard_secret_retarget(...)``: Wer ein Ziel (Host, Port, Benutzer, URL, Issuer, ...) aendert, muss das
  Geheimnis neu eingeben. Sonst koennte ein Admin-Konto (oder eine gestohlene Session) das gespeicherte
  Passwort an einen eigenen Server schicken lassen. Pflicht fuer PUT **und** Test-Endpunkte (ein Test
  benutzt das gespeicherte Geheimnis nur, wenn alle Zielfelder unveraendert sind).
"""
from __future__ import annotations

from typing import Any, Iterable, Literal, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit

from app.core.secrets import is_unreadable

SECRET_MASK = "••••••••"

# Zielfelder je Geheimnis (Bauplan B.2). Aufrufer koennen auch Settings-Keys mit Praefix verwenden
# (z. B. "smtp_host", "ldap_server_urls"); die Normalisierung erkennt Host-/URL-Felder am Namensende.
SMTP_TARGET_FIELDS: tuple[str, ...] = ("host", "port", "username", "encryption", "use_tls", "use_ssl")
LDAP_TARGET_FIELDS: tuple[str, ...] = ("server_urls", "bind_dn", "security", "ca_cert")
OIDC_TARGET_FIELDS: tuple[str, ...] = ("issuer", "client_id", "token_auth_method")

SecretInputAction = Literal["keep", "clear", "set"]


def secret_input_action(new: Optional[str]) -> SecretInputAction:
    """Bedeutung einer Eingabe: None/Maske -> "keep", leer (nach strip) -> "clear", sonst "set"."""
    if new is None or new.strip() == SECRET_MASK:
        return "keep"
    if new.strip() == "":
        return "clear"
    return "set"


def resolve_secret_update(new: Optional[str], old: Optional[str]) -> str:
    """None oder Maske -> alten Wert behalten; "" (nach strip) -> loeschen; sonst neuer Wert (nicht gestrippt).

    Ist der alte Wert nicht entschluesselbar (``UNREADABLE``), wird beim Behalten der Platzhalter selbst
    zurueckgegeben (``== ""``). Aufrufer pruefen ``is_unreadable(ergebnis)`` und schreiben dann nichts –
    ``set_setting`` wuerde den Platzhalter ohnehin mit ``SecretsUnavailableError`` ablehnen, statt den
    Chiffretext still mit "" zu ueberschreiben.
    """
    action = secret_input_action(new)
    if action == "keep":
        if is_unreadable(old):
            return old  # type: ignore[return-value]
        return old or ""
    if action == "clear":
        return ""
    return new  # type: ignore[return-value]


class SecretReentryRequired(ValueError):
    """Ein Zielfeld wurde geaendert, das gespeicherte Geheimnis soll aber weiterverwendet werden.

    Router bilden das auf HTTP 400 mit ``str(exc)`` ab (der globale ValueError-Handler liefert ebenfalls 400).
    ``changed_fields`` nennt nur Feldnamen, nie Werte (fuer Audit/Frontend-Hinweis).
    """

    DEFAULT_MESSAGE = "Zieladresse geändert – bitte das Passwort/Secret erneut eingeben."

    def __init__(self, changed_fields: Iterable[str] = (), message: Optional[str] = None) -> None:
        self.changed_fields: tuple[str, ...] = tuple(changed_fields)
        super().__init__(message or self.DEFAULT_MESSAGE)


def _is_host_field(name: str) -> bool:
    n = name.lower()
    return n.endswith("host") or n.endswith("hostname") or n in ("server", "smtp_server")


def _is_url_field(name: str) -> bool:
    n = name.lower()
    return n.endswith("url") or n.endswith("urls") or n.endswith("issuer")


def _norm_url(value: str) -> str:
    """Schema und Host (inkl. Port/Userinfo) klein, Pfad/Query exakt."""
    try:
        parts = urlsplit(value)
    except ValueError:
        return value
    if not parts.scheme and not parts.netloc:
        return value.lower()  # kein URL-Format (z. B. nur Hostname) -> wie Host behandeln
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, parts.fragment))


def _norm_scalar(name: str, value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    s = str(value).strip()
    if s.lower() in ("true", "false"):
        return s.lower()
    if _is_host_field(name):
        return s.lower().rstrip(".")
    if _is_url_field(name):
        return _norm_url(s)
    return s


def _norm(name: str, value: Any) -> Any:
    if isinstance(value, (list, tuple, set, frozenset)):
        # Reihenfolge egal (z. B. LDAP-Failover-Liste): gleiche Menge = gleiches Ziel.
        items = {_norm_scalar(name, v) for v in value}
        items.discard("")
        return tuple(sorted(items))
    return _norm_scalar(name, value)


def changed_target_fields(targets_before: Mapping[str, Any], targets_after: Mapping[str, Any]) -> list[str]:
    """Namen der Zielfelder, die sich (nach Normalisierung) geaendert haben.

    Fehlt ein Feld in ``targets_after``, gilt es als unveraendert (Teil-Update).
    """
    changed: list[str] = []
    for name in sorted(targets_after):
        if _norm(name, targets_before.get(name)) != _norm(name, targets_after.get(name)):
            changed.append(name)
    return changed


def guard_secret_retarget(
    *,
    targets_before: Mapping[str, Any],
    targets_after: Mapping[str, Any],
    secret_in: Optional[str],
    secret_stored: bool = True,
) -> None:
    """Wirft ``SecretReentryRequired``, wenn sich ein Zielfeld aendert und das gespeicherte Geheimnis
    weiterverwendet werden soll (``secret_in`` ist None oder die Maske).

    - Ziel unveraendert -> ok (auch mit Maske).
    - Ziel geaendert + neues Geheimnis -> ok; Ziel geaendert + "" (Geheimnis loeschen) -> ok.
    - ``secret_stored=False`` (kein bzw. kein lesbares Geheimnis gespeichert) -> ok, es gibt nichts zu schuetzen.

    Normalisierung: Strings gestrippt; Host-Felder (Name endet auf "host") klein und ohne Schlusspunkt;
    URL-Felder (Name endet auf "url"/"urls"/"issuer") mit kleinem Schema/Host; Zahlen/Bools als Text
    ("587" == 587, "true" == True); Listen als Menge.
    """
    if not secret_stored:
        return
    if secret_input_action(secret_in) != "keep":
        return
    changed = changed_target_fields(targets_before, targets_after)
    if changed:
        raise SecretReentryRequired(changed)


def pick_targets(values: Mapping[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    """Teilmenge ``fields`` aus ``values`` (fehlende Felder fehlen auch im Ergebnis)."""
    return {f: values[f] for f in fields if f in values}
