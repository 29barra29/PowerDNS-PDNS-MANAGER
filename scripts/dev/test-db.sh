#!/usr/bin/env bash
# test-db.sh – gemeinsame Wegwerf-MariaDB fuer Backend-DB-Tests (eine Datenbank je Workstream).
#
#   up            startet Container pdnsmgr-test-db (mariadb:11) im Netz pdnsmgr-test-net,
#                 KEIN Host-Port; wartet, bis die DB bereit ist (idempotent)
#   create <db>   legt Datenbank <db> an (falls fehlt) und gibt dem Testnutzer Rechte darauf
#   drop <db>     loescht Datenbank <db> (falls vorhanden)
#   url <db>      gibt die DATABASE_URL fuer <db> aus (nur aus dem Netz pdnsmgr-test-net erreichbar)
#   down [--force] stoppt/entfernt Container und Netz (Daten sind danach weg); verweigert, solange
#                 noch Workstream-Datenbanken existieren (andere Agenten!), ausser mit --force
#   status        zeigt Zustand und vorhandene Datenbanken
#
# Datenbanknamen: [A-Za-z0-9_], max. 64 Zeichen; Konvention ws_<id> klein, "-" als "_" (W0-DEPS -> ws_w0_deps).
# Beispiel:
#   scripts/dev/test-db.sh up && scripts/dev/test-db.sh create ws_w0_deps
#   docker run --rm --network pdnsmgr-test-net -e RUN_DB_TESTS=1 \
#     -e DATABASE_URL="$(scripts/dev/test-db.sh url ws_w0_deps)" ... pdnsmgr-test:w0 ...
#
# Zugangsdaten sind feste Testwerte: der Container hat keinen Host-Port und haengt nur am
# eigenen Netz pdnsmgr-test-net (kein Kontakt zu Produktions-Netzen/-Containern).
#
# Umgebung (nur bei Bedarf):
#   PDNSMGR_TEST_DB_IMAGE   Default mariadb:11
#   PDNSMGR_TEST_DB_MEMORY  Speicherlimit des Containers (Default 1g)
#   PDNSMGR_TEST_DB_CPUS    CPU-Limit des Containers (Default 1)
set -euo pipefail

CONTAINER=pdnsmgr-test-db
NETWORK=pdnsmgr-test-net
IMAGE="${PDNSMGR_TEST_DB_IMAGE:-mariadb:11}"
MEMORY="${PDNSMGR_TEST_DB_MEMORY:-1g}"
CPUS="${PDNSMGR_TEST_DB_CPUS:-1}"
ROOT_PW=pdnsmgr_test_root
DB_USER=pdnsmgr_test
DB_PW=pdnsmgr_test
LOCK_DIR="${PDNSMGR_SLOT_DIR:-/tmp/pdnsmgr-slots}"

usage() {
  sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

die() { echo "[test-db] $*" >&2; exit 1; }

valid_db() {
  [[ "${1:-}" =~ ^[A-Za-z0-9_]{1,64}$ ]] || die "ungueltiger Datenbankname '${1:-}' (erlaubt: A-Z a-z 0-9 _, max. 64)"
}

# up/down gegen parallele Aufrufe mehrerer Agenten serialisieren.
with_lock() {
  mkdir -p "$LOCK_DIR"
  exec {lfd}>>"$LOCK_DIR/test-db.lock"
  flock "$lfd"
  "$@"
  flock -u "$lfd"
  exec {lfd}>&-
}

running() {
  [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)" = "true" ]
}

sql() {
  # Passwort per Umgebung statt Argument (nicht in der Prozessliste sichtbar).
  docker exec -i -e MYSQL_PWD="$ROOT_PW" "$CONTAINER" mariadb -uroot --batch --skip-column-names "$@"
}

wait_ready() {
  local i
  for i in $(seq 1 90); do
    if docker exec "$CONTAINER" healthcheck.sh --connect --innodb_initialized >/dev/null 2>&1 \
       && sql -e "SELECT 1" >/dev/null 2>&1; then
      return 0
    fi
    running || die "Container $CONTAINER ist beendet (docker logs $CONTAINER pruefen)"
    sleep 2
  done
  die "MariaDB nach 180 s nicht bereit"
}

do_up() {
  if ! docker network inspect "$NETWORK" >/dev/null 2>&1; then
    docker network create "$NETWORK" >/dev/null
    echo "[test-db] Netz $NETWORK angelegt" >&2
  fi
  if running; then
    echo "[test-db] $CONTAINER laeuft bereits" >&2
  else
    # Gestoppte Altlast entfernen (--rm greift nur bei sauberem Stop).
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker run -d --rm --name "$CONTAINER" --network "$NETWORK" \
      --memory "$MEMORY" --cpus "$CPUS" \
      --label pdnsmgr.role=test-db \
      -e MARIADB_ROOT_PASSWORD="$ROOT_PW" \
      -e MARIADB_USER="$DB_USER" -e MARIADB_PASSWORD="$DB_PW" \
      "$IMAGE" --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci >/dev/null
    echo "[test-db] $CONTAINER gestartet ($IMAGE), warte auf Bereitschaft ..." >&2
  fi
  wait_ready
  echo "[test-db] bereit" >&2
}

user_dbs() {
  sql -e "SHOW DATABASES" | grep -Ev '^(information_schema|mysql|performance_schema|sys)$' || true
}

do_down() {
  local force="$1" left
  if running && [ "$force" != "--force" ]; then
    left="$(user_dbs | tr '\n' ' ')"
    if [ -n "${left// /}" ]; then
      die "noch Datenbanken vorhanden: ${left}– erst 'drop <db>' oder 'down --force'"
    fi
  fi
  if docker inspect "$CONTAINER" >/dev/null 2>&1; then
    docker rm -f "$CONTAINER" >/dev/null
    echo "[test-db] $CONTAINER entfernt" >&2
  fi
  if docker network inspect "$NETWORK" >/dev/null 2>&1; then
    docker network rm "$NETWORK" >/dev/null || die "Netz $NETWORK noch in Benutzung (laufende Testcontainer?)"
    echo "[test-db] Netz $NETWORK entfernt" >&2
  fi
}

need_running() {
  running || die "$CONTAINER laeuft nicht – zuerst: $0 up"
}

cmd="${1:-}"
case "$cmd" in
  up)
    [ "$#" -eq 1 ] || usage
    with_lock do_up
    ;;
  down)
    [ "$#" -eq 1 ] || { [ "$#" -eq 2 ] && [ "$2" = "--force" ]; } || usage
    with_lock do_down "${2:-}"
    ;;
  create)
    [ "$#" -eq 2 ] || usage
    valid_db "$2"; need_running
    sql -e "CREATE DATABASE IF NOT EXISTS \`$2\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
            GRANT ALL PRIVILEGES ON \`$2\`.* TO '$DB_USER'@'%';"
    echo "[test-db] Datenbank $2 bereit" >&2
    ;;
  drop)
    [ "$#" -eq 2 ] || usage
    valid_db "$2"; need_running
    sql -e "DROP DATABASE IF EXISTS \`$2\`;"
    echo "[test-db] Datenbank $2 geloescht" >&2
    ;;
  url)
    [ "$#" -eq 2 ] || usage
    valid_db "$2"
    echo "mysql+aiomysql://$DB_USER:$DB_PW@$CONTAINER:3306/$2"
    ;;
  status)
    if running; then
      echo "$CONTAINER: laeuft (Netz $NETWORK)"
      user_dbs | sed 's/^/  db: /'
    else
      echo "$CONTAINER: gestoppt"
    fi
    ;;
  *)
    usage
    ;;
esac
