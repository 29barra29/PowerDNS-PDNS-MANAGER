"""Route-Policy WS-F12F13-BE (Anhang A3): Propagations-Check (zone) und Monitoring-Einstellungen (session).

``/metrics`` (root, own_auth) und ``/api/v1/metrics`` (admin) stehen bereits in ``base.py``.
"""

A = "/api/v1"
ZP = "{server_name}/{zone_id:path}"

ROUTE_POLICY = {
    # F12: Lesezugriff auf die Zone (assert_zone_access als erste Zeile, Panel-Tokens mit Scope erlaubt)
    ("GET", f"{A}/zones/{ZP}/propagation"): "zone",
    # F12/F13: Admin-Browser-Session (get_admin_session_user), nie per Token
    ("GET", f"{A}/settings/propagation"): "session",
    ("PUT", f"{A}/settings/propagation"): "session",
    ("GET", f"{A}/settings/metrics"): "session",
    ("PUT", f"{A}/settings/metrics"): "session",
    ("POST", f"{A}/settings/metrics/token"): "session",
    ("DELETE", f"{A}/settings/metrics/token"): "session",
    ("GET", f"{A}/settings/monitoring/status"): "session",
}
