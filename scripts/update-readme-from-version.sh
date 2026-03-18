#!/usr/bin/env bash
# Liest die VERSION-Datei und trägt die Version in README.md ein (Badge + Beispiel).
# Nach dem Ändern von VERSION einmal ausführen, dann README committen.
set -e
cd "$(dirname "$0")/.."
V=$(cat VERSION 2>/dev/null | head -1 | tr -d '\n')
if [ -z "$V" ]; then
  echo "Fehler: VERSION-Datei leer oder nicht gefunden."
  exit 1
fi
echo "Aktualisiere README.md auf Version $V"
# Badge: version-vX.X.X
sed -i.bak "s/version-v[0-9][0-9.]*/version-v$V/g" README.md
# Beispiel: git checkout vX.X.X
sed -i.bak "s/git checkout v[0-9][0-9.]*/git checkout v$V/g" README.md
rm -f README.md.bak
echo "Fertig. README.md angepasst."
