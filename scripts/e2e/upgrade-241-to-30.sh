#!/usr/bin/env bash
# upgrade-241-to-30.sh – E2E-Test "Upgrade": 2.4.1-Datenbank mit Altdaten -> aktueller Stand (3.0).
#
# Ablauf:
#   1. Image pdnsmgr-e2e:local aus dem aktuellen Checkout bauen (Default; --no-build ueberspringt)
#   2. `git archive <ref>` (Default v2.4.1) -> Quellstand 2.4.1 in den Arbeitsordner
#   3. db, pdns1, pdns2, receiver starten (Projekt pdnsmgr-e2e, compose.e2e.yaml)
#   4. seed_241.py mit dem 2.4.1-Code: init_db (2.4.1-Schema) + Altdaten (TOTP-Nutzerin, Server mit
#      Klartext-Key, Webhooks, Panel-Tokens aktiv/inaktiv, Zonenrechte, Audit v1, SMTP/Captcha-Secret)
#   5. Backend des aktuellen Stands starten (Migration laeuft beim Start) -> check_upgrade(ctx) aller
#      scripts/e2e/checks/*.py (base zuerst)
#   6. Backend neu starten (zweiter Start) -> check_upgrade_restart(ctx), wo vorhanden
#   7. Abbau (down -v; Images bleiben)
# Laeuft in einem Kapazitaets-Slot (scripts/dev/with-slot.sh) und haelt den E2E-Lock.
#
# Aufruf:
#   scripts/e2e/upgrade-241-to-30.sh [--no-build] [--keep] [--ref v2.4.1] [--only base,f5]
#
#   --ref REF    Git-Ref des Altstands (Default v2.4.1; muss im Repo vorhanden sein)
#   --no-build / --keep / --only wie bei run-e2e.sh; Abbau nach --keep: scripts/e2e/run-e2e.sh --down
#
# Hinweis: Der 2.4.1-Code laeuft fuer init_db und Seed im aktuellen Image (gleiche bzw. neuere
# Paketversionen, Python 3.12) – nicht in einem echten 2.4.1-Image. Schema, Hashes und Datenformate
# stammen trotzdem vollstaendig aus dem 2.4.1-Code.
set -euo pipefail

# shellcheck source=scripts/e2e/lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

BUILD=1 KEEP=0 ONLY="" REF="v2.4.1"
ARGS=("$@")
while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-build) BUILD=0 ;;
    --keep) KEEP=1 ;;
    --ref) shift; REF="${1:-}"; [ -n "$REF" ] || { echo "--ref braucht einen Wert" >&2; exit 2; } ;;
    --ref=*) REF="${1#--ref=}" ;;
    --only) shift; ONLY="${1:-}"; [ -n "$ONLY" ] || { echo "--only braucht eine Liste" >&2; exit 2; } ;;
    --only=*) ONLY="${1#--only=}" ;;
    -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unbekanntes Argument: $1" >&2; exit 2 ;;
  esac
  shift
done

e2e_require_tools
git -C "$E2E_REPO_ROOT" rev-parse --verify --quiet "$REF^{commit}" >/dev/null \
  || e2e_die "Git-Ref $REF nicht gefunden (Tags holen: git fetch --tags)"
e2e_enter_slot "$0" "${ARGS[@]}"

e2e_new_secrets
e2e_verify_config
e2e_workdir
STATUS=1

