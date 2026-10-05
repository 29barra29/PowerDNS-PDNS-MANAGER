"""Route-Policy WS-F7-BE: Zonenverlauf/Rollback und Audit-Log-Erweiterungen (F7 3.2–3.10, Anhang A3).

- ``zone``: Historie, Einzeleintrag, Rollback-Vorschau und Rollback (``get_current_user`` + ``assert_zone_access``;
  Panel-Tokens mit passendem Scope erlaubt, Rollback nur mit ``manage``).
- ``admin``: Einzeleintrag des Audit-Logs (Token nur mit ``allow_admin``).
- ``session``: Aufbewahrungs-Einstellung (nur Admin per Browser-Session, nie per Token).
"""

A = "/api/v1"
ZP = "{server_name}/{zone_id:path}"

ROUTE_POLICY = {
    ("GET", f"{A}/zones/{ZP}/history"): "zone",
    ("GET", f"{A}/zones/{ZP}/history/{{audit_id:int}}/rollback-preview"): "zone",
    ("POST", f"{A}/zones/{ZP}/history/{{audit_id:int}}/rollback"): "zone",
    ("GET", f"{A}/zones/{ZP}/history/{{audit_id:int}}"): "zone",
    ("GET", f"{A}/audit-log/settings"): "session",
    ("PUT", f"{A}/audit-log/settings"): "session",
    ("GET", f"{A}/audit-log/{{entry_id:int}}"): "admin",
}
