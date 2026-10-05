#!/usr/bin/env bash
# run-e2e.sh – E2E-Test "Neuinstallation" gegen echte MariaDB + 2x PowerDNS + Webhook-Empfaenger.
#
# Ablauf: Image pdnsmgr-e2e:local bauen -> Umgebung pdnsmgr-e2e (compose.e2e.yaml) frisch starten ->
# Check-Runner ruft check_fresh(ctx) aller scripts/e2e/checks/*.py auf (base zuerst) -> Abbau
# (down -v; Images bleiben). Laeuft komplett in einem Kapazitaets-Slot (scripts/dev/with-slot.sh)
# und haelt den E2E-Lock (nur ein E2E-Lauf je Host).
#
# Aufruf (aus beliebigem Verzeichnis):
#   scripts/e2e/run-e2e.sh [--no-build] [--keep] [--only base,f5] [--list]
#
#   --no-build   vorhandenes Image pdnsmgr-e2e:local verwenden (Default: neu bauen)
#   --keep       Umgebung nach dem Lauf NICHT abbauen (Fehlersuche; spaeter: scripts/e2e/run-e2e.sh --down)
#   --down       nur abbauen (Container, Netz, Volumes des Projekts pdnsmgr-e2e) und beenden
#   --only LISTE nur diese Check-Module (Komma-getrennt, Dateinamen ohne .py)
#   --list       gefundene Check-Module auflisten und beenden (startet keine Container)
#
# Umgebung: E2E_IMAGE (Default pdnsmgr-e2e:local), E2E_WORKDIR (Arbeitsordner, Default mktemp),
#           E2E_WAIT_TIMEOUT (Sekunden, Default 240), E2E_LOG_LEVEL (Backend, Default info).
# Exit-Code: 0 = alle Checks ok, 1 = Check-Fehler oder Startproblem, 2 = Aufruffehler.
set -euo pipefail

# shellcheck source=scripts/e2e/lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

BUILD=1 KEEP=0 ONLY="" LIST=0 DOWN_ONLY=0
ARGS=("$@")
while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-build) BUILD=0 ;;
    --keep) KEEP=1 ;;
    --down) DOWN_ONLY=1 ;;
    --only) shift; ONLY="${1:-}"; [ -n "$ONLY" ] || { echo "--only braucht eine Liste" >&2; exit 2; } ;;
    --only=*) ONLY="${1#--only=}" ;;
    --list) LIST=1 ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unbekanntes Argument: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$LIST" -eq 1 ]; then
  NAMES="$(find "$E2E_DIR/checks" -maxdepth 1 -name '*.py' ! -name '_*' -printf '%f\n' | sed 's/\.py$//' | sort)"
  { grep -x base <<<"$NAMES" || true; grep -vx base <<<"$NAMES" || true; }
  exit 0
fi

e2e_require_tools
e2e_enter_slot "$0" "${ARGS[@]}"

if [ "$DOWN_ONLY" -eq 1 ]; then
  e2e_down
  e2e_log "Umgebung $E2E_PROJECT abgebaut"
  exit 0
fi

e2e_new_secrets
e2e_verify_config
e2e_workdir
STATUS=1

finish() {
  local rc=$?
  [ "$STATUS" -eq 0 ] || rc=1
  if [ "$KEEP" -eq 1 ]; then
    printf '%s\n' "$E2E_ADMIN_PASSWORD" >"$E2E_WORKDIR/admin_password"
    e2e_log "Umgebung bleibt stehen (--keep): Backend $(e2e_backend_url || echo '?'), Admin-Passwort in $E2E_WORKDIR/admin_password"
    e2e_log "Abbau: scripts/e2e/run-e2e.sh --down"
  else
    e2e_save_backend_log "$E2E_WORKDIR/backend-final.log"
    e2e_down
    e2e_log "Umgebung abgebaut (Images bleiben)"
  fi
  if [ "$rc" -eq 0 ] && [ "$KEEP" -eq 0 ]; then
    rm -rf "$E2E_WORKDIR"
  else
    e2e_log "Arbeitsordner (Logs, Report): $E2E_WORKDIR"
  fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT TERM

[ "$BUILD" -eq 1 ] && e2e_build_image
docker image inspect "$E2E_IMAGE" >/dev/null 2>&1 || e2e_die "Image $E2E_IMAGE fehlt (ohne --no-build starten)"

e2e_log "raeume Reste frueherer Laeufe ab"
e2e_down

e2e_log "starte db, pdns1, pdns2, receiver, backend (Projekt $E2E_PROJECT)"
if ! e2e_up db pdns1 pdns2 receiver backend; then
  e2e_save_backend_log "$E2E_WORKDIR/backend-start.log"
  tail -n 60 "$E2E_WORKDIR/backend-start.log" >&2 || true
  exit 1
fi
e2e_save_backend_log "$E2E_WORKDIR/backend-fresh.log"

CHECK_ARGS=(fresh)
[ -n "$ONLY" ] && CHECK_ARGS+=(--only "$ONLY")
e2e_log "Check-Runner: ${CHECK_ARGS[*]}"
if e2e_run_checks "${CHECK_ARGS[@]}"; then
  STATUS=0
  e2e_log "E2E Neuinstallation: OK"
else
  e2e_log "E2E Neuinstallation: FEHLGESCHLAGEN"
  e2e_save_backend_log "$E2E_WORKDIR/backend-fresh.log"
  tail -n 40 "$E2E_WORKDIR/backend-fresh.log" >&2 || true
fi
exit "$STATUS"
