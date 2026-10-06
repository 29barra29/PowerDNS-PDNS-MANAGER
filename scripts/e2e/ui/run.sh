#!/usr/bin/env bash
# run.sh – UI-Smoke-Tests (Playwright/Chromium im Container) gegen einen LAUFENDEN E2E-Stack.
#
# Ablauf: Stack pruefen (pdnsmgr-e2e-api healthy) -> zweite Backend-Instanz fuer den Einrichtungs-
# assistenten mit leerer Datenbank starten (setup-backend) -> Playwright-Container im Netz pdnsmgr-e2e-net
# ("playwright test", ein Worker, Retries 1) -> setup-backend wieder entfernen. Der Stack selbst bleibt stehen.
#
# Aufruf (aus beliebigem Verzeichnis):
#   scripts/e2e/run-e2e.sh --keep          # Stack starten und stehen lassen (einmal)
#   scripts/e2e/ui/run.sh [Optionen] [-- <playwright-Argumente>]
#   scripts/e2e/run-e2e.sh --down          # spaeter abbauen
#
#   --workdir DIR  Arbeitsordner (Default: $E2E_WORKDIR bzw. mktemp); Ergebnisse unter DIR/ui
#                  (report/ = HTML-Bericht, test-results/ = Traces/Screenshots fehlgeschlagener Tests)
#   --pending      auch Specs fuer noch nicht integrierte Welle-3-UI ausfuehren (E2E_UI_PENDING=1)
#   --no-setup     ohne setup-backend (Spec "Einrichtungsassistent" wird uebersprungen)
#   -- ARGS        weiter an "playwright test", z. B. -- tests/03-zones-records.spec.js --grep Fan-out
#
# Umgebung: E2E_ADMIN_PASSWORD (sonst aus DIR/admin_password bzw. dem laufenden Backend-Container),
#           E2E_UI_IMAGE (Default mcr.microsoft.com/playwright:v<Version aus package.json>-noble),
#           E2E_UI_NPM_CACHE (Default ${XDG_CACHE_HOME:-$HOME/.cache}/pdnsmgr-e2e-ui-npm), E2E_UI_RETRIES (Default 1).
# Exit-Code: 0 = alle Specs gruen (Skips erlaubt), 1 = Fehler, 2 = Aufruffehler.
set -euo pipefail

UI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/e2e/lib.sh
. "$UI_DIR/../lib.sh"
UI_COMPOSE_FILE="$UI_DIR/compose.ui.yaml"

WORKDIR="${E2E_WORKDIR:-}" PENDING="${E2E_UI_PENDING:-}" WITH_SETUP=1
ARGS=("$@")
PW_ARGS=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --workdir) shift; WORKDIR="${1:-}"; [ -n "$WORKDIR" ] || { echo "--workdir braucht einen Ordner" >&2; exit 2; } ;;
    --workdir=*) WORKDIR="${1#--workdir=}" ;;
    --pending) PENDING=1 ;;
    --no-setup) WITH_SETUP=0 ;;
    --) shift; PW_ARGS=("$@"); break ;;
    -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unbekanntes Argument: $1 (Playwright-Argumente nach --)" >&2; exit 2 ;;
  esac
  shift
done

# Basis-Datei + UI-Ergaenzung; Profil "ui" ist immer aktiv (fuer config/up/run/rm).
e2e_compose() {
  COMPOSE_PROFILES="${COMPOSE_PROFILES:+$COMPOSE_PROFILES,}ui" \
    docker compose -p "$E2E_PROJECT" -f "$E2E_COMPOSE_FILE" -f "$UI_COMPOSE_FILE" "$@"
}

ui_log() { printf '[ui %s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }

e2e_require_tools
e2e_enter_slot "$0" "${ARGS[@]}"
T0=$(date +%s)

# --- Playwright-Image: Version muss zu @playwright/test (package.json) passen ------------------
PW_VERSION="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["devDependencies"]["@playwright/test"])' "$UI_DIR/package.json")"
E2E_UI_IMAGE="${E2E_UI_IMAGE:-mcr.microsoft.com/playwright:v${PW_VERSION}-noble}"
case "$E2E_UI_IMAGE" in
  *":v${PW_VERSION}-"*) ;;
  *) e2e_die "E2E_UI_IMAGE=$E2E_UI_IMAGE passt nicht zu @playwright/test $PW_VERSION (package.json)" ;;
esac
export E2E_UI_IMAGE
if ! docker image inspect "$E2E_UI_IMAGE" >/dev/null 2>&1; then
  ui_log "lade $E2E_UI_IMAGE (einmalig, ca. 2 GB) ..."
  docker pull -q "$E2E_UI_IMAGE" >/dev/null || e2e_die "docker pull $E2E_UI_IMAGE fehlgeschlagen"
fi

