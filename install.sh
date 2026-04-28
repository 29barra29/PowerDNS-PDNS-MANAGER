#!/bin/bash
# PDNS Manager - One-Click Installation Script
# This script downloads and sets up PDNS Manager automatically

set -e

# Prevent execution via curl | bash (breaks interactive prompts)
if [ ! -t 0 ]; then
    echo -e "\033[0;31m✗ Fehler: Dieses Skript benötigt Benutzereingaben und darf nicht per Pipe (| bash) ausgeführt werden.\033[0m"
    echo -e "Bitte nutze stattdessen: curl -sSLO https://.../install.sh && bash install.sh"
    echo -e "\033[0;31m✗ Error: This script needs user input; do not run via pipe (| bash).\033[0m"
    echo -e "Use instead: curl -sSLO https://.../install.sh && bash install.sh"
    exit 1
fi

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_success() { echo -e "${GREEN}✓${NC} $1"; }
print_error()   { echo -e "${RED}✗${NC} $1"; }
print_info()    { echo -e "${YELLOW}→${NC} $1"; }

# ---------- Language selection (first thing the user sees) ----------
clear
echo "================================================"
echo "   🌐 PDNS Manager - Installation"
echo "================================================"
echo ""
echo "Sprache / Language:"
echo "   [1] Deutsch"
echo "   [2] English"
echo ""
read -p "Choose [1/2] (default 1): " -n 1 -r LANG_CHOICE
echo ""
LANG_CHOICE=${LANG_CHOICE:-1}
if [ "$LANG_CHOICE" = "2" ]; then
    LANG_APP="en"
else
    LANG_APP="de"
fi

# ---------- Set all messages in chosen language ----------
if [ "$LANG_APP" = "en" ]; then
    M_BANNER_TITLE="PDNS Manager - Installation"
    M_CHECK_PREREQ="Checking prerequisites..."
    M_NO_ROOT="Do not run this installer as root. Use a regular user with sudo rights - Docker commands will use sudo automatically when needed."
    M_DOCKER_MISSING="Docker is not installed!"
    M_DOCKER_INSTALL="Install Docker with: curl -fsSL https://get.docker.com | sh"
    M_DOCKER_FOUND="Docker found"
    M_COMPOSE_MISSING="Docker Compose is not installed!"
    M_COMPOSE_FOUND="Docker Compose found"
    M_DOCKER_SUDO="Docker requires root. Using 'sudo'."
    M_DOCKER_NO_PERM="Your user cannot run Docker."
    M_DOCKER_ADD_USER="Run: sudo usermod -aG docker \$USER and log in again."
    M_DOCKER_OK="Docker permissions OK"
    M_PORT_IN_USE="Port 5380 is already in use!"
    M_CONTINUE_ANYWAY="Continue anyway? (y/n): "
    M_INSTALL_DIR="Installation directory [./pdns-manager]: "
    M_DIR_EXISTS="Directory %s already exists!"
    M_OVERWRITE="Overwrite? (y/n): "
    M_DOWNLOAD="Downloading PDNS Manager..."
    M_GIT_TRY="Git not available, trying direct download..."
    M_NEED_CURL_WGET="Neither git, curl nor wget available!"
    M_DOWNLOAD_DONE="Download complete"
    M_START_SETUP="Starting setup wizard..."
    M_NO_SETUP="Setup script not found, creating default .env..."
    M_ENV_CREATED=".env created with secure passwords"
    M_START_CONTAINERS="Starting Docker containers..."
    M_WAIT_SERVICES="Waiting for services..."
    M_BACKEND_UP="Backend is healthy"
    M_BACKEND_DOWN="Backend did not become healthy in time!"
    M_BACKEND_WAITING="Waiting for backend health endpoint..."
    M_CHECK_LOGS_BACKEND="Check logs: %s logs backend"
    M_DB_UP="Database is running"
    M_DB_DOWN="Database failed to start!"
    M_CHECK_LOGS_DB="Check logs: %s logs mariadb"
    M_OPENSSL_MISSING="openssl is required to generate secure passwords."
    M_PORT_CHECK_SKIPPED="No tool to probe ports (lsof/ss/netstat). Skipping port check."
    M_TARBALL_TAG="Downloaded release %s as tarball."
    M_TARBALL_MAIN="No release tag found - downloading main branch (unstable)."
    M_DONE_TITLE="Installation complete!"
    M_ACCESS="PDNS Manager is available at:"
    M_NEXT="Next steps:"
    M_NEXT_1="Open http://localhost:5380 in your browser"
    M_NEXT_2="Register as first user (becomes Admin)"
    M_NEXT_3="Add your PowerDNS servers"
    M_COMMANDS="Useful commands:"
    M_LOGS="View logs:"
    M_STATUS="Check status:"
    M_STOP="Stop:"
    M_UPDATE="Update:"
    M_SECURITY="Security:"
    M_SEC_1="Change all default passwords"
    M_SEC_2="Enable HTTPS (reverse proxy)"
    M_SEC_3="See INSTALL.md for details"
    M_HELP="Need help?"
