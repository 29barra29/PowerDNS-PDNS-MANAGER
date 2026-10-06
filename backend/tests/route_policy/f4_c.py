"""Route-Policy WS-F4-C: DNSSEC Teil B (F4 3.13; Anhang A3 "zone").

Beide Routen sind zonenbezogene Lesepfade: ``get_current_user`` (Sessions und Panel-Tokens) plus
``assert_zone_access`` (Leserecht) als erste Zeile. DNS-Abfragen nur nach Admin-Opt-in (Propagations-Einstellungen),
Rate-Limit gemeinsam mit dem Propagations-Check.
"""

A = "/api/v1"
ZP = "{server_name}/{zone_id:path}"

ROUTE_POLICY = {
    ("GET", f"{A}/dnssec/{ZP}/parent-ds"): "zone",
    ("GET", f"{A}/dnssec/{ZP}/dnskey-check"): "zone",
}
