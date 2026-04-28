#!/bin/bash
# PDNS Manager - Update: Git-Stand aktualisieren, Backend-Image bauen, Container neu starten.
#
# Optionale Flags:
#   --rebuild       Erzwingt --no-cache Build (sonst nur bei Versionswechsel).
#   --no-backup     Ueberspringt die Frage nach einem DB-Backup.
#   --skip-fetch    Kein git fetch/pull (z. B. fuer rein lokales Rebuild).
#
# Beispiele:
#   ./update.sh
#   ./update.sh --rebuild
#   ./update.sh --no-backup --rebuild
set -e

# ----------------------------------------------------------------------------
# Args
# ----------------------------------------------------------------------------
FORCE_REBUILD=false
SKIP_BACKUP=false
SKIP_FETCH=false
for arg in "$@"; do
    case "$arg" in
        --rebuild|--no-cache) FORCE_REBUILD=true ;;
        --no-backup)          SKIP_BACKUP=true ;;
        --skip-fetch)         SKIP_FETCH=true ;;
        --help|-h)
            sed -n '2,15p' "$0"
            exit 0
            ;;
    esac
done

echo "🔄 Suche nach Updates..."

if [ ! -d .git ]; then
    echo "❌ Kein Git-Repository in diesem Ordner."
    echo "   Das passiert z. B. nach reiner Tarball-Installation ohne git clone."
    echo "   Lösung: Projekt mit git klonen und .env + Datenbank übernehmen, oder neu installieren."
    exit 1
fi

VERSION_BEFORE=$(cat VERSION 2>/dev/null | head -1 | tr -d '\r\n' || echo "?")

# ----------------------------------------------------------------------------
# Git: Tags + neueste Commits holen
# ----------------------------------------------------------------------------
if ! $SKIP_FETCH; then
    # `--prune` raeumt entfernte Branches auf. Bewusst KEIN --prune-tags mehr,
    # weil das lokale Tags wischt, die nicht im Remote sind (User-eigene Marker).
    git fetch origin --tags --force --prune

    CURRENT_BRANCH=$(git symbolic-ref -q --short HEAD 2>/dev/null || true)
    if [ "$CURRENT_BRANCH" = "main" ]; then
        echo "📌 Branch main – hole neueste Commits …"
        git pull origin main
    else
        # Nach install.sh ist HEAD oft auf einem Tag (detached HEAD).
        # Dann: auf das aktuell neueste v*-Tag wechseln.
        LATEST_TAG=$(git tag -l 'v*' --sort=-v:refname 2>/dev/null | head -1)
        if [ -n "$LATEST_TAG" ] && git rev-parse "$LATEST_TAG" >/dev/null 2>&1; then
            echo "📌 Aktualisiere auf Release $LATEST_TAG …"
            git checkout "$LATEST_TAG"
        else
            echo "📌 Kein passendes v*-Tag – nutze Branch main …"
            if git show-ref --verify --quiet refs/heads/main; then
                git checkout main
            elif git show-ref --verify --quiet refs/remotes/origin/main; then
                git checkout -B main origin/main
            else
                echo "❌ Weder v*-Tags noch origin/main gefunden. Prüfe Remote „origin“."
                exit 1
            fi
            git pull origin main
        fi
    fi
fi

VERSION_AFTER=$(cat VERSION 2>/dev/null | head -1 | tr -d '\r\n' || echo "?")

if [ "$VERSION_BEFORE" = "$VERSION_AFTER" ]; then
    echo "ℹ️  Version unverändert: $VERSION_BEFORE"
else
    echo "⬆️  Version: $VERSION_BEFORE → $VERSION_AFTER"
fi

# ----------------------------------------------------------------------------
# JWT-Secret-Hinweis – sonst werden bei jedem Update alle User ausgeloggt.
# ----------------------------------------------------------------------------
if [ -f .env ] && ! grep -qE '^JWT_SECRET_KEY=.+' .env; then
    echo ""
    echo "⚠️  Hinweis: JWT_SECRET_KEY ist in .env nicht gesetzt."
    echo "    Folge: nach jedem Container-Restart sind alle Logins ungültig."
    echo "    Fix:   echo \"JWT_SECRET_KEY=\$(openssl rand -hex 64)\" >> .env"
    echo ""
fi

