# E2E-Tests (`scripts/e2e/`)

End-to-End-Tests von PDNS Manager gegen echte Dienste: MariaDB 11, zwei PowerDNS-Server
(`powerdns/pdns-auth-49`, API + LUA-Records) und einen Webhook-Empfaenger. Alles laeuft isoliert im
Compose-Projekt `pdnsmgr-e2e` und beruehrt eine parallel laufende Produktion nicht.

| Datei | Zweck |
|---|---|
| `run-e2e.sh` | Neuinstallation: Image bauen, Umgebung starten, `check_fresh(ctx)` aller Check-Module, Abbau |
| `upgrade-241-to-30.sh` | Upgrade-Pfad: 2.4.1-Datenbank seeden, aktuellen Stand starten, `check_upgrade(ctx)`, zweiter Start, `check_upgrade_restart(ctx)`, Abbau |
| `static-checks.sh` | statische Regeln des Bauplans (grep/AST, ohne Container) |
| `compose.e2e.yaml` | Dienste `db`, `pdns1`, `pdns2`, `receiver`, `backend` (+ Werkzeug-Dienst `tools`) |
| `lib.sh` | gemeinsame Shell-Funktionen der beiden Lauf-Skripte |
| `seed_241.py` | erzeugt mit dem 2.4.1-Code Schema und Altdaten fuer den Upgrade-Test |
| `webhook-receiver.py` | Webhook-Empfaenger (nur Standardbibliothek) |
| `pdns.conf` | gemeinsame PowerDNS-Einstellungen beider Testserver |
| `checks/` | Check-Framework (`__init__.py`), Basis-Checks (`base.py`), Feature-Checks je Workstream, [ctx-API](checks/README.md) |

## Sicherheit: Produktion nicht beruehren

- Die `compose.yaml` im Repo-Wurzelverzeichnis hat feste Containernamen der Produktion
  (`dns-manager-api`, `dns-manager-db`). Sie wird von diesen Skripten **nie** verwendet – auch nicht in
  Kopien oder Worktrees. Die Skripte rufen ausschliesslich
  `docker compose -p pdnsmgr-e2e -f scripts/e2e/compose.e2e.yaml …` auf.
- Vor jedem Start prueft `lib.sh` die aufgeloeste Konfiguration: alle Container heissen
  `pdnsmgr-e2e-*`, einziges Netz `pdnsmgr-e2e-net`, Host-Ports nur auf `127.0.0.1` und nur zufaellig
  vergeben (das Backend zur Fehlersuche, Port per `docker compose -p pdnsmgr-e2e -f scripts/e2e/compose.e2e.yaml port backend 8000`).
- Es gibt keine `.env`: Zugangsdaten der Testdienste sind feste Testwerte im internen Netz, JWT-Secret
  und Admin-Passwort erzeugt jeder Lauf neu.
- Ein E2E-Lauf je Host (flock `/tmp/pdnsmgr-slots/e2e.lock`); der ganze Lauf belegt einen
  Kapazitaets-Slot (`scripts/dev/with-slot.sh`, siehe `scripts/dev/README.md`).
- Abbau mit `down -v`: Container, Netz und Volumes des Projekts werden entfernt, Images bleiben.

## Benutzung

Aus der Wurzel des Checkouts (Worktree):

```bash
scripts/e2e/run-e2e.sh                     # baut pdnsmgr-e2e:local, Neuinstallation, alle check_fresh
scripts/e2e/upgrade-241-to-30.sh --no-build # nutzt das Image, Upgrade v2.4.1 -> aktueller Stand
scripts/e2e/static-checks.sh               # statische Regeln (vor Wellenende 0b: --legacy-ok)
```

Optionen (beide Lauf-Skripte): `--no-build` (vorhandenes Image), `--keep` (Umgebung stehen lassen,
Abbau spaeter mit `scripts/e2e/run-e2e.sh --down`), `--only base,f5` (nur diese Check-Module).
`run-e2e.sh --list` zeigt die gefundenen Module. `upgrade-241-to-30.sh --ref <tag>` waehlt den
Altstand (Default `v2.4.1`, braucht die Git-Tags).

Umgebung: `E2E_IMAGE` (Default `pdnsmgr-e2e:local`), `E2E_WORKDIR` (Arbeitsordner, Default `mktemp`),
`E2E_WAIT_TIMEOUT` (Sekunden bis „healthy“, Default 240), `E2E_LOG_LEVEL` (Backend, Default `info`),
`E2E_PDNS_LOGLEVEL` (PowerDNS, Default 5).

Exit-Code 0 = alle Checks bestanden. Bei Fehlern bleibt der Arbeitsordner erhalten (Pfad steht am
Ende der Ausgabe) mit `backend-*.log`, `report-<modus>.json` und – beim Upgrade – `seed.json`
(Testgeheimnisse, Modus 0600).

