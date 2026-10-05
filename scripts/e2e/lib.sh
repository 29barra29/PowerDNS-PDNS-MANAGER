# shellcheck shell=bash
# lib.sh – gemeinsame Funktionen fuer run-e2e.sh und upgrade-241-to-30.sh (wird per `source` geladen).
#
# Sicherheitsregeln (Produktion laeuft auf demselben Host):
# - docker compose NUR mit -p pdnsmgr-e2e und -f scripts/e2e/compose.e2e.yaml (Funktion e2e_compose);
#   die compose.yaml im Repo-Wurzelverzeichnis wird nie angefasst.
# - Vor jedem Start wird die aufgeloeste Konfiguration geprueft: alle Containernamen beginnen mit
#   "pdnsmgr-e2e-", das Netz heisst pdnsmgr-e2e-net, Host-Ports nur auf 127.0.0.1.
# - Ein Lauf zur Zeit (flock auf $PDNSMGR_SLOT_DIR/e2e.lock); schwere Schritte ueber with-slot.sh.

E2E_PROJECT="pdnsmgr-e2e"
E2E_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
E2E_REPO_ROOT="$(cd "$E2E_DIR/../.." && pwd)"
E2E_COMPOSE_FILE="$E2E_DIR/compose.e2e.yaml"
E2E_WITH_SLOT="$E2E_REPO_ROOT/scripts/dev/with-slot.sh"
E2E_IMAGE="${E2E_IMAGE:-pdnsmgr-e2e:local}"
export E2E_IMAGE