else
    M_BANNER_TITLE="PDNS Manager - Automatische Installation"
    M_CHECK_PREREQ="Prüfe Voraussetzungen..."
    M_NO_ROOT="Bitte nicht als root ausführen. Nutze einen normalen Benutzer mit sudo-Rechten - Docker-Befehle verwenden sudo automatisch, wenn nötig."
    M_DOCKER_MISSING="Docker ist nicht installiert!"
    M_DOCKER_INSTALL="Installiere Docker mit: curl -fsSL https://get.docker.com | sh"
    M_DOCKER_FOUND="Docker gefunden"
    M_COMPOSE_MISSING="Docker Compose ist nicht installiert!"
    M_COMPOSE_FOUND="Docker Compose gefunden"
    M_DOCKER_SUDO="Docker erfordert root-Rechte. Verwende 'sudo'."
    M_DOCKER_NO_PERM="Dein Benutzer hat keine Rechte, um Docker auszuführen."
    M_DOCKER_ADD_USER="Bitte: sudo usermod -aG docker \$USER und neu einloggen."
    M_DOCKER_OK="Docker Berechtigungen geprüft ok"
    M_PORT_IN_USE="Port 5380 ist bereits belegt!"
    M_CONTINUE_ANYWAY="Trotzdem fortfahren? (j/n): "
    M_INSTALL_DIR="Installationsverzeichnis [./pdns-manager]: "
    M_DIR_EXISTS="Verzeichnis %s existiert bereits!"
    M_OVERWRITE="Überschreiben? (j/n): "
    M_DOWNLOAD="Lade PDNS Manager herunter..."
    M_GIT_TRY="Git nicht verfügbar, versuche direkten Download..."
    M_NEED_CURL_WGET="Weder git, curl noch wget verfügbar!"
    M_DOWNLOAD_DONE="Download abgeschlossen"
    M_START_SETUP="Starte Setup-Assistenten..."
    M_NO_SETUP="Setup-Script nicht gefunden, erstelle Standard .env..."
    M_ENV_CREATED=".env erstellt mit sicheren Passwörtern"
    M_START_CONTAINERS="Starte Docker Container..."
    M_WAIT_SERVICES="Warte auf Services..."
    M_BACKEND_UP="Backend ist healthy"
    M_BACKEND_DOWN="Backend wurde nicht rechtzeitig healthy!"
    M_BACKEND_WAITING="Warte auf Backend-Health-Endpoint..."
    M_CHECK_LOGS_BACKEND="Prüfe Logs mit: %s logs backend"
    M_DB_UP="Datenbank läuft"
    M_DB_DOWN="Datenbank startet nicht!"
    M_CHECK_LOGS_DB="Prüfe Logs mit: %s logs mariadb"
    M_OPENSSL_MISSING="openssl wird benötigt, um sichere Passwörter zu erzeugen."
    M_PORT_CHECK_SKIPPED="Kein Tool zur Port-Prüfung gefunden (lsof/ss/netstat). Port-Check übersprungen."
    M_TARBALL_TAG="Release %s als Tarball geladen."
    M_TARBALL_MAIN="Kein Release-Tag gefunden – nutze main-Branch (instabil)."
    M_DONE_TITLE="Installation abgeschlossen!"
    M_ACCESS="PDNS Manager ist erreichbar unter:"
    M_NEXT="Nächste Schritte:"
    M_NEXT_1="Öffne http://localhost:5380 im Browser"
    M_NEXT_2="Registriere dich als erster Benutzer (wird Admin)"
    M_NEXT_3="Füge deine PowerDNS Server hinzu"
    M_COMMANDS="Hilfreiche Befehle:"
    M_LOGS="Logs anzeigen:"
    M_STATUS="Status prüfen:"
    M_STOP="Stoppen:"
    M_UPDATE="Update:"
    M_SECURITY="Sicherheit:"
    M_SEC_1="Ändere alle Standard-Passwörter"
    M_SEC_2="Aktiviere HTTPS mit Reverse Proxy"
    M_SEC_3="Siehe INSTALL.md für Details"
    M_HELP="Bei Problemen:"
