# Entwickler-Skripte (`scripts/dev/`)

Hilfsskripte fuer die isolierte Entwicklung von PDNS Manager 3.0 auf einem Host, der gleichzeitig die
Produktion traegt. Sie ersetzen **kein** `docker compose`: `compose.yaml` hat feste `container_name`
(`dns-manager-db`, `dns-manager-api`) – ein `docker compose up` in einem Worktree oder einer Kopie wuerde die
Produktions-Container ersetzen. Deshalb gilt: nie `docker compose` in Repo, Kopie oder Worktree; nur
`docker build`/`docker run` mit eigenen Namen und Tags. `.env` wird nie gelesen oder kopiert.

| Skript | Zweck |
|---|---|
| `with-slot.sh <cmd …>` | fuehrt ein schweres Kommando erst aus, wenn einer von 2 Slots frei ist (flock) |
| `build-test-image.sh <n>` | baut das Backend-Testimage `pdnsmgr-test:w<n>` (Python 3.12 + Lock + pytest) |
| `test-db.sh up\|create\|drop\|url\|down\|status` | gemeinsame Wegwerf-MariaDB `pdnsmgr-test-db` |

Alle Beispiele werden aus dem Wurzelverzeichnis des Worktrees aufgerufen.

## `with-slot.sh` – Kapazitaetsbremse

Der Host hat 2 CPUs. Schwere Schritte (`docker build`, `npm ci`/`npm run build`, DB-Testlaeufe) laufen
deshalb nur ueber dieses Skript:

```bash
scripts/dev/with-slot.sh docker build -t pdnsmgr-e2e:local -f backend/Dockerfile .
scripts/dev/with-slot.sh npm --prefix frontend run build
```

- Slots sind die Lock-Dateien `/tmp/pdnsmgr-slots/0` und `/tmp/pdnsmgr-slots/1` (Verzeichnis wird bei Bedarf
  angelegt). Sind beide belegt, wartet das Skript (Meldung auf stderr) und prueft alle 2 s erneut.
- Exit-Code = Exit-Code des Kommandos; `124` bei Zeitueberschreitung (`PDNSMGR_SLOT_TIMEOUT=<s>`), `2` bei
  Aufruffehlern.
- Das Kommando bekommt `PDNSMGR_SLOT=<nr>`. Verschachtelte Aufrufe (z. B. `build-test-image.sh`, das selbst
  `with-slot.sh` nutzt, innerhalb eines Slots) laufen direkt im bestehenden Slot – kein Deadlock.
- Der Slot wird frei, sobald das Kommando endet (auch bei Abbruch/Kill des Skripts, da flock an den Prozess
  gebunden ist). Weitere Schalter: `PDNSMGR_SLOT_DIR`, `PDNSMGR_SLOTS` (Default 2).

## `build-test-image.sh <n>` – Testimage je Welle

```bash
scripts/dev/build-test-image.sh 0      # -> pdnsmgr-test:w0
```

- Basis `python:3.12-slim`, darauf `pip install -r backend/requirements.lock` und `pytest pytest-asyncio
  pytest-cov` (wie CI), `pip check`, `ENV DB_POOL_SIZE=0`, `WORKDIR /work`.
- Build-Kontext ist ein temporaeres Verzeichnis mit **nur** `requirements.lock` (kein App-Code, keine `.env`).
  Labels: `pdnsmgr.wave`, `pdnsmgr.lock-sha256`, `pdnsmgr.git-rev`. Die installierten Versionen stehen im Image
  unter `/opt/pdnsmgr/pip-freeze.txt`.
- Laeuft automatisch ueber `with-slot.sh`. Einmal je Welle (bzw. nach einer Lock-Aenderung) bauen; danach
  kein `pip install` mehr pro Testlauf.

Pruefen, ob das Image zum aktuellen Lock passt:

```bash
docker inspect -f '{{index .Config.Labels "pdnsmgr.lock-sha256"}}' pdnsmgr-test:w0
sha256sum backend/requirements.lock
```

### Backend-Tests ohne DB (Unit)

Der `backend`-Ordner wird read-only eingehaengt und im Container nach `/work/backend` kopiert (Tests und
Config duerfen dort schreiben, der Worktree bleibt unberuehrt):

```bash
scripts/dev/with-slot.sh docker run --rm -v "$PWD/backend:/src:ro" \
  -e DB_POOL_SIZE=0 -e JWT_SECRET_KEY=test pdnsmgr-test:w0 \
  bash -c 'cp -r /src /work/backend && cd /work/backend && python -m pytest -q'
```

Einzelne Tests: `python -m pytest -q tests/test_xyz.py -k name`. Integrationstests paralleler Workstreams:
`-m ""` (alle Marker, inkl. `wave_integration`).

### Backend-Tests mit Test-MariaDB

