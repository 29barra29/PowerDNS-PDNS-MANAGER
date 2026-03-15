#!/bin/bash
# DNS Manager - One-Click Installation Script
# This script downloads and sets up DNS Manager automatically

set -e

# Prevent execution via curl | bash (breaks interactive prompts)
if [ ! -t 0 ]; then
    echo -e "\033[0;31m✗ Fehler: Dieses Skript benötigt Benutzereingaben und darf nicht per Pipe (| bash) ausgeführt werden.\033[0m"
    echo -e "Bitte nutze stattdessen diesen Befehl:"
    echo -e "\033[1;33mcurl -sSLO https://raw.githubusercontent.com/29barra29/dns-manager/main/install.sh && bash install.sh\033[0m"
    exit 1
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Functions
print_success() {
    echo -e "${GREEN}✓${NC} $1"
}

print_error() {
    echo -e "${RED}✗${NC} $1"
}

print_info() {
    echo -e "${YELLOW}→${NC} $1"
}

# Banner
clear
echo "================================================"
echo "   🌐 DNS Manager - Automatische Installation"
echo "================================================"
echo ""

# Check prerequisites
print_info "Prüfe Voraussetzungen..."

# Check if running as root
if [ "$EUID" -eq 0 ]; then
   print_error "Bitte nicht als root ausführen!"
   exit 1
fi

# Check Docker
if ! command -v docker &> /dev/null; then
    print_error "Docker ist nicht installiert!"
    echo "Installiere Docker mit: curl -fsSL https://get.docker.com | sh"
    exit 1
fi
print_success "Docker gefunden"

# Check Docker Compose
if ! docker compose version &> /dev/null; then
    if ! command -v docker-compose &> /dev/null; then
        print_error "Docker Compose ist nicht installiert!"
        exit 1
    fi
    COMPOSE_CMD="docker-compose"
else
    COMPOSE_CMD="docker compose"
fi
print_success "Docker Compose gefunden"

# Check Docker permissions
if ! docker ps &> /dev/null; then
    if sudo docker ps &> /dev/null; then
        print_info "Docker erfordert root-Rechte. Verwende 'sudo'."
        COMPOSE_CMD="sudo $COMPOSE_CMD"
    else
        print_error "Dein Benutzer hat keine Rechte, um Docker auszuführen."
        print_info "Bitte führe 'sudo usermod -aG docker $USER' aus und logge dich neu ein."
        exit 1
    fi
else
    print_success "Docker Berechtigungen geprüft ok"
fi

# Check if port 5380 is free
if lsof -Pi :5380 -sTCP:LISTEN -t >/dev/null 2>&1; then
    print_error "Port 5380 ist bereits belegt!"
    read -p "Trotzdem fortfahren? (j/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Jj]$ ]]; then
        exit 1
    fi
fi

# Select installation directory
echo ""
read -p "Installationsverzeichnis [./dns-manager]: " INSTALL_DIR
INSTALL_DIR=${INSTALL_DIR:-./dns-manager}

# Create directory
if [ -d "$INSTALL_DIR" ]; then
    print_error "Verzeichnis $INSTALL_DIR existiert bereits!"
    read -p "Überschreiben? (j/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Jj]$ ]]; then
        rm -rf "$INSTALL_DIR"
    else
        exit 1
    fi
fi

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# Download repository
print_info "Lade DNS Manager herunter..."
git clone https://github.com/29barra29/dns-manager.git . 2>/dev/null || {
    # If git fails, try with curl/wget
    print_info "Git nicht verfügbar, versuche direkten Download..."

    if command -v curl &> /dev/null; then
        curl -L https://github.com/29barra29/dns-manager/archive/main.tar.gz | tar xz --strip-components=1
    elif command -v wget &> /dev/null; then
        wget -qO- https://github.com/29barra29/dns-manager/archive/main.tar.gz | tar xz --strip-components=1
    else
        print_error "Weder git, curl noch wget verfügbar!"
        exit 1
    fi
}
print_success "Download abgeschlossen"

# Run setup script
if [ -f "setup.sh" ]; then
    print_info "Starte Setup-Assistenten..."
    chmod +x setup.sh
    ./setup.sh --from-install
else
    print_info "Setup-Script nicht gefunden, erstelle Standard .env..."
    cp .env.example .env

    # Generate secure passwords
    sed -i "s/changeme-root/$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-25)/g" .env
    sed -i "s/changeme-password/$(openssl rand -base64 32 | tr -d '=+/' | cut -c1-25)/g" .env

    # Enable registration for first-run
    echo "ENABLE_REGISTRATION=true" >> .env

    print_success ".env erstellt mit sicheren Passwörtern"
fi

# Start containers
print_info "Starte Docker Container..."
$COMPOSE_CMD up -d

# Wait for services
print_info "Warte auf Services..."
sleep 10

# Check if services are running
if $COMPOSE_CMD ps | grep "dns-manager-api" | grep -qi "Up"; then
    print_success "Backend läuft"
else
    print_error "Backend startet nicht!"
    echo "Prüfe Logs mit: $COMPOSE_CMD logs backend"
fi

if $COMPOSE_CMD ps | grep "dns-manager-db" | grep -qi "Up"; then
    print_success "Datenbank läuft"
else
    print_error "Datenbank startet nicht!"
    echo "Prüfe Logs mit: $COMPOSE_CMD logs mariadb"
fi

# Final message
echo ""
echo "================================================"
echo "   ✅ Installation abgeschlossen!"
echo "================================================"
echo ""
echo "📌 DNS Manager ist erreichbar unter:"
echo "   http://localhost:5380"
echo ""
echo "📝 Nächste Schritte:"
echo "   1. Öffne http://localhost:5380 im Browser"
echo "   2. Registriere dich als erster Benutzer (wird Admin)"
echo "   3. Füge deine PowerDNS Server hinzu"
echo ""
echo "📚 Hilfreiche Befehle:"
echo "   Logs anzeigen:    $COMPOSE_CMD logs -f"
echo "   Status prüfen:    $COMPOSE_CMD ps"
echo "   Stoppen:          $COMPOSE_CMD down"
echo "   Update:           ./update.sh"
echo ""
echo "🔒 Sicherheit:"
echo "   - Ändere alle Standard-Passwörter"
echo "   - Aktiviere HTTPS mit Reverse Proxy"
echo "   - Siehe INSTALL.md für Details"
echo ""
echo "💡 Bei Problemen:"
echo "   GitHub Issues: https://github.com/29barra29/dns-manager/issues"
echo ""