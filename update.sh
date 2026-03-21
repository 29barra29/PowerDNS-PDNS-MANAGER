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

# Immer neueste Referenzen von GitHub
git fetch origin --tags --force

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