```bash
scripts/dev/test-db.sh up                       # einmal; laeuft bereits -> nur Bereitschaftspruefung
scripts/dev/test-db.sh create ws_w0_deps        # eigene DB je Workstream (Konvention ws_<id>, klein, "_")
scripts/dev/with-slot.sh docker run --rm --network pdnsmgr-test-net -v "$PWD/backend:/src:ro" \
  -e CI=true -e RUN_DB_TESTS=1 -e DB_POOL_SIZE=0 -e JWT_SECRET_KEY=test-secret-key \
  -e DATABASE_URL="$(scripts/dev/test-db.sh url ws_w0_deps)" pdnsmgr-test:w0 \
  bash -c 'cp -r /src /work/backend && cd /work/backend && python -m pytest -q -rs'
scripts/dev/test-db.sh drop ws_w0_deps          # frischer Stand: drop + create
```

`CI=true` schaltet `tests/test_health.py` frei (startet den lifespan inkl. `init_db`).

## `test-db.sh` – gemeinsame Test-MariaDB

| Befehl | Wirkung |
|---|---|
| `up` | Netz `pdnsmgr-test-net` + Container `pdnsmgr-test-db` (`mariadb:11`, utf8mb4, `--memory 1g --cpus 1`, **kein Host-Port**) starten und auf Bereitschaft warten; idempotent |
| `create <db>` | `CREATE DATABASE IF NOT EXISTS` + Rechte fuer den Testnutzer; idempotent |
| `drop <db>` | `DROP DATABASE IF EXISTS` |
| `url <db>` | gibt `mysql+aiomysql://pdnsmgr_test:pdnsmgr_test@pdnsmgr-test-db:3306/<db>` aus (nur aus Containern im Netz `pdnsmgr-test-net` erreichbar) |
| `down [--force]` | Container und Netz entfernen; verweigert, solange noch Datenbanken existieren (andere Workstreams!) – `--force` nur durch den Integrator |
| `status` | Zustand und vorhandene Datenbanken |

- Zugangsdaten sind feste Testwerte (Root `pdnsmgr_test_root`, Nutzer `pdnsmgr_test`), da der Container nur am
  eigenen Netz haengt. Die Datenbank ist fluechtig (`--rm`, kein Volume).
- Datenbanknamen: `[A-Za-z0-9_]`, max. 64 Zeichen.
- `up`/`down` sind per flock (`/tmp/pdnsmgr-slots/test-db.lock`) gegen parallele Aufrufe geschuetzt.
- Wer fertig ist, raeumt **seine** Datenbank mit `drop` auf; `down` nur, wenn keine Datenbank mehr existiert.

## Lockfile neu erzeugen (`backend/requirements.lock`)

Nur nach einer Aenderung an `backend/requirements.txt` (laut Bauplan einmalig in W0-DEPS; danach keine neuen
Abhaengigkeiten). Das bestehende Lock wird als Vorgabe mitgegeben (`-o`), damit uv **nur** die neuen Pakete
aufloest und bestehende transitive Pins nicht still anhebt (ohne `-o` hebt ein frisches Kompilieren z. B.
`starlette`, `pymysql`, `uvloop` an):

```bash
D=$(mktemp -d); cp backend/requirements.txt "$D/"; sed -n '16,$p' backend/requirements.lock > "$D/requirements.lock"
scripts/dev/with-slot.sh docker run --rm -v "$D:/req" python:3.12-slim bash -c \
  "pip install -q --root-user-action=ignore --disable-pip-version-check uv && cd /req && uv pip compile requirements.txt -o requirements.lock --no-header -q && chown $(id -u):$(id -g) requirements.lock"
{ head -15 backend/requirements.lock; cat "$D/requirements.lock"; } > "$D/lock.new" && mv "$D/lock.new" backend/requirements.lock
rm -rf "$D"; git diff --stat backend/requirements.lock
```

Der 15-zeilige Kommentarkopf bleibt von Hand erhalten (`--no-header`). Bewusste Updates aller Pins:
`--upgrade` bzw. `--upgrade-package <name>` ergaenzen. Danach Testimage neu bauen und Tests erneut laufen
lassen. Neue Pakete muessen Wheels fuer linux/amd64 **und** linux/arm64 haben (Release-Build):

```bash
docker run --rm -v "$PWD/backend/requirements.lock:/r.lock:ro" python:3.12-slim bash -c \
  'grep -E "^[A-Za-z]" /r.lock > /tmp/r.txt && pip download -q --no-deps --only-binary=:all: \
   --python-version 3.12 --implementation cp --platform manylinux_2_28_aarch64 \
   --platform manylinux2014_aarch64 --platform manylinux_2_17_aarch64 -d /tmp/w -r /tmp/r.txt && echo ok'
```
