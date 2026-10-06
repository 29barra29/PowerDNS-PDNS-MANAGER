# UI-Smoke-Tests (`scripts/e2e/ui/`)

Automatisierte Browser-Tests von PDNS Manager: Playwright mit Chromium (headless) in einem Docker-Container, der im
E2E-Netz `pdnsmgr-e2e-net` haengt und das Panel unter `http://backend:8000` aufruft (das Backend liefert das gebaute
Frontend aus). Sie ersetzen die manuellen Browser-Checklisten der Integrationsnotizen. Auf dem Host ist weder ein
Browser noch Node.js noetig – nur Docker.

| Datei | Zweck |
|---|---|
| `run.sh` | Tests gegen einen **laufenden** E2E-Stack (Playwright-Container, Einrichtungs-Backend, Ergebnisse) |
| `compose.ui.yaml` | Ergaenzung zu `../compose.e2e.yaml` (Profil `ui`): Dienste `ui` (Playwright) und `setup-backend` |
| `container-run.sh` | laeuft im Container: Quellen kopieren, `npm ci` (nur bei geaendertem Lock), `playwright test` |
| `playwright.config.js` | ein Worker, Retries 1, Traces/Screenshots nur bei Fehlern, Sprache `de-DE` |
| `package.json`, `package-lock.json` | `@playwright/test` (gleiche Version wie das Image) und `mysql2`; **unabhaengig** von `frontend/package.json` |
| `fixtures/` | Test-Basis (Konsolen-Waechter, Admin-API, weitere Sitzungen), API-/DB-/TOTP-Helfer, i18n, UI-Helfer |
| `tests/*.spec.js` | Specs (siehe unten) |

## Benutzung

Aus der Wurzel des Checkouts (Worktree):

```bash
scripts/e2e/run-e2e.sh --ui          # Image bauen, Stack starten, API-Checks, dann UI-Smoke, Abbau
scripts/e2e/run-e2e.sh --ui-only     # wie oben, aber ohne API-Checks

# Entwicklung: Stack stehen lassen und die UI-Tests beliebig oft gegen ihn laufen lassen
scripts/e2e/run-e2e.sh --no-build --keep --ui-only
scripts/e2e/ui/run.sh                                  # alle Specs
scripts/e2e/ui/run.sh -- tests/04-bulk-editor.spec.js  # eine Spec (Argumente nach -- gehen an "playwright test")
scripts/e2e/ui/run.sh -- --grep "Rollback"             # nach Testnamen filtern
scripts/e2e/run-e2e.sh --down                          # Abbau
```

Optionen von `run.sh`: `--workdir DIR` (Ergebnisse unter `DIR/ui`), `--no-setup` (ohne Einrichtungs-Backend; die
Spec „Einrichtungsassistent“ wird uebersprungen), `--pending` (siehe unten). Umgebung: `E2E_ADMIN_PASSWORD` (sonst
aus `DIR/admin_password` bzw. dem laufenden Backend-Container), `E2E_UI_IMAGE` (Default
`mcr.microsoft.com/playwright:v<Version aus package.json>-noble`), `E2E_UI_RETRIES` (Default 1), `E2E_UI_NPM_CACHE`
(npm-Cache, Default `~/.cache/pdnsmgr-e2e-ui-npm`), `E2E_UI_LOCALES_DIR` (Sprachdateien fuer die Selektoren, Default
`frontend/src/locales`). Bei `run-e2e.sh --ui-only` gehen Playwright-Argumente ueber `E2E_UI_ARGS`.

Ergebnisse (bei Fehlern bleibt der Arbeitsordner erhalten, Pfad steht am Ende der Ausgabe):
`ui/report/index.html` (HTML-Bericht), `ui/test-results/` (Trace, Screenshot, `error-context.md` je fehlgeschlagenem
Test; Trace ansehen mit `npx playwright show-trace <trace.zip>` auf einem Rechner mit Browser), `ui/results.json`,
`ui/setup-backend.log`.

Typische Laufzeit auf dem Entwicklungs-Host (2 CPUs, warmer Build-Cache): UI-Smoke allein ca. 3 Minuten
(54 Tests, davon 5 nur mit `--pending`), `run-e2e.sh --ui` gesamt ca. 4 Minuten (Build, Start, 17 API-Check-Module,
UI-Smoke, Abbau). Das Playwright-Image (ca. 3,5 GB entpackt) wird einmal gezogen.

## Sicherheit und Isolation

- Gleiche Regeln wie `scripts/e2e`: nur Projekt `pdnsmgr-e2e`, Container `pdnsmgr-e2e-*`, Netz `pdnsmgr-e2e-net`, keine
  Host-Ports; `run.sh` prueft die zusammengefuehrte Compose-Konfiguration (`e2e_verify_config`), haelt den E2E-Lock und
  laeuft in einem Kapazitaets-Slot (`scripts/dev/with-slot.sh`).
- `setup-backend` ist eine zweite Backend-Instanz mit **leerer** Datenbank `dns_manager_setup` (je Lauf neu angelegt,
  nach dem Lauf wieder entfernt) und `ENABLE_REGISTRATION=true` – nur fuer den Einrichtungsassistenten.
- Der Browser darf nur das E2E-Netz erreichen: Anfragen an `api.github.com` (Versionspruefung, Commits) werden mit
  Testdaten beantwortet, jede andere externe Anfrage (z. B. Google Fonts) wird blockiert und laesst den Test
  scheitern (`fixtures/external.js`).
- `http://backend:8000` gilt per Chromium-Schalter als sicherer Kontext (sonst fehlen Zwischenablage und WebCrypto);
  dafuer laeuft das volle Chromium im neuen Headless-Modus (`channel: 'chromium'`).