# ----------------------------------------------------------------------------
# Major-Version-Sprung -> aktive Bestätigung verlangen
# ----------------------------------------------------------------------------
extract_major() { echo "${1#v}" | cut -d. -f1; }
MAJOR_BEFORE=$(extract_major "$VERSION_BEFORE")
MAJOR_AFTER=$(extract_major "$VERSION_AFTER")
if [ -n "$MAJOR_BEFORE" ] && [ -n "$MAJOR_AFTER" ] \
   && [ "$MAJOR_BEFORE" != "?" ] && [ "$MAJOR_AFTER" != "?" ] \
   && [ "$MAJOR_BEFORE" != "$MAJOR_AFTER" ]; then
    echo ""
    echo "════════════════════════════════════════════════════"
    echo "  ⚠️  MAJOR-VERSION-SPRUNG: $VERSION_BEFORE → $VERSION_AFTER"
    echo "  Bitte CHANGELOG / README lesen, BEVOR du fortfährst."
    echo "  https://github.com/29barra29/PowerDNS-PDNS-MANAGER/releases"
    echo "════════════════════════════════════════════════════"
    read -p "Trotzdem fortfahren? (j/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Jj]$ ]]; then
        echo "Update abgebrochen."
        exit 0
    fi
fi

# ----------------------------------------------------------------------------
# Docker-Befehle (mit sudo-Fallback wie install.sh)
# ----------------------------------------------------------------------------
if ! docker ps &> /dev/null && sudo docker ps &> /dev/null 2>&1; then
    COMPOSE_CMD="sudo docker compose"
    DOCKER_CMD="sudo docker"
else
    COMPOSE_CMD="docker compose"
    DOCKER_CMD="docker"
fi

# ----------------------------------------------------------------------------
# Optional: DB-Dump anlegen, bevor irgendetwas neu gebaut wird
# ----------------------------------------------------------------------------
if ! $SKIP_BACKUP && [ -f .env ]; then
    echo ""
    read -p "💾 Vor dem Update einen DB-Dump anlegen? (j/n) [j]: " -n 1 -r DB_BACKUP_REPLY
    echo
    if [[ ! $DB_BACKUP_REPLY =~ ^[Nn]$ ]]; then
        BACKUP_FILE="backup_${VERSION_BEFORE}_$(date +%Y%m%d-%H%M%S).sql"
        # Werte direkt aus .env lesen, ohne sie ins Shell-Env zu leaken.
        DB_ROOT_PW=$(grep -E '^DB_ROOT_PASSWORD=' .env | head -1 | cut -d= -f2- || true)
        DB_NAME_VAL=$(grep -E '^DB_NAME=' .env | head -1 | cut -d= -f2- || true)
        DB_NAME_VAL=${DB_NAME_VAL:-dns_manager}

        if [ -z "$DB_ROOT_PW" ]; then
            echo "ℹ️  DB_ROOT_PASSWORD nicht in .env gefunden – Backup übersprungen."
        else
            # Container-ID via compose holen, damit wir nicht von einem festen Namen abhaengen.
            DB_CID=$($COMPOSE_CMD ps -q mariadb 2>/dev/null || true)
            if [ -z "$DB_CID" ]; then
                echo "ℹ️  MariaDB-Container läuft nicht – Backup übersprungen."
            else
                echo "→ Schreibe $BACKUP_FILE …"
                if $DOCKER_CMD exec "$DB_CID" mysqldump --single-transaction --quick \
                        -u root -p"$DB_ROOT_PW" "$DB_NAME_VAL" > "$BACKUP_FILE" 2>/dev/null; then
                    SIZE=$(du -h "$BACKUP_FILE" 2>/dev/null | cut -f1 || echo "?")
                    echo "✅ Backup ok ($SIZE) – $BACKUP_FILE"
                else
                    echo "⚠️  Backup fehlgeschlagen (DB-Login? Container healthy?). Datei wird entfernt."
                    rm -f "$BACKUP_FILE"
                fi
            fi
        fi
    fi
fi

# ----------------------------------------------------------------------------
# Build: --no-cache nur bei Versionswechsel ODER --rebuild
# (Spart bei kleinen Updates 3-5 Minuten Frontend/Backend-Rebuild.)
# ----------------------------------------------------------------------------
BUILD_FLAGS=()
if $FORCE_REBUILD || [ "$VERSION_BEFORE" != "$VERSION_AFTER" ]; then
    BUILD_FLAGS+=(--no-cache)
    echo "📦 Baue backend neu (--no-cache) – das kann ein paar Minuten dauern …"
else
    echo "📦 Baue backend (Cache wird genutzt) – Code-Änderungen übernehmen sich, Dependencies bleiben gecacht."
fi

$COMPOSE_CMD build "${BUILD_FLAGS[@]}" backend
$COMPOSE_CMD up -d

echo "✅ App-Update erfolgreich abgeschlossen!"

# ----------------------------------------------------------------------------
# Status der Compose-Services anzeigen (generisch, ohne Container-Namen-Filter)
# ----------------------------------------------------------------------------
$COMPOSE_CMD ps --format "table {{.Service}}\t{{.Status}}" 2>/dev/null \
    || $DOCKER_CMD ps --format "table {{.Names}}\t{{.Status}}"
