#!/bin/bash
# DNS Manager – Update: Git-Stand aktualisieren, Backend-Image neu bauen, Container neu starten.
set -e

echo "🔄 Suche nach Updates..."

if [ ! -d .git ]; then
    echo "❌ Kein Git-Repository in diesem Ordner."
    echo "   Das passiert z. B. nach reiner Tarball-Installation ohne git clone."
    echo "   Lösung: Projekt mit git klonen und .env + Datenbank übernehmen, oder neu installieren."
    exit 1
fi

# Aktuelle Version (vor dem Pull) merken
VERSION_BEFORE=$(cat VERSION 2>/dev/null | head -1 | tr -d '\r\n' || echo "?")

# Immer neueste Referenzen von GitHub
git fetch origin --tags --force --prune --prune-tags

# Nach install.sh: oft Release-Tag (detached HEAD) → „git pull origin main“ ändert den Checkout nicht.
# Auf Branch main: nur pull. Sonst: wie install.sh → neuestes v*-Tag, sonst main.
CURRENT_BRANCH=$(git symbolic-ref -q --short HEAD 2>/dev/null || true)
if [ "$CURRENT_BRANCH" = "main" ]; then
    echo "📌 Branch main – hole neueste Commits …"
    git pull origin main
else
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

VERSION_AFTER=$(cat VERSION 2>/dev/null | head -1 | tr -d '\r\n' || echo "?")

if [ "$VERSION_BEFORE" = "$VERSION_AFTER" ]; then
    echo "ℹ️  Version unverändert: $VERSION_BEFORE"
else
    echo "⬆️  Version: $VERSION_BEFORE → $VERSION_AFTER"
fi

# Hinweis ausgeben, wenn JWT_SECRET_KEY in .env fehlt – sonst werden bei jedem Update alle User ausgeloggt.
if [ -f .env ] && ! grep -qE '^JWT_SECRET_KEY=.+' .env; then
    echo ""
    echo "⚠️  Hinweis: JWT_SECRET_KEY ist in .env nicht gesetzt."
    echo "    Folge: nach jedem Container-Restart sind alle Logins ungültig."
    echo "    Fix:   echo \"JWT_SECRET_KEY=\$(openssl rand -hex 64)\" >> .env"
    echo ""
fi

# Docker (optional mit sudo wie bei install.sh)
if ! docker ps &> /dev/null && sudo docker ps &> /dev/null 2>&1; then
    COMPOSE_CMD="sudo docker compose"
    DOCKER_CMD="sudo docker"
else
    COMPOSE_CMD="docker compose"
    DOCKER_CMD="docker"
fi

echo "📦 Baue und starte Container neu. Das kann einen Moment dauern..."
$COMPOSE_CMD build --no-cache backend
$COMPOSE_CMD up -d

echo "✅ App-Update erfolgreich abgeschlossen!"
$DOCKER_CMD ps -a --filter "name=dns-manager" --format "table {{.Names}}\t{{.Status}}" 2>/dev/null || $DOCKER_CMD ps