## Regeln fuer Specs

- Texte nie abschreiben: `t('key')` aus `fixtures/i18n.js` liest die Sprachdateien des Frontends. Selektoren ueber
  Rollen, Beschriftungen und diese Texte; das Frontend hat keine `data-testid`.
- Keine festen Wartezeiten: `expect(...)`-Wiederholungen, `expect.poll`, `waitForEvent`.
- Eigene Daten: eindeutige Namen (`uniqueZone`, `unique`), Aufraeumen im `afterAll`/`finally`. Tests, die
  Benutzer-Einstellungen aendern (Sprache, 2FA), nutzen einen eigenen Testbenutzer – nie den Admin.
- Konsole: Jede Seite wird ueberwacht. `console.error`, ungefangene Fehler und HTTP-5xx-Meldungen lassen den Test
  scheitern; 4xx-Netzwerkmeldungen (erwartete 401/403/409) nicht. Erwartete Meldungen: `guard.allow(/…/)`.
- Zustaende ohne API (externes SSO-Konto, unlesbares Geheimnis, abgelaufener Token) per `fixtures/db.js`.
- Noch nicht integrierte Welle-3-UI: `skipUnlessPending('WS-…')` bzw. `pendingCheck('WS-…', fn)` – laeuft nur mit
  `run.sh --pending` (`E2E_UI_PENDING=1`). Nach dem Merge des Workstreams einschalten und, wenn gruen, die Marker entfernen.
- Bekannte, gemeldete Fehler: `knownBug('ID', fn)` – erwartet das Scheitern; ist der Fehler behoben, schlaegt der Test
  fehl, damit der Marker verschwindet.

## Specs

| Spec | Inhalt (Quelle der Checkliste) |
|---|---|
| `01-auth` | Login/Logout, falsches Passwort, Sprachwechsel Login-Seite (Sprachdatei erst bei Auswahl) und Profil, 401 -> Neuladen, Rollen-Gate (FE1a, F8b) |
| `02-setup-wizard` | Einrichtungsassistent auf leerer DB inkl. SMTP und Passwort-Maske (F8b J01) |
| `03-zones-records` | Zone anlegen (Fan-out ns1+ns2), Records anlegen/bearbeiten/klonen/loeschen, Fan-out-Hinweis, roter Peer-Fehler-Banner, Fehler im Dialog, Nur-Lese-Nutzer (FE2, F8b) |
| `04-bulk-editor` | Auswahl/Indeterminate, TTL -> Vorschau -> Anwenden, Text-Editor mit Zeilenfehler, PTR-Pflege mit Erfolgszeile und gelbem Hinweis (F1 9.5, F1-fix2) |
| `05-history-rollback` | Verlauf per Deep-Link und Zeilen-Aktion, Rollback-Dialog, Rollback ohne/mit Konflikt (force), Lese-Nutzer (F7) |
| `06-dnssec` | Aktivieren mit Optionen, Peer-Warnung, DS-Assistent (Kopieren), Schluessel, Rollover Schritt 1, Zone mit DNSSEC anlegen (F4-B) |
| `07-export-notify` | Export-Download, NOTIFY (Master), gesperrt bei Native und fuer Leser (F2F3) |
| `08-users` | Anlegen mit Passwortzwang, Sicherheitsdialog (Tokens, 2FA-Reset, Zufallspasswort, Zugaenge widerrufen), externes SSO-Konto (F2F3, F14, F10) |
| `09-settings` | alle Reiter, Reihenfolge, Deep-Link/URL-Sync, Nicht-Admin, SMTP-Maske, Monitoring-Token (FE1b, F8b, F12F13) |
| `10-sso` | Step-up im SSO-Reiter (lokales Konto), Fehlerbanner aller `sso_error`-Codes (F10-APP-FE) |
| `11-dyndns` | Token mit Anleitung, echtes `/nic/update`, Admin-Uebersicht (F9F11-FE) |
| `12-webhooks` | Anlegen, Secret, Test, echte Zustellung, Zustellprotokoll, unlesbare URL (F6-FE) |
| `13-panel-tokens` | Scope/Lesen, Bearbeiten ohne Aenderung, Pausieren, Widerrufen, abgelaufener Token (F14) |
| `14-audit-log` | Paginierung, Filter mit URL, Detail-Drawer, CSV mit Filter, Aufbewahrung (F7) |
| `15-propagation` | Deep-Link, Pruefung mit Record-Vergleich, Zonenwechsel (F12F13-FE) |
| `16-pending-wave3` | LUA (F15), Secrets-Status (F5-FE), DNSKEY-/Parent-DS-Schritt (F4-C) – nur mit `--pending` |
| `17-dialogs` | Lage (Abdeckung, klickbare Knoepfe), Fokus, Tab-Falle, ESC, Fokus-Rueckgabe fuer 10 Dialoge; Lage der aelteren Dialoge (W1-NACHARBEIT 5.6) |
| `18-dashboard-search` | Uebersicht (beide Server online), Suche mit Link in die Zone, Nicht-Admin nur eigene Zonen (F8b) |
| `19-templates` | Vorlage mit Record anlegen (TTL-Pruefung), in der Zonenanlage verwenden, loeschen (FE1b, F8b) |

## CI

Der optionale Job `e2e` in `.github/workflows/test.yml` (nur `workflow_dispatch`) ruft `scripts/e2e/run-e2e.sh --ui`
auf (Eingabe `e2e_ui`, Default an) und laedt bei Fehlern den Arbeitsordner (Bericht, Traces) als Artefakt hoch.
