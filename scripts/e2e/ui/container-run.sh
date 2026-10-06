#!/usr/bin/env bash
# container-run.sh – laeuft IM Playwright-Container (Dienst "ui" aus compose.ui.yaml), nicht auf dem Host.
#
# /ui-src  = scripts/e2e/ui (read-only), /out = Arbeitsordner des Laufs (beschreibbar),
# /npm-cache = npm-Cache (bleibt zwischen Laeufen erhalten), /locales = frontend/src/locales (read-only).
#
# Kopiert die Testquellen nach /out/app, installiert @playwright/test exakt nach package-lock.json
# (npm ci, Browser liegen schon im Image unter /ms-playwright) und startet "playwright test" mit den
# uebergebenen Argumenten. Exit-Code = Exit-Code von Playwright.
set -euo pipefail

SRC=/ui-src
APP=/out/app
mkdir -p "$HOME" "$APP"

# Frische Kopie der Testquellen (ohne node_modules/Berichte eines lokalen Laufs).
rm -rf "$APP/tests" "$APP/fixtures"
cp "$SRC/package.json" "$SRC/package-lock.json" "$SRC/playwright.config.js" "$APP/"
cp -r "$SRC/tests" "$SRC/fixtures" "$APP/"

cd "$APP"
want="$(sha256sum package-lock.json | cut -d' ' -f1)"
have="$(cat node_modules/.lock-sha256 2>/dev/null || true)"
if [ "$want" != "$have" ]; then
  echo "[ui] npm ci (@playwright/test laut package-lock.json) ..."
  npm ci --no-audit --no-fund --prefer-offline --loglevel=error
  printf '%s\n' "$want" >node_modules/.lock-sha256
fi

echo "[ui] @playwright/test $(node -p 'require("@playwright/test/package.json").version'), Node $(node --version)"

exec npx --no-install playwright test "$@"
