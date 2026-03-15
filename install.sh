#!/bin/bash

# Farben für Output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}=================================================${NC}"
echo -e "${BLUE}       DNS Manager - One-Click Installer         ${NC}"
echo -e "${BLUE}=================================================${NC}"
echo ""

# Überprüfen ob Docker installiert ist
if ! command -v docker &> /dev/null; then
    echo -e "${RED}Fehler: Docker ist nicht installiert. Bitte installiere Docker zuerst.${NC}"
    echo "Website: https://docs.docker.com/get-docker/"
    exit 1
fi

# Zielordner bestimmen
INSTALL_DIR="dns-manager"

if [ -d "$INSTALL_DIR" ]; then
    echo -e "${RED}Fehler: Der Ordner '$INSTALL_DIR' existiert bereits in diesem Verzeichnis.${NC}"
    echo "Bitte wechsle in ein anderes Verzeichnis oder lösche den alten Ordner."
    exit 1
fi

echo -e "${GREEN}1. Lade DNS Manager von GitHub herunter...${NC}"
git clone https://github.com/29barra29/dns-manager.git $INSTALL_DIR || { echo -e "${RED}Fehler beim Herunterladen.${NC}"; exit 1; }

cd $INSTALL_DIR

echo -e "${GREEN}2. Erstelle Konfigurationsdateien...${NC}"
if [ ! -f .env ]; then
    cp .env.example .env
    # Generiere ein zufälliges Datenbank-Passwort für die Sicherheit
    MARIADB_PW=$(openssl rand -hex 16)
    sed -i "s/DB_PASSWORD=.*/DB_PASSWORD=$MARIADB_PW/" .env
    sed -i "s/DB_ROOT_PASSWORD=.*/DB_ROOT_PASSWORD=$(openssl rand -hex 16)/" .env
    echo "Sichere Datenbank-Passwörter wurden in .env generiert."
fi

echo -e "${GREEN}3. Starte das System (Docker baut jetzt das Image, das kann ein paar Minuten dauern)...${NC}"
# Stelle sicher, dass das Update-Skript ausführbar ist
chmod +x update.sh

# Starte Docker
docker compose up -d --build

echo ""
echo -e "${BLUE}=================================================${NC}"
echo -e "${GREEN}🎉 Installation erfolgreich abgeschlossen!${NC}"
echo ""
echo -e "Die Container laufen nun im Hintergrund."
echo -e "Öffne einfach deinen Browser und gehe auf:"
echo -e "${BLUE}👉 http://localhost:5380 (oder IP-deines-Servers:5380)${NC}"
echo ""
echo -e "Dort startet das Setup-Menü (Setup-Wizard)."
echo -e "Du kannst die Passwörter nachträglich in der Datei ${INSTALL_DIR}/.env ändern."
echo -e "${BLUE}=================================================${NC}"