fi

# Yes/no prompt pattern (j/n for German, y/n for English)
if [ "$LANG_APP" = "en" ]; then
    YES_PATTERN='^[Yy]$'
else
    YES_PATTERN='^[JjYy]$'
fi

# ---------- Main script ----------
clear
echo "================================================"
echo "   🌐 $M_BANNER_TITLE"
echo "================================================"
echo ""

print_info "$M_CHECK_PREREQ"

if [ "$EUID" -eq 0 ]; then
   print_error "$M_NO_ROOT"
   exit 1
fi

if ! command -v docker &> /dev/null; then
    print_error "$M_DOCKER_MISSING"
    echo "$M_DOCKER_INSTALL"
    exit 1
fi
print_success "$M_DOCKER_FOUND"

if ! docker compose version &> /dev/null 2>&1; then
    if ! command -v docker-compose &> /dev/null; then
        print_error "$M_COMPOSE_MISSING"
        exit 1
    fi
    COMPOSE_CMD="docker-compose"
else
    COMPOSE_CMD="docker compose"
fi
print_success "$M_COMPOSE_FOUND"

if ! docker ps &> /dev/null; then
    if sudo docker ps &> /dev/null 2>&1; then
        print_info "$M_DOCKER_SUDO"
        COMPOSE_CMD="sudo $COMPOSE_CMD"
    else
        print_error "$M_DOCKER_NO_PERM"
        print_info "$M_DOCKER_ADD_USER"
        exit 1
    fi
else
    print_success "$M_DOCKER_OK"
fi

# Port-Check mit Fallback (lsof -> ss -> netstat). Returncode:
#   0 = belegt, 1 = frei, 2 = kein Tool verfügbar
port_in_use() {
    local port="$1"
    if command -v lsof &>/dev/null; then
        lsof -Pi :"$port" -sTCP:LISTEN -t >/dev/null 2>&1 && return 0 || return 1
    elif command -v ss &>/dev/null; then
        ss -ltnH 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}\$" && return 0 || return 1
    elif command -v netstat &>/dev/null; then
        netstat -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}\$" && return 0 || return 1
    fi
    return 2
}

PORT_CHECK_RC=0
port_in_use 5380 || PORT_CHECK_RC=$?
if [ "$PORT_CHECK_RC" = "0" ]; then
    print_error "$M_PORT_IN_USE"
    read -p "$M_CONTINUE_ANYWAY" -n 1 -r
    echo
    if [[ ! $REPLY =~ $YES_PATTERN ]]; then
        exit 1
    fi
elif [ "$PORT_CHECK_RC" = "2" ]; then
    print_info "$M_PORT_CHECK_SKIPPED"
