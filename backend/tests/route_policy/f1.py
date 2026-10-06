"""Route-Policy WS-F1: Bulk-Editor (F1 3.2, Anhang A3).

- ``zone``: Vorschau einer Bulk-Aenderung (``get_current_user`` + ``assert_zone_access(write=True)`` als erste
  Zeile; Panel-Tokens mit passendem Scope und ``manage`` erlaubt). ``POST …/bulk`` steht unveraendert in
  ``base.py``.
"""

A = "/api/v1"
ZP = "{server_name}/{zone_id:path}"

ROUTE_POLICY = {
    ("POST", f"{A}/records/{ZP}/bulk/preview"): "zone",
}
