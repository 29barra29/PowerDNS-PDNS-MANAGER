#!/bin/bash
# PDNS Manager - Update: Git-Stand aktualisieren, Backend-Image bauen, Container neu starten.
#
# Optionale Flags:
#   --rebuild          Erzwingt --no-cache Build (sonst nur bei Versionswechsel).
#   --no-backup        Ueberspringt die Frage nach einem DB-Backup.
#   --skip-fetch       Kein git fetch/pull (z. B. fuer rein lokales Rebuild).
#   --no-key-backup    Keine Kopie des Schluessels fuer gespeicherte Geheimnisse anlegen.
#   --backup-key-only  Nur den Schluessel des laufenden Backends sichern (kein Update).
#
# Schluessel-Sicherung (ab 3.0): Liegt der Schluessel nicht in der .env (SECRET_ENCRYPTION_KEY), sondern
# im Daten-Volume (/app/data/.secret_key), legt das Skript nach dem Start EINE Kopie je Schluessel unter
# ${PDNSMGR_KEY_BACKUP_DIR:-$HOME/.pdnsmgr-keys}/<stack>-<fingerprint>.key ab (Ordner 0700, Datei 0600) –
# nie neben den DB-Dump und nie im Stack-Ordner. Dieses Verzeichnis getrennt sichern.
#
# Beispiele:
#   ./update.sh
#   ./update.sh --rebuild
#   ./update.sh --no-backup --rebuild
#   ./update.sh --backup-key-only
set -e

# ----------------------------------------------------------------------------
# Args
# ----------------------------------------------------------------------------
FORCE_REBUILD=false
SKIP_BACKUP=false
SKIP_FETCH=false
SKIP_KEY_BACKUP=false
KEY_BACKUP_ONLY=false
for arg in "$@"; do
    case "$arg" in
        --rebuild|--no-cache) FORCE_REBUILD=true ;;
        --no-backup)          SKIP_BACKUP=true ;;
        --skip-fetch)         SKIP_FETCH=true ;;
        --no-key-backup)      SKIP_KEY_BACKUP=true ;;
        --backup-key-only)    KEY_BACKUP_ONLY=true ;;
        --help|-h)
            sed -n '2,21p' "$0"
            exit 0
            ;;
    esac
done

KEY_BACKUP_DIR="${PDNSMGR_KEY_BACKUP_DIR:-$HOME/.pdnsmgr-keys}"

# ----------------------------------------------------------------------------
# Docker-Befehle (mit sudo-Fallback wie install.sh)
# ----------------------------------------------------------------------------
detect_docker() {
    if ! docker ps &> /dev/null && sudo docker ps &> /dev/null 2>&1; then
        COMPOSE_CMD="sudo docker compose"
        DOCKER_CMD="sudo docker"
    else
        COMPOSE_CMD="docker compose"
        DOCKER_CMD="docker"
    fi
}

# ----------------------------------------------------------------------------
# Schluessel fuer gespeicherte Geheimnisse sichern (ab 3.0) [S7]
# ----------------------------------------------------------------------------
# Name des Stacks fuer den Dateinamen der Kopie (Compose-Projektname oder Ordnername).
stack_name() {
    local n="${COMPOSE_PROJECT_NAME:-$(basename "$PWD")}"
    n=$(printf '%s' "$n" | tr -c 'A-Za-z0-9._-' '_')
    printf '%s' "${n:-pdns-manager}"
}

