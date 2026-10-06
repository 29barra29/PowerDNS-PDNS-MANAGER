# Mitwirken / Contributing

Danke für dein Interesse am **PDNS Manager**! Kurz und technisch – passend zu einem Self-Hosting-Projekt, das in
Unternehmen produktiv läuft. Deshalb gibt es ein paar feste Regeln für Rechte, Datenbank und Übersetzungen.

## Issues

* **Bug oder Feature?** Nutze die [Issue-Templates](https://github.com/29barra29/PowerDNS-PDNS-MANAGER/issues/new/choose) (Bug, Feature, Installation).
* **Sicherheit:** Keine öffentlichen Issues – siehe [SECURITY.md](SECURITY.md) (GitHub **Security** → *Report a vulnerability*).

## Pull Requests

* Branch von **`main`** erstellen, Änderungen in **kleinen, nachvollziehbaren** Commits.
* PR-Beschreibung ausfüllen (Template erscheint automatisch) und kurz beschreiben, **was getestet** wurde.
* Bei größeren Änderungen vorher ein Issue aufmachen.

## Entwicklung (Überblick)

* Stack: siehe [README – Stack](README.md#stack); Frontend-Aufbau: [frontend/README.md](frontend/README.md);
  API: [docs/PANEL-API.md](docs/PANEL-API.md).
* Lokales Setup ohne Docker: [README – Lokale Entwicklung](README.md#lokale-entwicklung-ohne-docker).
* `compose.yaml` hat feste Containernamen (`dns-manager-api`, `dns-manager-db`). Auf einem Host, auf dem schon eine
  Installation läuft, kein `docker compose up` aus einem zweiten Checkout starten – für Tests gibt es die isolierten
  E2E-Skripte unter `scripts/e2e/` (eigenes Compose-Projekt, eigene Namen, keine festen Ports).

## Tests und Prüfungen

Die CI (`.github/workflows/test.yml`) führt bei jedem Pull Request aus:

```bash
# Backend (Python 3.12) – mit einer Test-MariaDB, sonst werden die DB-Tests übersprungen
cd backend
pip install -r requirements.lock pytest pytest-asyncio pytest-cov
pytest tests/test_migrations_db.py          # Migration 2.4.1 -> aktuell, braucht eine frische DB (RUN_DB_TESTS=1, DATABASE_URL)
pytest --ignore=tests/test_migrations_db.py -m ""

# Frontend (Node 22)
cd frontend
npm ci
npm run lint
npm run check:locales -- --strict
npm test
npm run build
```

Ohne Datenbank laufen die Backend-Tests mit `pytest -m ""` ebenfalls; die DB-Tests werden dann übersprungen.
Zusätzlich (manuell in der CI über „Run workflow“, lokal mit Docker):

```bash
scripts/e2e/run-e2e.sh                       # Neuinstallation gegen MariaDB, zwei PowerDNS-Server, Webhook-Empfänger
scripts/e2e/upgrade-241-to-30.sh --no-build  # Update einer 2.4.1-Datenbank (--with-downgrade: inkl. Downgrade-Weg)
scripts/e2e/run-e2e.sh --no-build --ui       # zusätzlich UI-Smoke-Tests (Playwright/Chromium im Container)
scripts/e2e/static-checks.sh                 # statische Regeln (Shell-Syntax, Router-Ordnung, Route-Policy, Audit, Rollenprüfung, Sprachdateien …)
```

Details: [scripts/e2e/README.md](scripts/e2e/README.md) und [scripts/e2e/ui/README.md](scripts/e2e/ui/README.md).

## Konventionen

### Backend

* **Neue Endpunkte** kommen als eigenes Modul `backend/app/routers/<name>.py` mit `router` (Präfix ohne `/api/v1`)
  und eindeutigem `ROUTER_ORDER`; Routen ohne `/api/v1` (`root_routers`) nur mit `GET`/`HEAD`. `main.py` bindet die
  Module automatisch ein (Regeln in `backend/app/routers/__init__.py`).
* **Jede neue Route** bekommt eine Zugangsklasse in `backend/tests/route_policy/<name>.py`
  (`public`, `own_auth`, `user`, `session`, `admin`, `zone`); `tests/test_route_policy.py` prüft sie gegen den Code.
* **Rechte:** Zonenbezogene Endpunkte rufen als Erstes `await assert_zone_access(db, user, zone_id, write=…)` auf.
  Admin-Funktionen nutzen `get_admin_user`; Einstellungen, Zugangsdaten und Token-Verwaltung `get_session_user` bzw.
  `get_admin_session_user` (nie per API-Token). Kein `role == "admin"` im Code – stattdessen `is_effective_admin`.
  Zonennamen erscheinen in Fehlertexten nur, wenn der Aufrufer die Zone lesen darf.
* **Schreibende Handler** (`POST`/`PUT`/`PATCH`/`DELETE`) nutzen die Datenbank-Abhängigkeit **`DbWrite`** (Commit vor
  dem Senden der Antwort), lesende `DbRead`.
* **Audit** nur über `write_audit(...)` (`app/services/audit.py`), **Webhooks** nur über
  `await enqueue_event(...)` (`app/services/webhook_outbox.py`) mit einem Ereignis aus `EVENT_CATALOG`
  (`app/services/webhook_events.py`) – nie direkt senden.
* **Geheimnisse** nur über die verschlüsselten Spalten bzw. `app/services/system_settings.py`; Endpunkte, die ein
  gespeichertes Geheimnis gegen ein änderbares Ziel verwenden, verlangen bei Zieländerung die Neueingabe
  (`guard_secret_retarget`). Nie Geheimnisse, Tokens oder Passwörter loggen oder auditieren.
* **Schreibzugriffe auf mehrere PowerDNS-Server** über `app/services/fanout.py` (Primary zuerst, Status-Strings laut
  [PANEL-API](docs/PANEL-API.md#mehrere-powerdns-server-fan-out)).
* **Datenbank:** Neue Tabellen als Modell in `backend/app/models/ext/<name>.py`, zusätzliche Statements und
  Datenmigrationen in `backend/app/core/migrations/<name>.py` (`SCHEMA_STATEMENTS`, `DATA_MIGRATIONS`). Migrationen
  müssen wiederholbar sein und eine 2.4.1-Datenbank ohne Eingriff hochziehen; neue Spalten an Bestandstabellen bitte im
  Issue/PR abstimmen.
* Fehlertexte für Benutzer und Code-Kommentare auf Deutsch.

### Frontend

* Endpunkt-Methoden als Modul `frontend/src/api/<name>.js` (Vertrag: [frontend/src/api/README.md](frontend/src/api/README.md));
  keine direkten `fetch`-Aufrufe in Komponenten.
* Erweiterungen über die **Slots** statt Änderungen an den großen Seiten (Einstellungs-Reiter und -Karten, Tabs,
  Kopf- und Zeilen-Aktionen, Erweiterungen des Record-Dialogs, Banner, Dialoge, Audit-Aktionen) – Übersicht in
  [frontend/README.md](frontend/README.md#slots).
* Texte nur über i18n. Neue Texte als **Locale-Fragment** in allen sechs Sprachen
  (`frontend/src/locales/fragments/<name>.<sprache>.json`, Regeln in der
  [Fragment-README](frontend/src/locales/fragments/README.md)); die Sprachdateien selbst nicht direkt ändern – das
  Zusammenführen (`npm run merge:locales`) passiert vor dem Release in einem eigenen Commit mit dem Betreff
  `locales: …` (`scripts/e2e/static-checks.sh` prüft das).
* Dialoge bekommen `role="dialog"`, Fokusführung über `useDialogFocus` (`frontend/src/lib/useDialogFocus.js`) und
  schließen mit ESC; Strg/Cmd+Enter über die Option `onSubmitShortcut`, keine eigenen globalen `keydown`-Listener.
  Das Overlay steht in `<ModalPortal>` (`frontend/src/components/common/ModalPortal.jsx`), siehe
  [Frontend-README](frontend/README.md).

### Dokumentation

* Nutzersichtbare Änderungen bekommen ein **Release-Notes-Fragment** `docs/release-notes/<nächste Version>/<name>.md`
  mit den Abschnitten *Neu*, *Geändert / Breaking*, *API*, *Nach dem Update prüfen* und *Website-Seiten (DE/EN)*.
  Daraus entstehen Changelog, PANEL-API und Website.
* Neue Umgebungsvariablen: `.env.example`, `compose.yaml` (Durchreichen) und [INSTALL.md](INSTALL.md#umgebungsvariablen).

## PR-Checkliste

- [ ] Tests geschrieben bzw. angepasst; Backend-Tests, `npm test`, `npm run lint` und `npm run check:locales -- --strict` grün.
- [ ] Neue Routen in `backend/tests/route_policy/<name>.py` klassifiziert; Rechteprüfung (`assert_zone_access`,
      `get_admin_user`, `get_session_user`) als erste Zeile.
- [ ] Schreibende Handler nutzen `DbWrite`; Audit über `write_audit`, Webhooks über `enqueue_event`.
- [ ] Schema-Änderungen über `models/ext/` bzw. `core/migrations/`, wiederholbar, mit Test.
- [ ] Frontend-Erweiterungen über Slots und `src/api/<name>.js`; Texte als Locale-Fragmente in allen sechs Sprachen.
- [ ] Neue Dialoge oder Abläufe: Spec unter `scripts/e2e/ui/tests/`, lokal `scripts/e2e/run-e2e.sh --ui`.
      Bekannte, gemeldete Fehler mit `knownBug('ID', fn)` markieren – der Test schlägt fehl, sobald der Fehler behoben ist.
- [ ] Neue API: Check unter `scripts/e2e/checks/` (siehe [checks/README.md](scripts/e2e/checks/README.md)).
- [ ] Release-Notes-Fragment angelegt; neue Umgebungsvariablen in `.env.example`, `compose.yaml` und `INSTALL.md`.
- [ ] Keine Geheimnisse, internen Hostnamen oder lokalen Pfade im Code, in Tests oder in der Doku.

Fragen? Erst ein Issue – dann können andere mitlesen.
