"""API-Router des Backends – Einbindung per Discovery (Bauplan B.13).

``app.main`` importiert jedes Modul dieses Pakets (ohne ``_``-Praefix), sortiert sie nach ihrer Ordnung und
bindet ``mod.router`` unter ``/api/v1`` ein – vor den Root-Routen (``/metrics``, ``/api``, ``/health``) und
vor dem SPA-Catch-all. Es gibt keine Router-Liste in ``main.py`` mehr.

Konvention fuer NEUE Module (``app/routers/<name>.py``):

- ``router: APIRouter`` – Prefix ohne ``/api/v1`` (z. B. ``APIRouter(prefix="/history")``).
- ``ROUTER_ORDER: int`` – Position in der Einbinde-Reihenfolge, eindeutig im Paket (Werte laut Bauplan B.13:
  sso 25, panel_tokens 27, webhooks 30, history 50, propagation 55, lua 75, settings_secrets 101,
  settings_sso 102, settings_dyndns 103, settings_monitoring 104, settings_lua 105, dyndns 130, ptr 135).
  Wichtig, wenn sich Pfade ueberlappen (z. B. ``{zone_id:path}``-Routen): der zuerst eingebundene Router gewinnt.
- optional ``root_routers: list[APIRouter]`` – Router OHNE ``/api/v1``-Prefix (z. B. ``/nic/update``); dort sind
  nur GET/HEAD erlaubt (der CSRF-Schutz gilt nur unter ``/api/``).

Die Bestandsmodule aus 2.4.1 (setup, auth, servers, zones, records, dnssec, search, settings, templates,
acme) haben kein ``ROUTER_ORDER``-Attribut; ihre Ordnung steht in ``main.LEGACY_ROUTER_ORDER``. Ein Modul
mit Attribut UND LEGACY-Eintrag oder ganz ohne Ordnung ist ein Fehler (``tests/test_router_discovery.py``,
``scripts/e2e/static-checks.sh`` Regel router-order). Jede neue Route braucht ausserdem einen Eintrag in
``backend/tests/route_policy/<ws>.py``.
"""
