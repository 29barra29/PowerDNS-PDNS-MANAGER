"""Route-Policy WS-F4-A: neue DNSSEC-Endpunkte (F4 3.2, 3.8, 3.9, 3.12; Anhang A3 "zone").

Alle Routen sind zonenbezogen: ``get_current_user`` (Sessions und Panel-Tokens) plus ``assert_zone_access`` als
erste Zeile – Lese-Endpunkte mit Leserecht, schreibende mit ``write=True`` (Token-Scope ``manage``). Keine
Session-Pflicht (keine Credential-Verwaltung). Die Bestandsrouten (keys, keys/{id}, enable, disable,
activate/deactivate, DELETE, ds) stehen weiter in ``base.py``.
"""

A = "/api/v1"
ZP = "{server_name}/{zone_id:path}"

ROUTE_POLICY = {
    ("GET", f"{A}/dnssec/{ZP}/status"): "zone",
    ("POST", f"{A}/dnssec/{ZP}/keys"): "zone",
    ("PUT", f"{A}/dnssec/{ZP}/keys/{{key_id}}"): "zone",
    ("PUT", f"{A}/dnssec/{ZP}/nsec3"): "zone",
}