fi

echo ""
read -p "$M_INSTALL_DIR" INSTALL_DIR
INSTALL_DIR=${INSTALL_DIR:-./pdns-manager}

if [ -d "$INSTALL_DIR" ]; then
    print_error "$(printf "$M_DIR_EXISTS" "$INSTALL_DIR")"
    read -p "$M_OVERWRITE" -n 1 -r
    echo
    if [[ $REPLY =~ $YES_PATTERN ]]; then
        rm -rf "$INSTALL_DIR"
    else
        exit 1
    fi
fi

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"
INSTALL_PATH_ABS=$(pwd)

print_info "$M_DOWNLOAD"
GH_REPO="29barra29/PowerDNS-PDNS-MANAGER"
git clone "https://github.com/${GH_REPO}.git" . 2>/dev/null || {
    print_info "$M_GIT_TRY"

    # Versuche zuerst, das neueste Release-Tag von der GitHub-API zu holen.
    # Wenn das klappt, liefern wir den Tarball dieses Release aus -> stabiler Stand.
    LATEST_TAG_FROM_API=""
    if command -v curl &> /dev/null; then
        LATEST_TAG_FROM_API=$(curl -fsSL "https://api.github.com/repos/${GH_REPO}/releases/latest" 2>/dev/null \
            | sed -nE 's/.*"tag_name": *"([^"]+)".*/\1/p' | head -1 || true)
    fi

    if [ -n "$LATEST_TAG_FROM_API" ]; then
        TARBALL_URL="https://github.com/${GH_REPO}/archive/refs/tags/${LATEST_TAG_FROM_API}.tar.gz"
        print_info "$(printf "$M_TARBALL_TAG" "$LATEST_TAG_FROM_API")"
    else
        TARBALL_URL="https://github.com/${GH_REPO}/archive/main.tar.gz"
        print_info "$M_TARBALL_MAIN"
    fi

    if command -v curl &> /dev/null; then
        curl -fL "$TARBALL_URL" | tar xz --strip-components=1
    elif command -v wget &> /dev/null; then
        wget -qO- "$TARBALL_URL" | tar xz --strip-components=1
    else
        print_error "$M_NEED_CURL_WGET"
        exit 1
    fi
}
print_success "$M_DOWNLOAD_DONE"

# Neuestes v*-Tag verwenden (entspricht GitHub-Release), falls vorhanden – vermeidet „alte“ main ohne aktuelle VERSION
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git fetch --tags --force 2>/dev/null || true
    LATEST_TAG=$(git tag -l 'v*' --sort=-v:refname 2>/dev/null | head -1)
    if [ -n "$LATEST_TAG" ] && git rev-parse "$LATEST_TAG" >/dev/null 2>&1; then
        if git checkout "$LATEST_TAG" 2>/dev/null; then
            print_info "Release $LATEST_TAG"
        fi
    fi
fi

if [ -f "setup.sh" ]; then
    print_info "$M_START_SETUP"
    chmod +x setup.sh
    ./setup.sh --from-install "$LANG_APP"
else
    # Fallback: setup.sh nicht vorhanden -> minimale, aber sichere .env selbst erzeugen.
    # Achtung: Niemals auf vorhandene Platzhalter in .env.example verlassen
    # (Namen ändern sich) -> wir setzen jede Variable zeilenweise.
    print_info "$M_NO_SETUP"

    if ! command -v openssl &> /dev/null; then
        print_error "$M_OPENSSL_MISSING"
        exit 1
    fi

    DB_ROOT_PW=$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-32)
    DB_PW=$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-32)
    JWT_SECRET=$(openssl rand -hex 64)

    # Variable zeilenweise setzen (insert oder update).
    set_env() {
        local key="$1" val="$2"
        if grep -q "^${key}=" .env 2>/dev/null; then
            sed -i.bak "s|^${key}=.*|${key}=${val}|" .env
        else
            echo "${key}=${val}" >> .env
        fi
    }

    cp .env.example .env 2>/dev/null || touch .env

    set_env DB_ROOT_PASSWORD     "$DB_ROOT_PW"
    set_env DB_PASSWORD          "$DB_PW"
    set_env JWT_SECRET_KEY       "$JWT_SECRET"
    set_env ENABLE_REGISTRATION  "true"
    set_env DEFAULT_LANGUAGE     "$LANG_APP"
    set_env INSTALL_PATH         "$INSTALL_PATH_ABS"
    set_env AUTH_COOKIE_SECURE   "false"
    set_env AUTH_COOKIE_SAMESITE "lax"
    set_env DOCS_ENABLED         "false"

    rm -f .env.bak 2>/dev/null || true
    chmod 600 .env 2>/dev/null || true
    print_success "$M_ENV_CREATED"
