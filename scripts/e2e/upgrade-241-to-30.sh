#!/usr/bin/env bash
# upgrade-241-to-30.sh – E2E-Test "Upgrade": 2.4.1-Datenbank mit Altdaten -> aktueller Stand (3.0).
#
# Ablauf:
#   1. Image pdnsmgr-e2e:local aus dem aktuellen Checkout bauen (Default; --no-build ueberspringt)
#   2. `git archive <ref>` (Default v2.4.1) -> Quellstand 2.4.1 in den Arbeitsordner
#   3. db, pdns1, pdns2, receiver starten (Projekt pdnsmgr-e2e, compose.e2e.yaml)
#   4. seed_241.py mit dem 2.4.1-Code: init_db (2.4.1-Schema) + Altdaten (TOTP-Nutzerin, Server mit
#      Klartext-Key, Webhooks, Panel-Tokens aktiv/inaktiv, Zonenrechte, Audit v1, SMTP/Captcha-Secret)
#   5. Backend des aktuellen Stands starten (Migration laeuft beim Start); Schluesselsicherung wie
#      ./update.sh (update.sh --backup-key-only in einem Stack-Ordner im Arbeitsordner, Ziel
#      PDNSMGR_KEY_BACKUP_DIR=<arbeitsordner>/keys) und lokales /health sichern -> check_upgrade(ctx) aller
#      scripts/e2e/checks/*.py (base zuerst)
#   6. Backend neu starten (zweiter Start) -> check_upgrade_restart(ctx), wo vorhanden
#   7. optional (--with-downgrade, Bauplan A.8): Token mit abgelaufenem expires_at anlegen ->
#      "python -m app.cli.secrets prepare-downgrade --yes" im 3.0-Container -> 2.4.1-Code (git archive REF)
#      startet gegen die DB (sieht Klartext, abgelaufener Token is_active=0, Marker fehlt) -> erneuter
#      3.0-Start migriert ihn als widerrufen (Pruefungen: checks/f5.py downgrade_main)
#   8. Abbau (down -v; Images bleiben)
# Laeuft in einem Kapazitaets-Slot (scripts/dev/with-slot.sh) und haelt den E2E-Lock.
#
# Aufruf:
#   scripts/e2e/upgrade-241-to-30.sh [--no-build] [--keep] [--ref v2.4.1] [--only base,f5] [--with-downgrade]
#
#   --ref REF          Git-Ref des Altstands (Default v2.4.1; muss im Repo vorhanden sein)
#   --with-downgrade   zusaetzlich Downgrade auf REF und erneutes Upgrade pruefen (Schritt 7)
#   --no-build / --keep / --only wie bei run-e2e.sh; Abbau nach --keep: scripts/e2e/run-e2e.sh --down
#
# Hinweis: Der 2.4.1-Code laeuft fuer init_db und Seed im aktuellen Image (gleiche bzw. neuere
# Paketversionen, Python 3.12) – nicht in einem echten 2.4.1-Image. Schema, Hashes und Datenformate
# stammen trotzdem vollstaendig aus dem 2.4.1-Code.
set -euo pipefail

# shellcheck source=scripts/e2e/lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

BUILD=1 KEEP=0 ONLY="" REF="v2.4.1" DOWNGRADE=0
ARGS=("$@")
while [ "$#" -gt 0 ]; do
  case "$1" in
    --no-build) BUILD=0 ;;
    --keep) KEEP=1 ;;
    --ref) shift; REF="${1:-}"; [ -n "$REF" ] || { echo "--ref braucht einen Wert" >&2; exit 2; } ;;
    --ref=*) REF="${1#--ref=}" ;;
    --only) shift; ONLY="${1:-}"; [ -n "$ONLY" ] || { echo "--only braucht eine Liste" >&2; exit 2; } ;;
    --only=*) ONLY="${1#--only=}" ;;
    --with-downgrade) DOWNGRADE=1 ;;
    -h|--help) sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
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

# Lokales /health des Backends (Zusatzfelder nur ueber Loopback: secrets, migration_errors, ...)
# fuer checks/*.py als /state/health-local-<name>.json ablegen.
save_local_health() {
  local name="$1" target="$E2E_WORKDIR/health-local-$1.json"
  docker exec "${2:-pdnsmgr-e2e-api}" python -c "import sys,urllib.request;sys.stdout.write(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=10).read().decode())" \
    >"$target" 2>/dev/null || { e2e_log "lokales /health ($name) nicht abrufbar"; rm -f "$target"; return 1; }
  chmod 600 "$target"
}

# Schluesselsicherung wie nach einem echten ./update.sh: Stack-Ordner im Arbeitsordner (leere .env, also
# kein SECRET_ENCRYPTION_KEY), Kopie nach $E2E_WORKDIR/keys. docker compose spricht ueber
# COMPOSE_PROJECT_NAME/COMPOSE_FILE das E2E-Projekt an – nie die compose.yaml des Repos.
backup_key_like_update() {
  mkdir -p "$E2E_WORKDIR/stack"
  : >"$E2E_WORKDIR/stack/.env"
  ( cd "$E2E_WORKDIR/stack" \
    && COMPOSE_PROJECT_NAME="$E2E_PROJECT" COMPOSE_FILE="$E2E_COMPOSE_FILE" \
       PDNSMGR_KEY_BACKUP_DIR="$E2E_WORKDIR/keys" bash "$E2E_REPO_ROOT/update.sh" --backup-key-only ) \
    >"$E2E_WORKDIR/update-backup-key.log" 2>&1
}