finish() {
  local rc=$?
  [ "$STATUS" -eq 0 ] || rc=1
  if [ "$KEEP" -eq 1 ]; then
    e2e_log "Umgebung bleibt stehen (--keep): Backend $(e2e_backend_url || echo '?'); Seed-Daten: $E2E_WORKDIR/seed.json"
    e2e_log "Abbau: scripts/e2e/run-e2e.sh --down"
  else
    e2e_save_backend_log "$E2E_WORKDIR/backend-final.log"
    e2e_down
    e2e_log "Umgebung abgebaut (Images bleiben)"
  fi
  if [ "$rc" -eq 0 ] && [ "$KEEP" -eq 0 ]; then
    rm -rf "$E2E_WORKDIR"
  else
    e2e_log "Arbeitsordner (Logs, Seed, Report): $E2E_WORKDIR"
  fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT TERM

[ "$BUILD" -eq 1 ] && e2e_build_image
docker image inspect "$E2E_IMAGE" >/dev/null 2>&1 || e2e_die "Image $E2E_IMAGE fehlt (ohne --no-build starten)"

LEGACY="$E2E_WORKDIR/legacy"
mkdir -p "$LEGACY"
git -C "$E2E_REPO_ROOT" archive "$REF" VERSION backend | tar -x -C "$LEGACY"
[ -f "$LEGACY/backend/app/core/database.py" ] || e2e_die "git archive $REF lieferte keinen Backend-Code"
chmod -R a+rX "$LEGACY"
LEGACY_VERSION="$(tr -d '[:space:]' <"$LEGACY/VERSION")"
e2e_log "Altstand $REF (VERSION $LEGACY_VERSION) nach $LEGACY entpackt"

e2e_log "raeume Reste frueherer Laeufe ab"
e2e_down

e2e_log "starte db, pdns1, pdns2, receiver"
e2e_up db pdns1 pdns2 receiver || exit 1

e2e_log "Seed mit dem $REF-Code (init_db + Altdaten)"
e2e_compose --profile tools run --rm -T --no-deps \
  --user "$(id -u):$(id -g)" -v "$E2E_WORKDIR:/state" -v "$LEGACY:/legacy:ro" \
  -e PYTHONPATH=/legacy/backend \
  -e DATABASE_URL=mysql+aiomysql://dns_admin:e2e_db_pass@db:3306/dns_manager \
  -e JWT_SECRET_KEY="$E2E_JWT_SECRET" -e DB_POOL_SIZE=0 \
  tools python /e2e/seed_241.py --out /state/seed.json --expect-version "$LEGACY_VERSION" \
  || e2e_die "Seed fehlgeschlagen"
chmod 600 "$E2E_WORKDIR/seed.json"

e2e_log "starte Backend $E2E_IMAGE gegen die 2.4.1-Datenbank (erster Start = Migration)"
if ! e2e_up backend; then
  e2e_save_backend_log "$E2E_WORKDIR/backend-start.log"
  tail -n 80 "$E2E_WORKDIR/backend-start.log" >&2 || true
  exit 1
fi
e2e_save_backend_log "$E2E_WORKDIR/backend-upgrade.log"

ONLY_ARGS=()
[ -n "$ONLY" ] && ONLY_ARGS=(--only "$ONLY")
e2e_log "Check-Runner: upgrade ${ONLY_ARGS[*]:-}"
RC1=0
e2e_run_checks upgrade "${ONLY_ARGS[@]}" || RC1=$?

e2e_log "zweiter Start des Backends"
SINCE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
e2e_compose restart --timeout 15 backend >/dev/null
if ! e2e_up backend; then
  e2e_save_backend_log "$E2E_WORKDIR/backend-restart-failed.log"
  tail -n 80 "$E2E_WORKDIR/backend-restart-failed.log" >&2 || true
  exit 1
fi
docker logs --since "$SINCE" pdnsmgr-e2e-api >"$E2E_WORKDIR/backend-upgrade-restart.log" 2>&1 || true
chmod 600 "$E2E_WORKDIR/backend-upgrade-restart.log"

RC2=0
e2e_run_checks upgrade-restart "${ONLY_ARGS[@]}" || RC2=$?

if [ "$RC1" -eq 0 ] && [ "$RC2" -eq 0 ]; then
  STATUS=0
  e2e_log "E2E Upgrade $REF -> aktueller Stand: OK"
else
  e2e_log "E2E Upgrade: FEHLGESCHLAGEN (upgrade=$RC1, upgrade-restart=$RC2)"
fi
exit "$STATUS"
