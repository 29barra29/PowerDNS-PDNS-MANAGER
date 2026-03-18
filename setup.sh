#!/bin/bash

# DNS Manager - Interaktives Setup Script
# Dieses Script hilft bei der Ersteinrichtung

set -e

echo "================================================"
echo "   🌐 DNS Manager - Setup Assistent"
echo "================================================"
echo ""

# Funktion für sichere Passwort-Generierung
generate_password() {
    openssl rand -base64 32 | tr -d "=+/" | cut -c1-25
}

generate_secret() {
    openssl rand -hex 32
}

# Prüfe ob .env bereits existiert
if [ -f .env ]; then
    echo "⚠️  Eine .env Datei existiert bereits!"
    read -p "Möchtest du sie überschreiben? (j/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Jj]$ ]]; then
        echo "Setup abgebrochen."
        exit 1
    fi
    cp .env .env.backup
    echo "✅ Backup erstellt: .env.backup"
fi

echo ""
echo "📋 Basis-Konfiguration"
echo "----------------------"

# App Name
echo "Wie soll das Panel heißen? (Dieser Name wird später oben links im Panel angezeigt)"
read -p "Eingabe (z.B. Firmenname oder Eigenname) [DNS Manager]: " APP_NAME
APP_NAME=${APP_NAME:-DNS Manager}

# Installation Mode
echo ""
echo "Wie möchtest du den Admin-Account erstellen?"
echo "1) Automatisch (generiertes Passwort)"
echo "2) Registrierung beim ersten Besuch (empfohlen)"
echo "3) Jetzt festlegen"
read -p "Wähle [1-3]: " ADMIN_MODE

ENABLE_REGISTRATION="false"
ADMIN_PASSWORD=""