e2e_log() { printf '[e2e %s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
e2e_die() { e2e_log "FEHLER: $*"; exit 1; }

e2e_compose() {
  docker compose -p "$E2E_PROJECT" -f "$E2E_COMPOSE_FILE" "$@"
}

# Startet das aufrufende Skript erneut innerhalb eines Kapazitaets-Slots (scripts/dev/with-slot.sh),
# nachdem der E2E-Lock gehalten wird. Aufruf: e2e_enter_slot "$0" "$@"
e2e_enter_slot() {
  local self="$1"; shift
  if [ -z "${E2E_LOCK_HELD:-}" ]; then
    local lock_dir="${PDNSMGR_SLOT_DIR:-/tmp/pdnsmgr-slots}"
    mkdir -p "$lock_dir"
    exec {E2E_LOCK_FD}>>"$lock_dir/e2e.lock"
    if ! flock -n "$E2E_LOCK_FD"; then
      e2e_log "anderer E2E-Lauf aktiv – warte auf $lock_dir/e2e.lock ..."
      flock "$E2E_LOCK_FD"
    fi
    export E2E_LOCK_HELD=1
  fi
  if [ -z "${PDNSMGR_SLOT:-}" ]; then
    [ -x "$E2E_WITH_SLOT" ] || e2e_die "$E2E_WITH_SLOT fehlt"
    # Der Lock-Deskriptor wird vererbt und bleibt bis zum Ende des Laufs gehalten.
    exec "$E2E_WITH_SLOT" bash "$self" "$@"
  fi
}

e2e_require_tools() {
  command -v docker >/dev/null || e2e_die "docker fehlt"
  docker compose version >/dev/null 2>&1 || e2e_die "docker compose (v2) fehlt"
  command -v flock >/dev/null || e2e_die "flock fehlt (util-linux)"
}

# Prueft die aufgeloeste compose-Konfiguration gegen die Isolationsregeln.
e2e_verify_config() {
  local cfg
  cfg="$(E2E_JWT_SECRET="${E2E_JWT_SECRET:-x}" E2E_ADMIN_PASSWORD="${E2E_ADMIN_PASSWORD:-x}" \
         e2e_compose --profile tools config --format json)" || e2e_die "compose-Konfiguration ungueltig"
  printf '%s' "$cfg" | python3 -c '
import json, sys
cfg = json.load(sys.stdin)
errors = []
if cfg.get("name") != "pdnsmgr-e2e":
    errors.append("Projektname ist %r" % cfg.get("name"))
for name, svc in cfg.get("services", {}).items():
    cname = svc.get("container_name")
    if cname is not None and not cname.startswith("pdnsmgr-e2e-"):
        errors.append("Dienst %s: container_name %s" % (name, cname))
    if cname is None and name != "tools":
        errors.append("Dienst %s ohne container_name" % name)
    for p in svc.get("ports", []) or []:
        if p.get("host_ip") != "127.0.0.1":
            errors.append("Dienst %s: Port ohne 127.0.0.1-Bindung: %s" % (name, p))
        if p.get("published") not in (None, "", "0"):
            errors.append("Dienst %s: fester Host-Port %s" % (name, p.get("published")))
    for net in (svc.get("networks") or {}):
        if net != "e2e":
            errors.append("Dienst %s: fremdes Netz %s" % (name, net))
nets = cfg.get("networks", {})
if [n.get("name") for n in nets.values()] != ["pdnsmgr-e2e-net"]:
    errors.append("Netze: %s" % [n.get("name") for n in nets.values()])
if errors:
    print("\n".join(errors)); sys.exit(1)
' || e2e_die "compose.e2e.yaml verletzt die Isolationsregeln (siehe oben)"
}

# Baut das Backend-Image pdnsmgr-e2e:local aus dem aktuellen Checkout (Dockerfile des Repos).
e2e_build_image() {
  e2e_log "baue $E2E_IMAGE aus $E2E_REPO_ROOT ..."
  DOCKER_BUILDKIT=1 "$E2E_WITH_SLOT" docker build --pull=false -q -t "$E2E_IMAGE" \
    -f "$E2E_REPO_ROOT/backend/Dockerfile" "$E2E_REPO_ROOT" >/dev/null \
    || e2e_die "docker build fehlgeschlagen"
  docker image inspect "$E2E_IMAGE" >/dev/null 2>&1 || e2e_die "Image $E2E_IMAGE fehlt nach dem Build"
  docker run --rm -e JWT_SECRET_KEY=e2e-smoke --entrypoint python "$E2E_IMAGE" -c "import app.main" >/dev/null \
    || e2e_die "Image-Smoke 'import app.main' fehlgeschlagen"
  e2e_log "Image $E2E_IMAGE bereit"
}

# Zufaellige Laufzeit-Geheimnisse fuer das Backend (nur fuer diesen Lauf).
e2e_new_secrets() {
  E2E_JWT_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  E2E_ADMIN_PASSWORD="E2e-Admin-$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')"
  export E2E_JWT_SECRET E2E_ADMIN_PASSWORD
}

e2e_workdir() {
  if [ -n "${E2E_WORKDIR:-}" ]; then
    mkdir -p "$E2E_WORKDIR"
  else
    E2E_WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/pdnsmgr-e2e.XXXXXX")"
  fi
  chmod 700 "$E2E_WORKDIR"
  export E2E_WORKDIR
}

# Entfernt Container, Netz und Volumes des E2E-Projekts (Images bleiben erhalten).
# Auch im Modus --down nach einem --keep-Lauf: dort fehlen E2E_JWT_SECRET/E2E_ADMIN_PASSWORD; compose braucht sie
# nur fuer die Interpolation von compose.e2e.yaml – ohne Platzhalter scheiterte "down -v" still und die Volumes
# (u. a. backend_data mit .secret_key) blieben stehen.
e2e_down() {
  E2E_JWT_SECRET="${E2E_JWT_SECRET:-down}" E2E_ADMIN_PASSWORD="${E2E_ADMIN_PASSWORD:-down}" \
    e2e_compose --profile tools down -v --remove-orphans --timeout 10 >/dev/null 2>&1 || true
  # Reste ohne compose-Labels (abgebrochene Laeufe) gezielt nach Namen entfernen.
  local c v
  for c in $(docker ps -aq --filter "name=^pdnsmgr-e2e-" 2>/dev/null); do
    docker rm -f "$c" >/dev/null 2>&1 || true
  done
  docker network rm pdnsmgr-e2e-net >/dev/null 2>&1 || true
  # Volumes nur dieses Projekts (compose-Label), falls compose sie nicht erwischt hat.
  for v in $(docker volume ls -q --filter "label=com.docker.compose.project=$E2E_PROJECT" 2>/dev/null); do
    docker volume rm "$v" >/dev/null 2>&1 || e2e_log "Volume $v konnte nicht entfernt werden"
  done
}

# Startet die angegebenen Dienste und wartet auf "healthy".
e2e_up() {
  e2e_compose up -d --wait --wait-timeout "${E2E_WAIT_TIMEOUT:-240}" --quiet-pull "$@" \
    || { e2e_log "Start fehlgeschlagen: $*"; e2e_compose ps -a >&2 || true; return 1; }
}

# Schreibt das Backend-Log in den Arbeitsordner (fuer ctx.backend_log()).
e2e_save_backend_log() {
  local target="$1"
  docker logs pdnsmgr-e2e-api >"$target" 2>&1 || true
  chmod 600 "$target" 2>/dev/null || true
}

# Fuehrt den Check-Runner im Werkzeug-Container aus. Aufruf: e2e_run_checks <modus> [--only a,b]
e2e_run_checks() {
  e2e_compose --profile tools run --rm -T --no-deps \
    --user "$(id -u):$(id -g)" -v "$E2E_WORKDIR:/state" \
    tools python -c 'import sys, checks; sys.exit(checks.main())' "$@"
}

e2e_backend_url() {
  e2e_compose port backend 8000 2>/dev/null | sed 's/^/http:\/\//'
}