# Liegt $1 im Stack-Ordner (aktuelles Verzeichnis)? Dann waere die Kopie Teil desselben Backups.
path_inside_stack() {
    local target here
    command -v realpath >/dev/null 2>&1 || return 1
    target=$(realpath -m "$1" 2>/dev/null) || return 1
    here=$(realpath -m "$PWD" 2>/dev/null) || return 1
    case "$target/" in
        "$here"/*) return 0 ;;
    esac
    return 1
}

# Wert aus der Ausgabe von "python -m app.cli.secrets key-info" (Zeilen name=wert).
key_info_value() {
    printf '%s\n' "$1" | sed -n "s/^$2=//p" | head -1
}

# $1 = ask (interaktiv nachfragen, Default ja) | yes (ohne Rueckfrage).
# Kopiert den Schluessel des laufenden Backends nach $KEY_BACKUP_DIR/<stack>-<fingerprint>.key,
# nur wenn diese Datei noch fehlt. Gibt nie den Schluessel aus. Rueckgabe 1 bei Fehlern.
backup_secret_key() {
    local mode="$1" info src fp kfile explicit target tmp len reply
    KEY_BACKUP_FILE=""
    if [ -f .env ] && grep -qE '^SECRET_ENCRYPTION_KEY=.+' .env; then
        echo "🔑 Der Schlüssel für gespeicherte Geheimnisse steht in der .env (SECRET_ENCRYPTION_KEY)."
        echo "   Keine Kopie nötig – die .env getrennt vom DB-Backup sichern."
        return 0
    fi
    if ! info=$($COMPOSE_CMD exec -T backend python -m app.cli.secrets key-info 2>/dev/null); then
        echo "⚠️  Schlüssel-Info nicht abrufbar (läuft das Backend in Version 3.x?)."
        echo "   Schlüssel bitte von Hand sichern, siehe INSTALL.md → Geheimnisse & Schlüssel."
        return 1
    fi
    src=$(key_info_value "$info" source)
    fp=$(key_info_value "$info" fingerprint)
    kfile=$(key_info_value "$info" key_file)
    explicit=$(key_info_value "$info" key_file_explicit)
    case "$src" in
        env)
            echo "🔑 Der Schlüssel kommt aus SECRET_ENCRYPTION_KEY (Fingerprint $fp) – dort getrennt vom DB-Backup sichern."
            return 0
            ;;
        file) ;;
        *)
            echo "⚠️  Kein Schlüssel für gespeicherte Geheimnisse aktiv (Quelle: ${src:-?})."
            echo "   Bitte Einstellungen → Sicherheit und das Backend-Log prüfen."
            return 1
            ;;
    esac
    if [ "$explicit" = "1" ]; then
        echo "🔑 Der Schlüssel kommt aus SECRET_ENCRYPTION_KEY_FILE=$kfile (Fingerprint $fp) – diese Datei selbst sichern."
        return 0
    fi
    if ! printf '%s' "$fp" | grep -qE '^[0-9a-f]{12}$'; then
        echo "⚠️  Unerwartete Schlüssel-Info vom Backend – keine Kopie angelegt."
        return 1
    fi
    if path_inside_stack "$KEY_BACKUP_DIR"; then
        echo "⚠️  $KEY_BACKUP_DIR liegt im Stack-Ordner – dort wird der Schlüssel NICHT abgelegt"
        echo "   (er würde mit dem DB-Backup zusammen gesichert). PDNSMGR_KEY_BACKUP_DIR auf einen Ordner"
        echo "   außerhalb setzen und ./update.sh --backup-key-only ausführen."
        return 1
    fi
    target="$KEY_BACKUP_DIR/$(stack_name)-$fp.key"
    if [ -f "$target" ]; then
        echo "🔑 Schlüssel bereits gesichert: $target (Fingerprint $fp)"
        KEY_BACKUP_FILE="$target"
        return 0
    fi
    if [ "$mode" = "ask" ] && [ -t 0 ]; then
        read -p "🔑 Schlüssel für gespeicherte Geheimnisse jetzt nach $KEY_BACKUP_DIR sichern? (j/n) [j]: " -n 1 -r reply || true
        echo
        if [[ $reply =~ ^[Nn]$ ]]; then
            echo "   Übersprungen. Später nachholen: ./update.sh --backup-key-only (Fingerprint $fp)"
            return 0
        fi
    fi
    ( umask 077; mkdir -p "$KEY_BACKUP_DIR" ) && chmod 700 "$KEY_BACKUP_DIR" || {
        echo "⚠️  $KEY_BACKUP_DIR kann nicht angelegt werden – keine Kopie."
        return 1
    }
    tmp="$target.tmp.$$"
    if ( umask 077; $COMPOSE_CMD exec -T backend cat "$kfile" > "$tmp" ) 2>/dev/null; then
        len=$(tr -d '\r\n' < "$tmp" | wc -c | tr -d ' ')
        if [ "$len" != "44" ]; then
            rm -f "$tmp"
            echo "⚠️  Die Schlüsseldatei im Backend sieht ungültig aus – keine Kopie angelegt."
            return 1
        fi
        chmod 600 "$tmp" && mv -f "$tmp" "$target"
        KEY_BACKUP_FILE="$target"
        echo "🔑 Schlüssel gesichert: $target (Fingerprint $fp, nur für dich lesbar)."
        echo "   Diesen Ordner NICHT in das Backup-Set des Stack-Ordners aufnehmen, sondern getrennt sichern"
        echo "   (z. B. Passwortmanager oder anderes Medium). Ohne Schlüssel sind die Geheimnisse aus einem"
        echo "   DB-Backup nicht lesbar. Fingerprint vergleichen: Einstellungen → Sicherheit."
        return 0
    fi
    rm -f "$tmp"
    echo "⚠️  Schlüssel konnte nicht aus dem Backend kopiert werden – bitte von Hand sichern (INSTALL.md)."
    return 1
}

if $KEY_BACKUP_ONLY; then
    detect_docker
    if backup_secret_key yes; then
        exit 0
    fi
    exit 1
fi

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
    # Lokale Aenderungen an versionierten Dateien (typisch: compose.yaml fuer die Port-Bindung
    # editiert) lassen git checkout/pull mit "would be overwritten" scheitern -> vorher klar sagen.
    if [ -n "$(git status --porcelain --untracked-files=no 2>/dev/null)" ]; then
        echo ""
        echo "⚠️  Lokale Änderungen an versionierten Dateien gefunden:"
        git status --short --untracked-files=no 2>/dev/null | sed 's/^/     /'
        echo "    Damit bricht das Update (git checkout/pull) gleich ab."
        echo "    Bitte compose.yaml nicht editieren – die Port-Bindung gehört als"
        echo "    BIND_ADDR=127.0.0.1 / HOST_PORT=5380 in die .env."
        echo "    Änderungen beiseitelegen: git stash   (oder verwerfen: git checkout -- <datei>)"
        echo ""
    fi

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
                echo "❌ Weder v*-Tags noch origin/main gefunden. Prüfe das Remote 'origin'."
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
    echo "    Das Backend nutzt dann einen automatisch erzeugten Schlüssel aus dem Daten-Volume (backend_data)."
    echo "    Empfohlen: echo \"JWT_SECRET_KEY=\$(openssl rand -hex 64)\" >> .env  (danach einmal neu anmelden)"
    echo ""
fi

# ----------------------------------------------------------------------------
# Hinweis ab v2.4.1: Reset-Mails brauchen die oeffentliche App-Basis-URL
# ----------------------------------------------------------------------------
if [ -f .env ] && ! grep -qE '^WEBAUTHN_ORIGIN=.+' .env; then
    echo "ℹ️  Ab v2.4.1 werden Passwort-Reset-Links nur noch aus der App-Basis-URL gebaut."
    echo "    Nach dem Update bitte prüfen: Einstellungen → Profil → Öffentliche Basis-URL (z. B. https://dns.example.com)."
    echo "    Alternativ WEBAUTHN_ORIGIN=https://<dein-host> in der .env setzen."
    echo ""
fi

# ----------------------------------------------------------------------------
# Major-Version-Sprung -> aktive Bestätigung verlangen
# ----------------------------------------------------------------------------
extract_major() { echo "${1#v}" | cut -d. -f1; }
MAJOR_BEFORE=$(extract_major "$VERSION_BEFORE")
MAJOR_AFTER=$(extract_major "$VERSION_AFTER")
# Sprung von < 3 auf >= 3 (Verschluesselung der Geheimnisse, Downgrade-Grenze)
is_major_30_jump() {
    [[ "$MAJOR_BEFORE" =~ ^[0-9]+$ ]] && [[ "$MAJOR_AFTER" =~ ^[0-9]+$ ]] \
        && [ "$MAJOR_BEFORE" -lt 3 ] && [ "$MAJOR_AFTER" -ge 3 ]
}
if [ -n "$MAJOR_BEFORE" ] && [ -n "$MAJOR_AFTER" ] \
   && [ "$MAJOR_BEFORE" != "?" ] && [ "$MAJOR_AFTER" != "?" ] \
   && [ "$MAJOR_BEFORE" != "$MAJOR_AFTER" ]; then
    echo ""
    echo "════════════════════════════════════════════════════"
    echo "  ⚠️  MAJOR-VERSION-SPRUNG: $VERSION_BEFORE → $VERSION_AFTER"
    echo "  Bitte CHANGELOG / README lesen, BEVOR du fortfährst."
    echo "  https://github.com/29barra29/PowerDNS-PDNS-MANAGER/releases"
    if is_major_30_jump; then
        echo "  ──────────────────────────────────────────────────"
        echo "  Upgrade auf 3.0 – Ablauf und Prüfliste: INSTALL.md → „Upgrade von 2.x auf 3.0“"
        echo "  • Geheimnisse: 3.0 verschlüsselt beim ersten Start alle gespeicherten Geheimnisse"
        echo "    (PowerDNS-API-Keys, SMTP-Passwort, Captcha-Secret, Webhook-Secrets und -URLs,"
        echo "    2FA-Geheimnisse) in der Datenbank."
        echo "  • Schlüssel: SECRET_ENCRYPTION_KEY in der .env oder automatisch erzeugt in"
        echo "    /app/data/.secret_key (Volume backend_data). Im zweiten Fall legt dieses Skript"
        echo "    nach dem Start eine Kopie im Schlüssel-Backup-Verzeichnis ab:"
        echo "      $KEY_BACKUP_DIR   (änderbar per PDNSMGR_KEY_BACKUP_DIR)"
        echo "    – nie im Stack-Ordner, nie neben dem Dump. Dieses Verzeichnis bzw. die .env"
        echo "    getrennt vom DB-Backup sichern: Ohne Schlüssel sind die Geheimnisse aus einem"
        echo "    DB-Backup NICHT lesbar."
        echo "  • Der Dump von vor dem Update enthält die Geheimnisse noch im Klartext."
        echo "  • Downgrade-Grenze: zurück auf 2.4.x nur mit dem DB-Dump von vorher oder nach"
        echo "      python -m app.cli.secrets prepare-downgrade --yes"
        echo "    (im 3.0-Backend-Container, siehe INSTALL.md → „Downgrade auf 2.4.x“)."
        echo "    Konten mit SSO-/LDAP-Anmeldung können sich unter 2.4.x nicht anmelden."
        echo "  • Einstellungen, Token- und Webhook-Verwaltung nur noch per Browser-Anmeldung"
        echo "    (nicht mehr per Panel-Token); Admin-Endpunkte per Token nur mit allow_admin."
        echo "  • Webhooks werden ab 3.0 wirklich zugestellt (2.3.7–2.4.x nie) – Empfänger prüfen."
        echo "  • Neue Variablen (optional, die Standardwerte reichen): SECRET_ENCRYPTION_KEY,"
        echo "    SECRET_ENCRYPTION_KEY_PREVIOUS, SECRET_ENCRYPTION_KEY_FILE, BACKGROUND_WORKERS_ENABLED,"
        echo "    METRICS_TOKEN, SSO_ALLOW_INSECURE (nur Tests), PDNSMGR_KEY_BACKUP_DIR (dieses Skript)."
        echo "  • /metrics ist neu und ohne Scrape-Token aus (404). Wer es nutzt: im Reverse-Proxy"
        echo "    nur für den Prometheus-Server freigeben. /health zeigt Details nur noch lokal."
        echo "  • DynDNS hinter einem Reverse-Proxy: TRUST_PROXY_HEADERS=true (ggf. TRUSTED_PROXY_HOPS)"
        echo "    in der .env – sonst sehen Rate-Limits und IP-Erkennung nur die Proxy-Adresse."
        echo "  • compose.yaml: fester DNS 8.8.8.8/8.8.4.4 entfällt (Resolver des Docker-Hosts,"
        echo "    eigene per compose.override.yaml). Eigene DB: MariaDB ≥ 10.6 / MySQL ≥ 8.0 mit ALTER-Recht."
    fi
    echo "════════════════════════════════════════════════════"
    read -p "Trotzdem fortfahren? (j/y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[JjYy]$ ]]; then
        echo "Update abgebrochen."
        exit 0
    fi
fi

# ----------------------------------------------------------------------------
# Docker-Befehle (mit sudo-Fallback wie install.sh)
# ----------------------------------------------------------------------------
detect_docker

# ----------------------------------------------------------------------------
# Optional: DB-Dump anlegen, bevor irgendetwas neu gebaut wird
# ----------------------------------------------------------------------------
BACKUP_DONE=""
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
                # Passwort per Umgebungsvariable statt als -p-Argument, damit es nicht in der
                # Prozessliste (ps / /proc) des Containers auftaucht.
                # umask 077: der Dump enthaelt Passwort-Hashes und (vor 3.0) Geheimnisse im Klartext.
                if ( umask 077; $DOCKER_CMD exec -e MYSQL_PWD="$DB_ROOT_PW" "$DB_CID" mysqldump --single-transaction --quick \
                        -u root "$DB_NAME_VAL" > "$BACKUP_FILE" 2>/dev/null ); then
                    chmod 600 "$BACKUP_FILE" 2>/dev/null || true
                    SIZE=$(du -h "$BACKUP_FILE" 2>/dev/null | cut -f1 || echo "?")
                    echo "✅ Backup ok ($SIZE, nur für dich lesbar) – $BACKUP_FILE"
                    BACKUP_DONE="$BACKUP_FILE"
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

# Daten- und Uploads-Volume dem App-User (UID 1001) geben, BEVOR das Backend startet: Installationen
# vor v2.3.3 haben sie als root angelegt. Ab 3.0 erzeugt das Backend beim ersten Start seinen
# Schluessel in /app/data – ohne Schreibrecht liefe es im unverschluesselten Notbetrieb.
$COMPOSE_CMD run --rm --no-deps -T --name "pdnsmgr-chown-$$" -u root backend \
    chown -R 1001:1001 /app/app/static_new/uploads /app/data >/dev/null 2>&1 || true

$COMPOSE_CMD up -d

# Uploads-Volume dem App-User (UID 1001) geben: Installationen vor v2.3.3 haben es als
# root angelegt, seitdem laeuft der Prozess ohne root -> Logo-Upload/JWT-Key-Datei wuerden
# mit "Permission denied" scheitern.
$COMPOSE_CMD exec -T -u root backend chown -R 1001:1001 /app/app/static_new/uploads /app/data 2>/dev/null || true

# ----------------------------------------------------------------------------
# Warten, bis das Backend gesund ist (lokal im Container, max. 120 s). Ein Startabbruch
# (z. B. "Start abgebrochen – Schluessel ...") wird hier sichtbar statt erst im Browser.
# ----------------------------------------------------------------------------
echo "⏳ Warte auf das Backend (max. 120 s) …"
HEALTH_OK=false
for _i in $(seq 1 60); do
    if $COMPOSE_CMD exec -T backend python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).read()" >/dev/null 2>&1; then
        HEALTH_OK=true
        break
    fi
    sleep 2
done
if ! $HEALTH_OK; then
    echo ""
    echo "❌ Das Backend ist nach 120 s nicht bereit. Letzte Log-Zeilen:"
    $COMPOSE_CMD logs --tail=60 backend 2>&1 || true
    echo ""
    echo "   Meldet das Log 'Start abgebrochen – Schluessel ...', siehe INSTALL.md → Geheimnisse & Schlüssel"
    echo "   bzw. https://pdns-manager.gemtecgames.com/docs/features/verschluesselung/"
    echo "   Rollback: Dump einspielen und auf die vorherige Version wechseln (INSTALL.md → Updates)."
    exit 1
fi

# Zusatzinfos des lokalen /health (nur ueber Loopback sichtbar): Verschluesselung, Migrationsfehler,
# nicht geladene Server. Aeltere Versionen liefern die Felder nicht – dann bleibt es still.
HEALTH_INFO=$($COMPOSE_CMD exec -T backend python -c "import json,urllib.request as u
d=json.load(u.urlopen('http://127.0.0.1:8000/health',timeout=5))
s=d.get('secrets') or {}
print('mode='+str(s.get('mode') or ''))
print('unreadable='+str(s.get('unreadable_values') or 0))
print('migration_errors='+str(len(d.get('migration_errors') or [])))
print('not_loaded='+','.join(sorted((d.get('servers_not_loaded') or {}).keys())))" 2>/dev/null || true)
HEALTH_MODE=$(printf '%s\n' "$HEALTH_INFO" | sed -n 's/^mode=//p')
HEALTH_UNREADABLE=$(printf '%s\n' "$HEALTH_INFO" | sed -n 's/^unreadable=//p')
HEALTH_MIGERR=$(printf '%s\n' "$HEALTH_INFO" | sed -n 's/^migration_errors=//p')
HEALTH_NOTLOADED=$(printf '%s\n' "$HEALTH_INFO" | sed -n 's/^not_loaded=//p')
if [ "$HEALTH_MODE" = "plaintext_fallback" ]; then
    echo "⚠️  Geheimnisse werden NICHT verschlüsselt (Schlüsseldatei nicht beschreibbar). Beheben:"
    echo "   $COMPOSE_CMD exec -u root backend chown -R 1001:1001 /app/data && $COMPOSE_CMD restart backend"
fi
if [ -n "$HEALTH_UNREADABLE" ] && [ "$HEALTH_UNREADABLE" != "0" ]; then
    echo "⚠️  $HEALTH_UNREADABLE gespeicherte Geheimnisse sind nicht lesbar – Einstellungen → Sicherheit zeigt, welche."
fi
if [ -n "$HEALTH_MIGERR" ] && [ "$HEALTH_MIGERR" != "0" ]; then
    echo "⚠️  $HEALTH_MIGERR Datenbank-Migrationsschritte sind fehlgeschlagen – Details: $COMPOSE_CMD logs backend"
fi
if [ -n "$HEALTH_NOTLOADED" ]; then
    echo "⚠️  Nicht geladene PowerDNS-Server (API-Key fehlt/unlesbar): $HEALTH_NOTLOADED – unter Einstellungen → Server neu eintragen."
fi

echo "✅ App-Update erfolgreich abgeschlossen!"

# ----------------------------------------------------------------------------
# Schluessel fuer gespeicherte Geheimnisse sichern (nur bei neuem Fingerprint) [S7]
# ----------------------------------------------------------------------------
KEY_BACKUP_FILE=""
if ! $SKIP_KEY_BACKUP && [ "$HEALTH_MODE" = "encrypted" ]; then
    echo ""
    backup_secret_key ask || true
fi

if is_major_30_jump; then
    echo ""
    echo "📋 Nach dem Update auf $VERSION_AFTER bitte prüfen:"
    echo "   • Einstellungen → Sicherheit: Verschlüsselung aktiv, Schlüssel gesichert (Fingerprint vergleichen)."
    if [ -n "$KEY_BACKUP_FILE" ]; then
        echo "   • Schlüssel-Kopie: $KEY_BACKUP_FILE – getrennt vom Stack-Ordner und vom DB-Dump sichern."
    fi
    if [ -n "$BACKUP_DONE" ]; then
        echo "   • Der Dump $BACKUP_DONE enthält die Geheimnisse noch im KLARTEXT –"
        echo "     sicher verwahren oder löschen, sobald 3.0 läuft."
    fi
    echo "   • Panel-Tokens prüfen und bei Bedarf einschränken; Webhook-Empfänger erhalten jetzt wirklich Zustellungen."
    if [ -f .env ] && ! grep -qiE '^TRUST_PROXY_HEADERS=(true|1|yes|on)[[:space:]]*$' .env; then
        echo "   • Hinter einem Reverse-Proxy: TRUST_PROXY_HEADERS=true in die .env (DynDNS, Login-Sperren, Audit-IP),"
        echo "     danach $COMPOSE_CMD up -d."
    fi
    echo "   • Vollständige Prüfliste: INSTALL.md → „Direkt nach dem Update“."
fi

# ----------------------------------------------------------------------------
# Status der Compose-Services anzeigen (generisch, ohne Container-Namen-Filter)
# ----------------------------------------------------------------------------
$COMPOSE_CMD ps --format "table {{.Service}}\t{{.Status}}" 2>/dev/null \
    || $DOCKER_CMD ps --format "table {{.Names}}\t{{.Status}}"
