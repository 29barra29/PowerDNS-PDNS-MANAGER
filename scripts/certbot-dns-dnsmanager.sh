#!/usr/bin/env bash
# certbot-dns-dnsmanager.sh
#
# certbot Hook-Skript fuer DNS-01-Challenges via DNS Manager (PowerDNS-Frontend).
# Wird sowohl als --manual-auth-hook als auch als --manual-cleanup-hook benutzt;
# certbot setzt die Variable $CERTBOT_AUTH_OUTPUT nur beim Cleanup, daran
# erkennt das Skript, welche Phase gerade laeuft.
#
# Voraussetzungen:
#   1. Im DNS Manager unter Settings -> ACME / Auto-TLS einen Token erstellen.
#      Der Token wird genau EINMAL angezeigt - sicher abspeichern.
#   2. Token + Manager-URL hier ueber Env-Variablen oder /etc/dnsmgr.env setzen:
#         DNSMGR_URL="https://dns.example.com"
#         DNSMGR_TOKEN="dnsmgr_acme_..."
#   3. Skript ausfuehrbar machen:
#         chmod +x /usr/local/bin/certbot-dns-dnsmanager.sh
#
# Aufruf durch certbot:
#   certbot certonly \
#     --manual --preferred-challenges=dns \
#     --manual-auth-hook    /usr/local/bin/certbot-dns-dnsmanager.sh \
#     --manual-cleanup-hook /usr/local/bin/certbot-dns-dnsmanager.sh \
#     -d smtp.example.com
#
# certbot reicht uns folgende Variablen rein:
#   CERTBOT_DOMAIN     -> z.B. "smtp.example.com"  (ohne Wildcard-Stern)
#   CERTBOT_VALIDATION -> der Base64URL-String, der als TXT veroeffentlicht werden soll
#   CERTBOT_AUTH_OUTPUT -> NUR beim Cleanup gesetzt
#
# Exit-Codes: 0 = OK, !=0 = Fehler (certbot bricht den Renew-Lauf ab).

set -euo pipefail

# Optional: Defaults aus /etc/dnsmgr.env (root-only, chmod 600 empfohlen) lesen.
# So muss man die Vars nicht in der certbot-systemd-Unit pflegen.
if [[ -r /etc/dnsmgr.env ]]; then
    # shellcheck disable=SC1091
    . /etc/dnsmgr.env
fi

DNSMGR_URL="${DNSMGR_URL:-}"
DNSMGR_TOKEN="${DNSMGR_TOKEN:-}"
CURL_OPTS=("--silent" "--show-error" "--max-time" "30")

if [[ -z "$DNSMGR_URL" || -z "$DNSMGR_TOKEN" ]]; then
    echo "[dnsmgr-hook] DNSMGR_URL und DNSMGR_TOKEN muessen gesetzt sein" >&2
    echo "[dnsmgr-hook] Tipp: in /etc/dnsmgr.env ablegen (chmod 600)" >&2
    exit 2
fi

if [[ -z "${CERTBOT_DOMAIN:-}" || -z "${CERTBOT_VALIDATION:-}" ]]; then
    echo "[dnsmgr-hook] CERTBOT_DOMAIN/CERTBOT_VALIDATION fehlt - vom certbot aufgerufen?" >&2
    exit 2
fi

# Phase: Cleanup wird mit gesetztem CERTBOT_AUTH_OUTPUT aufgerufen.
if [[ -n "${CERTBOT_AUTH_OUTPUT:-}" ]]; then
    ENDPOINT="cleanup"
    PROPAGATE_WAIT=0
else
    ENDPOINT="present"
    # Wartezeit fuer DNS-Propagation: nach dem PUT muss der ACME-Server (Let's
    # Encrypt) den TXT global sehen koennen. Bei PowerDNS mit Slave/Notify
    # reichen 15-20s; bei externen Resolver-Caches ist mehr besser.
    PROPAGATE_WAIT="${DNSMGR_PROPAGATE_SECONDS:-30}"
fi

# JSON-Body bauen ohne externes jq - die beiden Werte sind base64url bzw. ein
# FQDN, also keine Quoting-Surprises zu erwarten.
JSON=$(printf '{"domain":"%s","validation":"%s"}' \
    "$CERTBOT_DOMAIN" "$CERTBOT_VALIDATION")

URL="${DNSMGR_URL%/}/api/v1/acme/${ENDPOINT}"

echo "[dnsmgr-hook] ${ENDPOINT}: ${CERTBOT_DOMAIN} -> ${URL}"

HTTP_CODE=$(curl "${CURL_OPTS[@]}" \
    -o /tmp/dnsmgr-acme-response.$$ \
    -w "%{http_code}" \
    -X POST "$URL" \
    -H "Authorization: Bearer ${DNSMGR_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "$JSON" || echo "000")

BODY="$(cat /tmp/dnsmgr-acme-response.$$ 2>/dev/null || true)"
rm -f /tmp/dnsmgr-acme-response.$$

if [[ "$HTTP_CODE" != "200" && "$HTTP_CODE" != "204" ]]; then
    echo "[dnsmgr-hook] FEHLER (HTTP $HTTP_CODE): $BODY" >&2
    exit 1
fi

echo "[dnsmgr-hook] OK ($HTTP_CODE)"

if [[ "$ENDPOINT" == "present" && "$PROPAGATE_WAIT" -gt 0 ]]; then
    echo "[dnsmgr-hook] Warte ${PROPAGATE_WAIT}s auf DNS-Propagation..."
    sleep "$PROPAGATE_WAIT"
fi

exit 0