# --- laufender Stack? -----------------------------------------------------------------------
health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' pdnsmgr-e2e-api 2>/dev/null || true)"
[ "$health" = "healthy" ] || e2e_die "kein laufender E2E-Stack (pdnsmgr-e2e-api: ${health:-fehlt}) – zuerst scripts/e2e/run-e2e.sh --keep"
# Gleiches Image wie das laufende Backend auch fuer setup-backend
E2E_IMAGE="$(docker inspect -f '{{.Config.Image}}' pdnsmgr-e2e-api)"
export E2E_IMAGE
ui_log "laufender Stack: Backend-Image $E2E_IMAGE"

if [ -z "${E2E_ADMIN_PASSWORD:-}" ] && [ -n "$WORKDIR" ] && [ -r "$WORKDIR/admin_password" ]; then
  E2E_ADMIN_PASSWORD="$(head -n1 "$WORKDIR/admin_password")"
fi
if [ -z "${E2E_ADMIN_PASSWORD:-}" ]; then
  # Testgeheimnis des laufenden E2E-Backends (nur dieses eine Feld, wird nicht ausgegeben)
  E2E_ADMIN_PASSWORD="$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' pdnsmgr-e2e-api \
    | sed -n 's/^INITIAL_ADMIN_PASSWORD=//p' | head -n1)"
fi
[ -n "${E2E_ADMIN_PASSWORD:-}" ] || e2e_die "Admin-Passwort des E2E-Stacks unbekannt (E2E_ADMIN_PASSWORD setzen)"
# compose.e2e.yaml verlangt beide Variablen fuer die Interpolation; der laufende Dienst "backend" wird nicht angefasst.
E2E_JWT_SECRET="${E2E_JWT_SECRET:-ui-run-unused}"
E2E_SETUP_JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
export E2E_ADMIN_PASSWORD E2E_JWT_SECRET E2E_SETUP_JWT_SECRET

if [ -z "$WORKDIR" ]; then
  WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/pdnsmgr-e2e-ui.XXXXXX")"
fi
OUT="$WORKDIR/ui"
mkdir -p "$OUT"
chmod 700 "$WORKDIR" 2>/dev/null || true
E2E_UI_OUT="$OUT"
E2E_UI_NPM_CACHE="${E2E_UI_NPM_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/pdnsmgr-e2e-ui-npm}"
mkdir -p "$E2E_UI_NPM_CACHE"
E2E_UID="$(id -u)" E2E_GID="$(id -g)"
E2E_UI_PENDING="$PENDING"
export E2E_UI_OUT E2E_UI_NPM_CACHE E2E_UID E2E_GID E2E_UI_PENDING

# Isolationsregeln auch fuer die zusammengefuehrte Konfiguration (Namen, Netz, keine Host-Ports)
e2e_verify_config

cleanup_ui() {
  e2e_compose rm -sf setup-backend >/dev/null 2>&1 || true
  docker rm -f pdnsmgr-e2e-ui >/dev/null 2>&1 || true
}
trap cleanup_ui EXIT
trap 'exit 130' INT TERM

# --- setup-backend: zweite Instanz mit leerer Datenbank --------------------------------------
if [ "$WITH_SETUP" -eq 1 ]; then
  ui_log "setup-backend: leere Datenbank dns_manager_setup, Start ..."
  e2e_compose rm -sf setup-backend >/dev/null 2>&1 || true
  e2e_compose exec -T -e MYSQL_PWD=e2e_root db mariadb -uroot -e \
    "DROP DATABASE IF EXISTS dns_manager_setup; CREATE DATABASE dns_manager_setup; GRANT ALL PRIVILEGES ON dns_manager_setup.* TO 'dns_admin'@'%';" \
    || e2e_die "Datenbank dns_manager_setup konnte nicht angelegt werden"
  if ! e2e_compose up -d --no-deps --wait --wait-timeout "${E2E_WAIT_TIMEOUT:-240}" setup-backend >/dev/null; then
    docker logs --tail 60 pdnsmgr-e2e-api-setup >&2 2>&1 || true
    e2e_die "setup-backend startet nicht"
  fi
  unset E2E_UI_SETUP_URL
else
  export E2E_UI_SETUP_URL=""
fi

# --- Playwright ---------------------------------------------------------------------------
ui_log "Playwright ($E2E_UI_IMAGE) gegen http://backend:8000, Ergebnisse: $OUT"
rc=0
e2e_compose run --rm -T --no-deps ui "${PW_ARGS[@]}" || rc=$?
if [ "$WITH_SETUP" -eq 1 ]; then
  docker logs pdnsmgr-e2e-api-setup >"$OUT/setup-backend.log" 2>&1 || true
  chmod 600 "$OUT/setup-backend.log" 2>/dev/null || true
fi

DUR=$(( $(date +%s) - T0 ))
if [ "$rc" -eq 0 ]; then
  ui_log "UI-Smoke: OK (${DUR} s)"
else
  ui_log "UI-Smoke: FEHLGESCHLAGEN (rc=$rc, ${DUR} s) – Bericht: $OUT/report/index.html, Traces: $OUT/test-results"
  rc=1
fi
exit "$rc"
