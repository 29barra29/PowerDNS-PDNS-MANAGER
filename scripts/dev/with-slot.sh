#!/usr/bin/env bash
# with-slot.sh – fuehrt ein Kommando erst aus, wenn einer von (Default) 2 Slots frei ist.
#
# Hintergrund: Der Entwicklungs-Host hat 2 CPUs und traegt die Produktion. Schwere Schritte
# (docker build, npm ci/build, DB-Tests) laufen deshalb nie mehr als zweimal gleichzeitig.
# Die Slots sind Lock-Dateien /tmp/pdnsmgr-slots/0 und /tmp/pdnsmgr-slots/1 (flock).
#
# Aufruf:  scripts/dev/with-slot.sh <kommando> [argumente ...]
# Beispiel: scripts/dev/with-slot.sh docker build -t pdnsmgr-e2e:local -f backend/Dockerfile .
#
# Umgebung:
#   PDNSMGR_SLOT_DIR      Lock-Verzeichnis (Default /tmp/pdnsmgr-slots)
#   PDNSMGR_SLOTS         Anzahl Slots (Default 2)
#   PDNSMGR_SLOT_TIMEOUT  max. Wartezeit in Sekunden (Default 0 = unbegrenzt)
#   PDNSMGR_SLOT          wird fuer das Kommando gesetzt (Nummer des belegten Slots).
#                         Ist es beim Aufruf bereits gesetzt (verschachtelter Aufruf), laeuft
#                         das Kommando direkt im bestehenden Slot – kein Deadlock.
#
# Exit-Code: der des Kommandos; 2 bei Aufruffehler; 124 bei Zeitueberschreitung.
set -uo pipefail

if [ "$#" -eq 0 ]; then
  echo "Aufruf: $0 <kommando> [argumente ...]" >&2
  exit 2
fi

# Verschachtelter Aufruf (z. B. build-test-image.sh innerhalb eines with-slot-Laufs).
if [ -n "${PDNSMGR_SLOT:-}" ]; then
  exec "$@"
fi

SLOT_DIR="${PDNSMGR_SLOT_DIR:-/tmp/pdnsmgr-slots}"
SLOTS="${PDNSMGR_SLOTS:-2}"
TIMEOUT="${PDNSMGR_SLOT_TIMEOUT:-0}"

case "$SLOTS" in ''|*[!0-9]*|0) echo "PDNSMGR_SLOTS muss eine positive Zahl sein" >&2; exit 2;; esac
case "$TIMEOUT" in ''|*[!0-9]*) echo "PDNSMGR_SLOT_TIMEOUT muss eine Zahl (Sekunden) sein" >&2; exit 2;; esac

if ! command -v flock >/dev/null 2>&1; then
  echo "flock nicht gefunden (Paket util-linux)" >&2
  exit 2
fi

mkdir -p "$SLOT_DIR" || { echo "Kann $SLOT_DIR nicht anlegen" >&2; exit 2; }

start=$(date +%s)
announced=0
while :; do
  i=0
  while [ "$i" -lt "$SLOTS" ]; do
    lockfile="$SLOT_DIR/$i"
    # Datei oeffnen (anlegen, falls noetig) und nicht-blockierend sperren.
    if exec {fd}>>"$lockfile"; then
      if flock -n "$fd"; then
        export PDNSMGR_SLOT="$i"
        # Wartezeit/Slot nur melden, wenn gewartet wurde (stdout bleibt dem Kommando).
        if [ "$announced" -eq 1 ]; then
          echo "[with-slot] Slot $i belegt nach $(( $(date +%s) - start )) s" >&2
        fi
        # Das Kommando erbt den Lock-Deskriptor NICHT ({fd}>&-): Der Slot gehoert dieser Shell und
        # wird frei, sobald das Kommando endet – auch wenn es Hintergrundprozesse hinterlaesst.
        "$@" {fd}>&-
        rc=$?
        flock -u "$fd"
        exec {fd}>&-
        exit "$rc"
      fi
      exec {fd}>&-
    fi
    i=$((i + 1))
  done
  if [ "$announced" -eq 0 ]; then
    echo "[with-slot] alle $SLOTS Slots belegt – warte ..." >&2
    announced=1
  fi
  if [ "$TIMEOUT" -gt 0 ] && [ $(( $(date +%s) - start )) -ge "$TIMEOUT" ]; then
    echo "[with-slot] Zeitueberschreitung nach $TIMEOUT s" >&2
    exit 124
  fi
  sleep 2
done
