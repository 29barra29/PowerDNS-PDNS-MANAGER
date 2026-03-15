#!/bin/bash

echo "🔄 Suche nach Updates..."
git pull origin main

# Check Docker permissions
if ! docker ps &> /dev/null && sudo docker ps &> /dev/null; then
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
$DOCKER_CMD ps | grep dns-manager-api