fi

print_info "$M_START_CONTAINERS"
$COMPOSE_CMD up -d

# 1) Datenbank: Compose-Service läuft?
#    Wir nutzen `compose ps -q` weil das nicht von einem festen Container-Namen
#    abhängt. Wenn die ID leer ist, ist der Service nicht da.
print_info "$M_WAIT_SERVICES"
DB_CID=$($COMPOSE_CMD ps -q mariadb 2>/dev/null || true)
if [ -n "$DB_CID" ] && docker inspect "$DB_CID" --format '{{.State.Status}}' 2>/dev/null | grep -qi "running"; then
    print_success "$M_DB_UP"
else
    print_error "$M_DB_DOWN"
    echo "$(printf "$M_CHECK_LOGS_DB" "$COMPOSE_CMD")"
fi

# 2) Backend: aktiv auf /health pollen statt blind 10 s schlafen.
#    Damit erfährt der User wirklich, ob das Backend bereit ist (DB-Init,
#    Migrations etc. können einen Moment dauern).
print_info "$M_BACKEND_WAITING"
HEALTH_OK=false
for _i in $(seq 1 60); do
    if command -v curl &> /dev/null; then
        if curl -fsS "http://localhost:5380/health" >/dev/null 2>&1; then
            HEALTH_OK=true; break
        fi
    elif command -v wget &> /dev/null; then
        if wget -qO- "http://localhost:5380/health" >/dev/null 2>&1; then
            HEALTH_OK=true; break
        fi
    else
        # Kein HTTP-Tool -> Fallback auf Container-Status
        BE_CID=$($COMPOSE_CMD ps -q backend 2>/dev/null || true)
        if [ -n "$BE_CID" ] && docker inspect "$BE_CID" --format '{{.State.Status}}' 2>/dev/null | grep -qi "running"; then
            HEALTH_OK=true; break
        fi
    fi
    sleep 2
done

if $HEALTH_OK; then
    print_success "$M_BACKEND_UP"
else
    print_error "$M_BACKEND_DOWN"
    echo "$(printf "$M_CHECK_LOGS_BACKEND" "$COMPOSE_CMD")"
fi

echo ""
echo "================================================"
echo "   ✅ $M_DONE_TITLE"
echo "================================================"
echo ""
echo "📌 $M_ACCESS"
echo "   http://localhost:5380"
echo ""
echo "📝 $M_NEXT"
echo "   1. $M_NEXT_1"
echo "   2. $M_NEXT_2"
echo "   3. $M_NEXT_3"
echo ""
echo "📚 $M_COMMANDS"
echo "   $M_LOGS    $COMPOSE_CMD logs -f"
echo "   $M_STATUS $COMPOSE_CMD ps"
echo "   $M_STOP   $COMPOSE_CMD down"
echo "   $M_UPDATE ./update.sh"
echo ""
echo "🔒 $M_SECURITY"
echo "   - $M_SEC_1"
echo "   - $M_SEC_2"
echo "   - $M_SEC_3"
echo ""
echo "💡 $M_HELP"
echo "   https://github.com/29barra29/PowerDNS-PDNS-MANAGER/issues"
echo ""
