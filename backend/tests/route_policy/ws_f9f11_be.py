"""Route-Policy WS-F9F11-BE (Anhang A3): DynDNS (eigene Token-Pruefung, Token-Verwaltung per Session) und PTR.

- ``own_auth``: Update-Endpunkte und ``whoami`` pruefen den DynDNS-Token selbst (keine Cookies, keine Panel-Tokens);
  ``/nic/update`` ist eine Root-Route (nur GET).
- ``session``: Token-Verwaltung, Zonenauswahl und die Admin-Einstellungen (``get_session_user`` bzw.
  ``get_admin_session_user`` – nie per API-Token).
- ``user``: ``/dyndns/info`` und die PTR-Hilfen (``get_current_user``; Zonenrechte prueft der Service per
  ``has_zone_access``, die Reverse-Zone steht nicht im Pfad).
"""

A = "/api/v1"

ROUTE_POLICY = {
    # Update-Endpunkte mit eigener Token-Pruefung
    ("GET", "/nic/update"): "own_auth",
    ("GET", f"{A}/dyndns/update"): "own_auth",
    ("POST", f"{A}/dyndns/update"): "own_auth",
    ("GET", f"{A}/dyndns/whoami"): "own_auth",
    # Info fuer die Einstellungs-Karte (auch per Panel-Token lesbar)
    ("GET", f"{A}/dyndns/info"): "user",
    # Token-Verwaltung nur per Browser-Session
    ("GET", f"{A}/dyndns/zones"): "session",
    ("GET", f"{A}/dyndns/tokens"): "session",
    ("POST", f"{A}/dyndns/tokens"): "session",
    ("PUT", f"{A}/dyndns/tokens/{{token_id}}"): "session",
    ("DELETE", f"{A}/dyndns/tokens/{{token_id}}"): "session",
    ("POST", f"{A}/dyndns/tokens/{{token_id}}/rotate"): "session",
    ("GET", f"{A}/dyndns/admin/tokens"): "session",
    # Admin-Einstellungen (settings_dyndns.py)
    ("GET", f"{A}/settings/dyndns"): "session",
    ("PUT", f"{A}/settings/dyndns"): "session",
    ("GET", f"{A}/settings/ptr"): "session",
    ("PUT", f"{A}/settings/ptr"): "session",
    # PTR-Hilfen fuer den Record-Dialog
    ("GET", f"{A}/ptr/config"): "user",
    ("GET", f"{A}/ptr/lookup"): "user",
}