Typische Laufzeiten auf dem Entwicklungs-Host (warmer Build-Cache): Image ~20 s, Neuinstallation ~35 s,
Upgrade ~40 s. Kalter Build 3–5 Minuten (npm ci + Frontend-Build im Dockerfile).

## Ablauf im Detail

**Neuinstallation (`run-e2e.sh`)**: Image aus `backend/Dockerfile` bauen (Smoke `import app.main`) →
Reste frueherer Laeufe entfernen → alle Dienste starten und auf `healthy` warten (`PDNS_SERVERS`
traegt beide PowerDNS-Server ein, `INITIAL_ADMIN_PASSWORD` legt den Admin an) → Runner im Dienst
`tools` (gleiches Image, `scripts/e2e` read-only unter `/e2e`, Arbeitsordner unter `/state`):
Admin-Login, Admin-Panel-Token, Testbenutzer `e2e-user` mit eigenem Panel-Token → `check_fresh(ctx)`
aller Module (`base` zuerst, dann alphabetisch) → Abbau.

**Upgrade (`upgrade-241-to-30.sh`)**: `git archive <ref> VERSION backend` in den Arbeitsordner →
`db`, `pdns1`, `pdns2`, `receiver` starten → `seed_241.py` mit `PYTHONPATH=/legacy/backend`
(2.4.1-`init_db`, Hashes und Datenformate aus dem 2.4.1-Code; Inhalt siehe Docstring) →
Backend des aktuellen Stands starten (erster Start migriert) → `check_upgrade(ctx)` →
`docker compose restart backend` → `check_upgrade_restart(ctx)` (Module ohne diese Funktion werden
uebersprungen) → Abbau. Der 2.4.1-Code laeuft dabei im aktuellen Image (gleiche oder neuere
Paketversionen), nicht in einem echten 2.4.1-Image; ein Downgrade-Test (F5) braucht dafuer ein
eigenes 2.4.1-Image.

## Webhook-Empfaenger

Im E2E-Netz unter `http://receiver:8080`. Webhook-Ziele: `ctx.receiver.url("name", fail=2)` →
`http://receiver:8080/hook/name?fail=2`. Query-Parameter: `status=<code>` (feste Antwort), `fail=N`
(erste N Versuche je Zustellung mit 500), `delay=S` (Antwort verzoegern, max. 60 s).
`GET /deliveries[?path=/hook/name]` liefert alle Zustellungen mit Headern und exaktem Rohkoerper
(`body_b64`, fuer HMAC-Pruefungen); `DELETE /deliveries` leert den Speicher.

## Neue Checks (Feature-Workstreams)

Jeder Workstream mit neuer API liefert **eine eigene Datei** `scripts/e2e/checks/<ws>.py`
(z. B. `f6.py`, `f9f11.py`) mit `check_fresh(ctx)` und/oder `check_upgrade(ctx)`
(optional `check_upgrade_restart(ctx)`). Die Skripte finden sie automatisch; `base.py`,
`__init__.py` und die Skripte werden dafuer nicht geaendert. API und Konventionen:
[checks/README.md](checks/README.md).

## Statische Regeln (`static-checks.sh`)

| Regel | ab | prueft |
|---|---|---|
| `shell-syntax` | sofort | `bash -n` aller `*.sh` (+ `shellcheck -S error`, falls installiert) |
| `e2e-checks` | sofort | Check-Module kompilieren und definieren eine `check_*`-Funktion |
| `webhook-legacy` | 0b | kein `deliver_webhooks_background` in `backend/app` |
| `role-admin` | 0b | kein Inline-`x.role == "admin"` ausserhalb `core/auth.py` (Ausnahme: Kommentar `static-ok: role-admin`) |
| `audit-raw` | 0b | kein rohes `AuditLog(` ausserhalb `services/audit.py` |
| `router-order` | 0b | jedes Router-Modul hat `ROUTER_ORDER` oder einen `LEGACY_ROUTER_ORDER`-Eintrag, keine Dopplungen |
| `route-policy` | 0b | `tests/route_policy/` + `tests/test_route_policy.py` vorhanden (Vollstaendigkeit: pytest) |
| `dbwrite-test` | 0b | `tests/test_db_dependency_scope.py` vorhanden (Pruefung: pytest) |
| `locales-json` | sofort | `frontend/src/locales/<lang>.json` nur in Commits mit Betreff `locales:` geaendert (seit `--base`, Default merge-base mit `main`) |
| `fragments-empty` | `--wave-end` | keine Locale-Fragmente mehr nach dem Merge |

Vor dem Ende von Welle 0b sind die 0b-Regeln auf dem 2.4.1-Stand erwartungsgemaess rot; mit
`--legacy-ok` melden sie WARN statt FAIL.