e2e_log "Schluesselsicherung wie ./update.sh (--backup-key-only)"
backup_key_like_update || { e2e_log "update.sh --backup-key-only fehlgeschlagen:"; cat "$E2E_WORKDIR/update-backup-key.log" >&2; }
save_local_health upgrade || true

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
save_local_health upgrade-restart || true

RC2=0
e2e_run_checks upgrade-restart "${ONLY_ARGS[@]}" || RC2=$?

# ---------------------------------------------------------------------------------------------
# Optional: Downgrade auf $REF und erneutes Upgrade (Bauplan A.8 [D6])
# ---------------------------------------------------------------------------------------------
f5_downgrade_step() {
  e2e_compose --profile tools run --rm -T --no-deps \
    --user "$(id -u):$(id -g)" -v "$E2E_WORKDIR:/state" -e E2E_LEGACY_URL="http://pdnsmgr-e2e-legacy:8000" \
    tools python -c 'import sys, checks.f5 as f5; sys.exit(f5.downgrade_main(sys.argv[1:]))' "$1"
}

wait_container_health() {
  local name="$1" _i
  for _i in $(seq 1 60); do
    if docker exec "$name" python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).read()" >/dev/null 2>&1; then
      return 0
    fi
    docker inspect -f '{{.State.Running}}' "$name" 2>/dev/null | grep -q true || return 1
    sleep 2
  done
  return 1
}

run_downgrade() {
  e2e_log "Downgrade-Test: Tokens anlegen (3.0 laeuft)"
  f5_downgrade_step prepare || return 1

  e2e_log "Downgrade-Test: Backend stoppen, prepare-downgrade --yes im 3.0-Container"
  e2e_compose stop --timeout 15 backend >/dev/null || return 1
  e2e_compose run --rm --no-deps -T --name pdnsmgr-e2e-cli backend \
    python -m app.cli.secrets prepare-downgrade --yes >"$E2E_WORKDIR/prepare-downgrade.log" 2>&1 \
    || { e2e_log "prepare-downgrade fehlgeschlagen:"; cat "$E2E_WORKDIR/prepare-downgrade.log" >&2; return 1; }
  chmod 600 "$E2E_WORKDIR/prepare-downgrade.log"

  e2e_log "Downgrade-Test: $REF-Code startet gegen die vorbereitete DB"
  e2e_compose run -d --no-deps --name pdnsmgr-e2e-legacy -v "$LEGACY:/legacy:ro" backend \
    sh -c 'cd /legacy/backend && exec uvicorn app.main:app --host 0.0.0.0 --port 8000' >/dev/null || return 1
  if ! wait_container_health pdnsmgr-e2e-legacy; then
    docker logs pdnsmgr-e2e-legacy >"$E2E_WORKDIR/backend-legacy.log" 2>&1 || true
    e2e_log "$REF-Backend nicht bereit:"; tail -n 60 "$E2E_WORKDIR/backend-legacy.log" >&2 || true
    return 1
  fi
  local rc=0
  f5_downgrade_step legacy || rc=1
  docker logs pdnsmgr-e2e-legacy >"$E2E_WORKDIR/backend-legacy.log" 2>&1 || true
  chmod 600 "$E2E_WORKDIR/backend-legacy.log" 2>/dev/null || true
  docker rm -f pdnsmgr-e2e-legacy >/dev/null 2>&1 || true
  [ "$rc" -eq 0 ] || return 1

  e2e_log "Downgrade-Test: erneuter Start des aktuellen Stands (Re-Upgrade)"
  local since
  since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if ! e2e_up backend; then
    e2e_save_backend_log "$E2E_WORKDIR/backend-reupgrade-failed.log"
    tail -n 80 "$E2E_WORKDIR/backend-reupgrade-failed.log" >&2 || true
    return 1
  fi
  docker logs --since "$since" pdnsmgr-e2e-api >"$E2E_WORKDIR/backend-reupgrade.log" 2>&1 || true
  chmod 600 "$E2E_WORKDIR/backend-reupgrade.log"
  save_local_health reupgrade || true
  f5_downgrade_step reupgrade
}

RC3=0
if [ "$DOWNGRADE" -eq 1 ]; then
  run_downgrade || RC3=1
fi

if [ "$RC1" -eq 0 ] && [ "$RC2" -eq 0 ] && [ "$RC3" -eq 0 ]; then
  STATUS=0
  e2e_log "E2E Upgrade $REF -> aktueller Stand: OK$([ "$DOWNGRADE" -eq 1 ] && echo ' (inkl. Downgrade/Re-Upgrade)')"
else
  e2e_log "E2E Upgrade: FEHLGESCHLAGEN (upgrade=$RC1, upgrade-restart=$RC2, downgrade=$RC3)"
fi
exit "$STATUS"
