#!/bin/bash

echo "🔄 Suche nach Updates..."
git pull origin main

echo "📦 Baue und starte Container neu. Das kann einen Moment dauern..."
docker compose build --no-cache backend
docker compose up -d

echo "✅ App-Update erfolgreich abgeschlossen!"
docker ps | grep dns-manager-api