case $ADMIN_MODE in
    1)
        ADMIN_PASSWORD=$(generate_password)
        echo "✅ Admin-Passwort wird generiert"
        ;;
    2)
        ENABLE_REGISTRATION="true"
        echo "✅ Registrierung wird aktiviert"
        ;;
    3)
        while true; do
            read -sp "Admin-Passwort eingeben: " ADMIN_PASSWORD
            echo
            read -sp "Passwort wiederholen: " ADMIN_PASSWORD_CONFIRM
            echo
            if [ "$ADMIN_PASSWORD" = "$ADMIN_PASSWORD_CONFIRM" ]; then
                if [ ${#ADMIN_PASSWORD} -lt 8 ]; then
                    echo "❌ Passwort muss mindestens 8 Zeichen haben!"
                else
                    break
                fi
            else
                echo "❌ Passwörter stimmen nicht überein!"
            fi
        done
        ;;
esac

echo ""
echo "📧 E-Mail Konfiguration (optional)"
echo "-----------------------------------"
echo "Für Benachrichtigungen und Passwort-Reset"
echo ""

read -p "E-Mail aktivieren? (j/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Jj]$ ]]; then
    read -p "SMTP Server [smtp.gmail.com]: " SMTP_HOST
    SMTP_HOST=${SMTP_HOST:-smtp.gmail.com}

    read -p "SMTP Port [587]: " SMTP_PORT
    SMTP_PORT=${SMTP_PORT:-587}

    read -p "SMTP Benutzer (E-Mail): " SMTP_USER

    read -sp "SMTP Passwort: " SMTP_PASSWORD
    echo

    read -p "Absender E-Mail [$SMTP_USER]: " SMTP_FROM
    SMTP_FROM=${SMTP_FROM:-$SMTP_USER}

    MAIL_ENABLED="true"
else
    MAIL_ENABLED="false"
    SMTP_HOST=""
    SMTP_PORT=""
    SMTP_USER=""
    SMTP_PASSWORD=""
    SMTP_FROM=""
fi

echo ""
echo "🔐 Sicherheit"
echo "-------------"

# Generiere automatisch sichere Werte
echo "Generiere sichere Schlüssel..."
JWT_SECRET=$(generate_secret)
DB_ROOT_PASSWORD=$(generate_password)
DB_PASSWORD=$(generate_password)

echo "✅ JWT Secret generiert (${#JWT_SECRET} Zeichen)"
echo "✅ Datenbank-Passwörter generiert"

echo ""
echo "🔌 PowerDNS Server (optional)"
echo "-----------------------------"
echo "Du kannst PowerDNS Server auch später über das Web-Interface hinzufügen."
echo ""

read -p "Möchtest du jetzt einen PowerDNS Server konfigurieren? (j/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Jj]$ ]]; then
    read -p "Server Name [server1]: " PDNS_NAME
    PDNS_NAME=${PDNS_NAME:-server1}

    read -p "Server URL [http://localhost:8081]: " PDNS_URL
    PDNS_URL=${PDNS_URL:-http://localhost:8081}

    read -sp "API Key: " PDNS_API_KEY
    echo

    PDNS_SERVERS="${PDNS_NAME}|${PDNS_URL}|${PDNS_API_KEY}"
else
    PDNS_SERVERS=""
fi

echo ""
echo "📝 Erstelle .env Datei..."

cat > .env << EOF
# ====================================
# DNS Manager - Konfiguration
# Generiert: $(date)
# ====================================

# App Settings (Version aus VERSION-Datei im Projektroot)
APP_NAME=${APP_NAME}
APP_VERSION=$(cat "$(dirname "$0")/VERSION" 2>/dev/null | head -1 || echo "2.2.1")
LOG_LEVEL=info

# Sicherheit
JWT_SECRET_KEY=${JWT_SECRET}
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# First-Run Settings
ENABLE_REGISTRATION=${ENABLE_REGISTRATION}
${ADMIN_PASSWORD:+INITIAL_ADMIN_PASSWORD=${ADMIN_PASSWORD}}

# Datenbank
DB_ROOT_PASSWORD=${DB_ROOT_PASSWORD}
DB_NAME=dns_manager
DB_USER=dns_admin
DB_PASSWORD=${DB_PASSWORD}

# E-Mail (optional)
MAIL_ENABLED=${MAIL_ENABLED}
${SMTP_HOST:+SMTP_HOST=${SMTP_HOST}}
${SMTP_PORT:+SMTP_PORT=${SMTP_PORT}}
${SMTP_USER:+SMTP_USER=${SMTP_USER}}
${SMTP_PASSWORD:+SMTP_PASSWORD=${SMTP_PASSWORD}}
${SMTP_FROM:+SMTP_FROM=${SMTP_FROM}}

# PowerDNS Server (optional)
PDNS_SERVERS=${PDNS_SERVERS}
EOF

echo "✅ .env Datei erstellt!"

echo ""
echo "================================================"
echo "   ✅ Setup abgeschlossen!"
echo "================================================"
echo ""

if [ "$ADMIN_MODE" = "1" ]; then
    echo "⚠️  WICHTIG: Admin-Zugangsdaten"
    echo "   Benutzername: admin"
    echo "   Passwort: ${ADMIN_PASSWORD}"
    echo ""
    echo "   BITTE NOTIEREN! Das Passwort wird nur jetzt angezeigt."
elif [ "$ENABLE_REGISTRATION" = "true" ]; then
    echo "📝 Registrierung aktiviert!"
    echo "   Der erste Benutzer, der sich registriert, wird automatisch Admin."
fi

if [ "$1" != "--from-install" ]; then
    echo ""
    echo "🚀 Nächste Schritte:"
    echo "   1. Falls nicht gestartet: cd $(basename $(pwd)) && docker compose up -d"
    echo "   2. Öffne im Browser:   http://localhost:5380"
    
    if [ "$ENABLE_REGISTRATION" = "true" ]; then
        echo "   3. Registriere dich als erster Benutzer (= Admin)"
    else
        echo "   3. Login mit den oben angezeigten Zugangsdaten"
    fi
    
    echo ""
    echo "📚 Weitere Hilfe:"
    echo "   GitHub: https://github.com/29barra29/dns-manager"
    echo "   Docs:   https://github.com/29barra29/dns-manager/wiki"
    echo ""
fi