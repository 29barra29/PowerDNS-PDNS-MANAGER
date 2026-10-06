# PDNS Manager – Installation und Betrieb

Diese Anleitung beschreibt Installation, Konfiguration, Updates, Backup und Fehlersuche für **PDNS Manager 3.0**.
Den Schnellüberblick gibt das [README](README.md), ausführliche Feature-Anleitungen stehen auf der Doku-Seite
[pdns-manager.gemtecgames.com](https://pdns-manager.gemtecgames.com).

> **Update von 2.x auf 3.0?** Bitte zuerst den Abschnitt [Upgrade von 2.x auf 3.0](#upgrade-von-2x-auf-30) lesen.
> 3.0 verschlüsselt beim ersten Start alle gespeicherten Geheimnisse, ändert einige API-Verhalten und lässt einen
> Downgrade auf 2.4.x nur noch mit einem Dump von vorher oder nach `prepare-downgrade` zu.

## Inhalt

- [Schnellstart](#schnellstart)
- [Voraussetzungen](#voraussetzungen)
- [Manuelle Installation](#manuelle-installation)
- [Umgebungsvariablen](#umgebungsvariablen)
- [Geheimnisse & Schlüssel](#geheimnisse--schlüssel)
- [Sicherheits-Checkliste](#sicherheits-checkliste)
- [HTTPS mit Reverse-Proxy](#https-mit-reverse-proxy)
- [PowerDNS anbinden](#powerdns-anbinden)
- [Docker Compose anpassen](#docker-compose-anpassen)
- [Monitoring](#monitoring)
- [Updates](#updates)
- [Upgrade von 2.x auf 3.0](#upgrade-von-2x-auf-30)
- [Backup & Restore](#backup--restore)
- [Troubleshooting](#troubleshooting)
- [Tipps für den Produktivbetrieb](#tipps-für-den-produktivbetrieb)

---

## Schnellstart

### Option 1: Setup-Assistent (empfohlen)

```bash
git clone https://github.com/29barra29/PowerDNS-PDNS-MANAGER.git
cd PowerDNS-PDNS-MANAGER
./setup.sh            # erzeugt die .env mit zufälligen Passwörtern und Schlüsseln (chmod 600)
docker compose up -d
# Browser: http://localhost:5380
```

`setup.sh` erzeugt `JWT_SECRET_KEY`, beide Datenbank-Passwörter und `SECRET_ENCRYPTION_KEY` (Schlüssel für die
verschlüsselten Geheimnisse). Eine vorhandene `.env` wird vorher als `.env.backup.<Zeitstempel>` gesichert; ein
bereits vorhandener `SECRET_ENCRYPTION_KEY` (sowie `SECRET_ENCRYPTION_KEY_PREVIOUS`/`_FILE`) wird übernommen.

### Option 2: One-Liner

```bash
curl -sSLO https://raw.githubusercontent.com/29barra29/PowerDNS-PDNS-MANAGER/main/install.sh && bash install.sh
```

`install.sh` holt das neueste Release, erzeugt eine `.env` (inklusive `SECRET_ENCRYPTION_KEY`) und startet die
Container.

---

## Voraussetzungen

- Docker mit Compose-Plugin (`docker compose`), Linux, macOS oder Windows mit WSL2, mindestens 1 GB RAM.
- Freier Port `5380` (änderbar über `HOST_PORT` in der `.env`).
- Ein oder mehrere **PowerDNS Authoritative 4.x** mit aktivierter HTTP-API (siehe [PowerDNS anbinden](#powerdns-anbinden)).
- Datenbank: Der Stack bringt **MariaDB 11** mit (Container `dns-manager-db`). Wer eine eigene Datenbank nutzt, braucht
  **MariaDB ab 10.6 oder MySQL ab 8.0** (die Webhook-Warteschlange nutzt `SELECT … FOR UPDATE SKIP LOCKED`). Der
  Datenbank-Benutzer muss Tabellen anlegen und ändern dürfen (`CREATE`, `ALTER`): Das Backend migriert das Schema
  bei jedem Start selbst und startet nicht, wenn danach eine benötigte Spalte fehlt.
- Ausgehende Verbindungen des Backend-Containers: zur PowerDNS-API; je nach Nutzung zum SMTP-Server, zu
  Webhook-Zielen, zum OIDC-Anbieter bzw. LDAP-Server und – nur nach Freischaltung unter
  **Einstellungen → Monitoring** – DNS (UDP/TCP 53) zu öffentlichen Resolvern und den Nameservern der Zonen
  (Propagations-Check, DNSKEY- und Elternzonen-Prüfung).

---

## Manuelle Installation

```bash
git clone https://github.com/29barra29/PowerDNS-PDNS-MANAGER.git
cd PowerDNS-PDNS-MANAGER
cp .env.example .env
nano .env
chmod 600 .env
docker compose up -d
```

Mindestens diese Werte setzen:

```env
# PFLICHT: beide DB-Passwörter (z. B. openssl rand -base64 32). Bleiben sie leer,
# bricht docker compose absichtlich mit einer Fehlermeldung ab.
DB_ROOT_PASSWORD=...
DB_PASSWORD=...

# Empfohlen: fester Schlüssel für die Session-Tokens (openssl rand -hex 64).
# Leer = das Backend erzeugt einmalig /app/data/.jwt_secret im Volume backend_data.
JWT_SECRET_KEY=...

# Empfohlen: Schlüssel für die verschlüsselten Geheimnisse (44 Zeichen):
#   openssl rand -base64 32 | tr '+/' '-_'
# Leer = das Backend erzeugt beim ersten Start /app/data/.secret_key im Volume backend_data.
# Getrennt vom Datenbank-Backup sichern, siehe "Geheimnisse & Schlüssel".
SECRET_ENCRYPTION_KEY=...

# Erster Admin: entweder Einrichtung im Browser ...
ENABLE_REGISTRATION=true
# ... oder festes Passwort für den Benutzer "admin"
# ENABLE_REGISTRATION=false
# INITIAL_ADMIN_PASSWORD=dein-sicheres-passwort

# Auf true, sobald HTTPS über einen Reverse-Proxy läuft
AUTH_COOKIE_SECURE=false
```

### Erster Login

- **Mit `ENABLE_REGISTRATION=true`:** http://localhost:5380 öffnen, der Einrichtungsassistent legt den ersten
  Benutzer als Admin an. Danach schaltet sich die Registrierung selbst ab.
- **Mit `INITIAL_ADMIN_PASSWORD`:** Benutzer `admin` mit diesem Passwort. Den Wert nach dem ersten Login aus der
  `.env` entfernen.
- **Ohne beides:** Das Backend erzeugt ein Zufallspasswort für `admin` und legt es in
  `/app/.initial-admin-password` im Container ab:
  ```bash
  docker compose exec backend cat /app/.initial-admin-password
  ```
  Die Datei nach dem ersten Login löschen. Kann sie nicht geschrieben werden, steht das Passwort einmalig im
  Container-Log (`docker compose logs backend | grep -i "initial admin"`).

---

## Umgebungsvariablen

Die Variablen der ersten Tabellen setzt du in der `.env`; die `compose.yaml` reicht genau diese an die Container
durch. Die `compose.yaml` selbst bitte nicht editieren – lokale Änderungen an versionierten Dateien lassen
`./update.sh` beim `git checkout` scheitern. Variablen, die die `compose.yaml` nicht durchreicht, lassen sich über eine
`compose.override.yaml` setzen (siehe [Docker Compose anpassen](#docker-compose-anpassen)). Nach einer Änderung an der
`.env` genügt `docker compose up -d`.

**Datenbank und Ports**

| Variable | Standard | Bedeutung |
|---|---|---|
| `DB_ROOT_PASSWORD` | – (Pflicht) | Root-Passwort der mitgelieferten MariaDB; auch für `./update.sh` (Dump) |
| `DB_PASSWORD` | – (Pflicht) | Passwort des App-Benutzers |
| `DB_NAME` | `dns_manager` | Datenbankname |
| `DB_USER` | `dns_admin` | Datenbank-Benutzer |
| `BIND_ADDR` | `0.0.0.0` | Host-Adresse des Ports; hinter einem Reverse-Proxy auf demselben Host `127.0.0.1` |
| `HOST_PORT` | `5380` | Host-Port des Panels |

**App**

| Variable | Standard | Bedeutung |
|---|---|---|
| `APP_NAME` | `PDNS Manager` | Anzeigename |
| `LOG_LEVEL` | `info` | Log-Level des Backends |
| `DEFAULT_LANGUAGE` | `de` | Standardsprache der Oberfläche (gilt nur, solange im Browser keine eigene Sprachwahl gespeichert ist) und der E-Mails (Deutsch oder Englisch), wenn im Profil keine Sprache gewählt ist |
| `INSTALL_PATH` | leer | Projektpfad auf dem Host (Anzeige unter Einstellungen → Updates); setzen `install.sh`/`setup.sh` |
| `PDNS_SERVERS` | leer | `name\|url\|api_key[,name\|url\|api_key]` – wird nur importiert, solange noch kein Server in der Datenbank steht; danach im Panel pflegen |

**Anmeldung und Sitzung**

| Variable | Standard | Bedeutung |
|---|---|---|
| `ENABLE_REGISTRATION` | `false` | Einrichtungsassistent für den ersten Admin |
| `INITIAL_ADMIN_PASSWORD` | leer | Passwort für den automatisch angelegten Benutzer `admin` |
| `JWT_SECRET_KEY` | leer | Schlüssel der Session-Tokens; leer = `/app/data/.jwt_secret`. Ein Wechsel meldet alle Benutzer ab |
| `AUTH_COOKIE_SECURE` | `false` | `true`, sobald HTTPS läuft (Cookie nur über HTTPS; setzt zusätzlich HSTS) |
| `AUTH_COOKIE_SAMESITE` | `lax` | SameSite des Session-Cookies; die kurzlebigen Anmelde-Cookies (OIDC, 2FA-Zwischenschritt) sind immer `lax` |
| `AUTH_COOKIE_MAX_AGE` | `2592000` (30 Tage) | Sitzungsdauer in Sekunden; die Token-Laufzeit folgt diesem Wert. Mit SSO 8–24 h (28800–86400) empfohlen |
| `WEBAUTHN_RP_ID` | leer | Domain, an die Passkeys gebunden werden (ohne Schema/Port); leer = aus der Browser-Adresse |
| `WEBAUTHN_RP_NAME` | leer | Anzeigename im Passkey-Dialog (leer = `APP_NAME`) |
| `WEBAUTHN_ORIGIN` | leer | Erlaubte Origins mit Schema (Komma-Liste); Pflicht, wenn `WEBAUTHN_RP_ID` nicht dem Panel-Host entspricht. Ersatz für die öffentliche Basis-URL, solange im Panel keine gesetzt ist |

**Geheimnisse** (Details: [Geheimnisse & Schlüssel](#geheimnisse--schlüssel))

| Variable | Standard | Bedeutung |
|---|---|---|
| `SECRET_ENCRYPTION_KEY` | leer | Fernet-Schlüssel (44 Zeichen). Leer = Schlüsseldatei `/app/data/.secret_key` |
| `SECRET_ENCRYPTION_KEY_PREVIOUS` | leer | Frühere Schlüssel (Komma-Liste), nur zum Entschlüsseln während eines Schlüsselwechsels |
| `SECRET_ENCRYPTION_KEY_FILE` | leer | Schlüssel aus einer Datei lesen (z. B. Docker-Secret); die Datei muss existieren, es wird dann nie ein Schlüssel erzeugt |

**Netz und Reverse-Proxy**

| Variable | Standard | Bedeutung |
|---|---|---|
| `TRUST_PROXY_HEADERS` | `false` | Echte Client-IP aus `X-Forwarded-For`/`X-Real-IP` übernehmen (Login-Sperren, Audit-Log, DynDNS). Nur setzen, wenn das Backend ausschließlich über den Proxy erreichbar ist |
| `TRUSTED_PROXY_HOPS` | `1` | Anzahl vertrauenswürdiger Proxys vor dem Backend (z. B. `2` bei Cloudflare vor nginx) |
| `ALLOWED_ORIGINS` | leer | Zusätzliche Origins für CORS und den CSRF-Schutz (Komma-Liste, mit Schema) |
| `WEBHOOK_ALLOW_PRIVATE_URLS` | `false` | Webhook-Ziele in privaten/internen Netzen erlauben (SSRF-Schutz abschalten) |

**Betrieb**

| Variable | Standard | Bedeutung |
|---|---|---|
| `BACKGROUND_WORKERS_ENABLED` | `true` | Hintergrund-Aufgaben: Webhook-Zustellung und stündliche Bereinigung des Audit-Logs. `false` = Webhook-Ereignisse werden nur gesammelt, nicht gesendet. Es darf nur **ein** Backend-Prozess die Warteschlange abarbeiten |
| `METRICS_TOKEN` | leer | Fester Scrape-Token für `/metrics` (mindestens 24 Zeichen). Gesetzt = `/metrics` aktiv und die Metrik-Einstellung im Panel gesperrt |
| `DOCS_ENABLED` | `false` | Interaktive API-Doku unter `/docs`, `/redoc`, `/openapi.json` |
| `SSO_ALLOW_INSECURE` | `false` | Unverschlüsselte Verbindungen zum Anmeldedienst (`http://`-Issuer, LDAP ohne TLS bzw. ohne Zertifikatsprüfung). **Nur für Testumgebungen** |

**Nur über `compose.override.yaml`** (von der `compose.yaml` nicht durchgereicht)

| Variable | Standard | Bedeutung |
|---|---|---|
| `CONTENT_SECURITY_POLICY` | im Code gesetzt | Eigener CSP-Header; leerer Wert schaltet den Header ab. Der Standard erlaubt die Captcha-Anbieter und den GitHub-Versionscheck, keine Google-Fonts-Hosts mehr (die Schrift liefert das Panel selbst aus) |
| `LOG_FORMAT` | `text` | `json` für strukturierte Log-Zeilen |
| `JWT_EXPIRE_MINUTES` | folgt `AUTH_COOKIE_MAX_AGE` | Token-Laufzeit abweichend vom Cookie |
| `DB_POOL_SIZE` | `10` | Größe des Datenbank-Pools |
| `JWT_SECRET_FILE` | `/app/data/.jwt_secret` | Ablageort des automatisch erzeugten JWT-Schlüssels |

**Für `./update.sh`** (Shell-Variable beim Aufruf, nicht in der `.env`)

| Variable | Standard | Bedeutung |
|---|---|---|
| `PDNSMGR_KEY_BACKUP_DIR` | `$HOME/.pdnsmgr-keys` | Ablage der Schlüsselkopie, z. B. `PDNSMGR_KEY_BACKUP_DIR=/srv/pdns-keys ./update.sh`. Muss außerhalb des Stack-Ordners liegen |

Einstellungen wie SMTP, Captcha, SSO, DynDNS, PTR-Standard, LUA-Records, Propagations-Check, Prometheus-Token,
Aufbewahrung des Audit-Logs und die öffentliche Basis-URL werden im Panel gepflegt, nicht in der `.env`.

---

## Geheimnisse & Schlüssel

### Was verschlüsselt gespeichert wird

Ab 3.0 liegen alle umkehrbar gespeicherten Geheimnisse nur noch verschlüsselt (`enc:v1:`, Fernet) in der Datenbank:

| Feld | Inhalt |
|---|---|
| `server_configs.api_key` | PowerDNS-API-Keys |
| `webhooks.secret`, `webhooks.url` | Webhook-Secrets und Ziel-URLs (bei Slack, Teams oder Discord ist die URL selbst das Geheimnis) |
| `users.totp_secret`, `users.totp_pending_secret` | 2FA-Geheimnisse |
| `system_settings.smtp_password` | SMTP-Passwort |
| `system_settings.captcha_secret_key` | Captcha-Secret |
| `system_settings.oidc_client_secret`, `system_settings.ldap_bind_password` | SSO-Secrets |
| `system_settings.metrics_token` | Prometheus-Scrape-Token aus dem Panel |

Passwörter werden weiterhin nur als bcrypt-Hash gespeichert, Panel-, ACME- und DynDNS-Tokens nur als SHA-256-Hash.
Ein Datenbank-Dump ohne den Schlüssel enthält die Geheimnisse also nicht mehr im Klartext – **mit** dem Schlüssel
schon. Deshalb gehören Dump und Schlüssel in getrennte Backups.

### Woher der Schlüssel kommt

Das Backend sucht beim Start in dieser Reihenfolge:

1. `SECRET_ENCRYPTION_KEY` aus der `.env` (empfohlen; `setup.sh` und `install.sh` setzen ihn bei Neuinstallationen).
2. Sonst eine Schlüsseldatei: der Pfad aus `SECRET_ENCRYPTION_KEY_FILE` (die Datei muss existieren, es wird nie ein
   Schlüssel erzeugt) oder – ohne diese Variable – `/app/data/.secret_key` im Volume `backend_data`. Diese Datei
   erzeugt das Backend beim ersten Start (Rechte 0600), solange es noch keine verschlüsselten Werte gibt.
   Bestandsinstallationen ohne `SECRET_ENCRYPTION_KEY` landen nach dem Update auf 3.0 in diesem Fall.

Fehlt der Schlüssel, obwohl verschlüsselte Werte in der Datenbank liegen, erzeugt das Backend **keinen** neuen,
sondern bricht den Start ab (siehe [Start abgebrochen](#start-abgebrochen)).

### Schlüssel sichern

- **Schlüssel in der `.env`:** die `.env` getrennt vom Datenbank-Dump sichern (z. B. Passwortmanager).
- **Schlüssel nur im Docker-Volume:** `./update.sh` legt nach dem Start eine Kopie an – beim ersten Lauf und immer,
  wenn sich der Schlüssel (Fingerprint) geändert hat. Jederzeit von Hand (das Backend muss laufen):
  ```bash
  ./update.sh --backup-key-only
  ```
  Ziel ist `${PDNSMGR_KEY_BACKUP_DIR:-$HOME/.pdnsmgr-keys}/<stack>-<fingerprint>.key` (Ordner 0700, Datei 0600).
  Das Skript legt die Kopie nie im Stack-Ordner und nie neben dem Dump ab. Dieses **Schlüssel-Backup-Verzeichnis**
  gehört in ein eigenes Backup, getrennt vom Stack-Ordner und vom Datenbank-Dump.
- **Schlüssel aus `SECRET_ENCRYPTION_KEY_FILE`:** die angegebene Datei selbst sichern.

Den Fingerprint (12 Hex-Zeichen, kein Geheimnis) zeigt **Einstellungen → Sicherheit**; er steht auch im Dateinamen
der Kopie und lässt sich so abgleichen.

### Status prüfen

- **Einstellungen → Sicherheit** (Admin): Statuskarte mit Modus, Schlüsselquelle, Fingerprint, Zusatzschlüsseln,
  Zählern je Feld und der Liste nicht entschlüsselbarer Einträge mit Sprung zum Neu-Eintragen.
- Ein **roter Hinweis über jeder Seite** (Admins) meldet nicht entschlüsselbare Geheimnisse oder einen Start ohne
  Verschlüsselung.
- Kommandozeile im laufenden Container:
  ```bash
  docker compose exec backend python -m app.cli.secrets status
  ```

### Schlüssel wechseln

1. Neuen Schlüssel erzeugen: `openssl rand -base64 32 | tr '+/' '-_'`
   (oder `docker compose exec backend python -m app.cli.secrets generate-key`).
2. In der `.env` den neuen Wert als `SECRET_ENCRYPTION_KEY` und den bisherigen als `SECRET_ENCRYPTION_KEY_PREVIOUS`
   eintragen. Lag der bisherige Schlüssel nur in `/app/data/.secret_key`, genügt der neue `SECRET_ENCRYPTION_KEY` –
   die vorhandene Datei dient beim nächsten Start automatisch als Zusatzschlüssel.
3. `docker compose up -d`: Beim Start wird alles mit dem neuen Schlüssel neu verschlüsselt.
4. Wenn **Einstellungen → Sicherheit** meldet, dass der Zusatzschlüssel nicht mehr benötigt wird,
   `SECRET_ENCRYPTION_KEY_PREVIOUS` entfernen. Die neue `.env` sichern.

### Schlüssel verloren

Ohne Schlüssel sind die verschlüsselten Werte verloren; der Rest der Datenbank bleibt nutzbar. Zurücksetzen der
nicht lesbaren Werte:

```bash
docker compose stop backend
docker compose run --rm --name pdnsmgr-secrets-cli backend python -m app.cli.secrets reset-unreadable        # Trockenlauf
docker compose run --rm --name pdnsmgr-secrets-cli backend python -m app.cli.secrets reset-unreadable --yes
docker compose up -d
```

Danach: PowerDNS-API-Keys unter **Einstellungen → DNS-Server** neu eintragen und die Zonen dieser Server mit den
anderen abgleichen (der Tab „Propagation“ einer Zone zeigt Abweichungen; einen automatischen Abgleich gibt es
nicht), SMTP-Passwort und Captcha-Secret neu setzen, Webhooks neu eintragen und aktivieren (sie werden deaktiviert),
SSO-Secrets und Prometheus-Token neu setzen. Betroffene Benutzer richten 2FA neu ein.

### Kommandozeile `python -m app.cli.secrets`

Schreibende Kommandos nur bei gestopptem Backend und über `docker compose run --rm --name pdnsmgr-secrets-cli backend …`.
Ohne `--yes` laufen sie als Trockenlauf. Die CLI gibt nie Werte, Chiffretexte oder Schlüssel aus.

| Kommando | Zweck | Exit-Codes |
|---|---|---|
| `status [--json]` | wie die Statuskarte | 0; 1 bei Datenbankfehler |
| `generate-key` | neuen Schlüssel ausgeben | 0 |
| `key-info` | Schlüsselquelle, Pfad und Fingerprint ohne Datenbank (nutzt `update.sh`) | 0; 1 bei ungültigem `SECRET_ENCRYPTION_KEY` |
| `reset-unreadable [--yes]` | nicht lesbare Werte zurücksetzen (siehe oben) | 0; 3 = nichts zu tun; 1 bei Fehlern |
| `prepare-downgrade [--yes] [--force]` | Downgrade auf 2.4.x vorbereiten (siehe [Downgrade](#downgrade-auf-24x)) | 0; 1 ohne Schlüssel; 2 bei nicht lesbaren Werten ohne `--force` |
| `decrypt-all [--yes] [--force]` | alle lesbaren Geheimnisse zurück in Klartext (Spezialfall; für einen Downgrade `prepare-downgrade` verwenden) | wie `prepare-downgrade` |

---

## Sicherheits-Checkliste

**Pflicht**
- [ ] `.env` nur für den eigenen Benutzer lesbar (`chmod 600 .env`).
- [ ] Admin-Passwort nach dem ersten Login geändert, `INITIAL_ADMIN_PASSWORD` aus der `.env` entfernt.
- [ ] `JWT_SECRET_KEY` und `SECRET_ENCRYPTION_KEY` gesetzt (bzw. Schlüsselkopie mit `./update.sh --backup-key-only`
      angelegt) und **getrennt** vom Datenbank-Backup gesichert.
- [ ] Port 5380 nicht öffentlich: `BIND_ADDR=127.0.0.1` in der `.env` (eine Host-Firewall greift bei Docker-Ports nicht).

**Empfohlen**
- [ ] HTTPS über einen Reverse-Proxy, danach `AUTH_COOKIE_SECURE=true` und `TRUST_PROXY_HEADERS=true`.
- [ ] `ENABLE_REGISTRATION=false`, sobald der erste Admin angelegt ist.
- [ ] API-Tokens auf die nötigen Zonen beschränken, wo möglich nur Leserecht, immer mit Laufzeit.
- [ ] `/metrics` im Proxy nur für den Prometheus-Server freigeben (falls genutzt).
- [ ] Aufbewahrung des Audit-Logs festlegen (Protokoll → „Aufbewahrung“, Standard unbegrenzt).
- [ ] Regelmäßige Backups (siehe [Backup & Restore](#backup--restore)), Fail2Ban auf die Proxy-Logs.

---

## HTTPS mit Reverse-Proxy

Das Panel bringt kein TLS mit. Davor gehört ein Reverse-Proxy (Caddy, nginx, Traefik, Cloudflare Tunnel).

### nginx

```nginx
server {
    listen 443 ssl http2;
    server_name dns.example.com;

    ssl_certificate     /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    # Prometheus: /metrics nur für den Prometheus-Server
    location = /metrics {
        allow 192.0.2.10;    # IP des Prometheus-Servers
        deny  all;
        proxy_pass http://127.0.0.1:5380;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # Alles andere, inklusive /nic/update (DynDNS) und /api/v1/auth/oidc/callback (SSO)
    location / {
        proxy_pass http://127.0.0.1:5380;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Was jeder Proxy erfüllen muss

- **`TRUST_PROXY_HEADERS=true`** in der `.env` (bei einem weiteren Proxy davor, z. B. Cloudflare vor nginx, zusätzlich
  `TRUSTED_PROXY_HOPS=2`). Sonst sieht das Backend für alle Anfragen nur die Proxy-IP: Login-Sperren treffen alle
  Benutzer gemeinsam (25 Fehlversuche je IP in 15 Minuten), das Audit-Log zeigt die Proxy-IP, und DynDNS erkennt die
  Router-IP nicht. Läuft das Panel erkennbar hinter einem Proxy ohne diese Einstellung, zeigt es Admins einen gelben
  Hinweis.
- **Port 5380 nicht öffentlich** (`BIND_ADDR=127.0.0.1`), damit niemand die Proxy-Header am Proxy vorbei fälschen kann.
- **`Host`-Header durchreichen** (`proxy_set_header Host $host`; bei Traefik und Caddy Standard). Sonst lehnt der
  CSRF-Schutz zustandsändernde Anfragen aus dem Browser ab. Für abweichende Hostnamen `ALLOWED_ORIGINS` setzen.
- **`Authorization`-Header nicht protokollieren.** Er enthält bei DynDNS (Basic-Auth) und bei API-Aufrufen den Token.

### DynDNS hinter dem Proxy

- `/nic/update` und `/api/v1/dyndns/…` müssen durchgereicht werden. Wer im Proxy nur bestimmte Pfade freigibt, muss
  diese ergänzen.
- Eine Anmeldung im Proxy (Basic-Auth vor dem Panel) darf diese Pfade nicht abdecken: Router senden den DynDNS-Token
  selbst im `Authorization`-Header.
- Ohne `TRUST_PROXY_HEADERS` sieht die automatische IP-Erkennung nur die (meist private) Proxy-Adresse; das Update
  scheitert dann mit `badip`, solange der Router die IP nicht per `myip` mitschickt.
- Die öffentliche Basis-URL (**Einstellungen → Profil**, nur Admins) setzt die DynDNS-Anleitung im Panel in ihre
  Beispiele ein.

### Prometheus

`/metrics` ist ohne Prometheus-Konfiguration aus (404). Wird er eingeschaltet, im Proxy nur für den
Prometheus-Server freigeben (Beispiel oben). Der Scrape-Token gehört in eine Datei mit Rechten 600.

### Single Sign-On (OIDC)

- Zuerst die öffentliche Basis-URL setzen (**Einstellungen → Profil**, z. B. `https://dns.example.com`).
- Beim Anbieter als Redirect-URI eintragen: `https://<host>/api/v1/auth/oidc/callback`. Der Tab
  **Einstellungen → Anmeldung / SSO** zeigt die exakte URI zum Kopieren.
- Der Proxy muss diesen Pfad durchreichen (im Beispiel oben über `location /`).
- Für LDAP die CA des Verzeichnisservers bereithalten (nur LDAPS oder StartTLS mit Zertifikatsprüfung).
- Notfallzugang bei abgeschalteter lokaler Anmeldung: `https://<host>/login?local=1` (nur Admins).

### Traefik

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.dns-manager.rule=Host(`dns.example.com`)"
  - "traefik.http.routers.dns-manager.tls=true"
  - "traefik.http.routers.dns-manager.tls.certresolver=letsencrypt"
```

Labels gehören in eine `compose.override.yaml` (siehe unten), nicht in die `compose.yaml`.

### Cloudflare Tunnel

```bash
cloudflared tunnel create dns-manager
cloudflared tunnel route dns --tunnel-id [TUNNEL_ID] dns.example.com
cloudflared tunnel run --url http://localhost:5380 dns-manager
```

Mit Cloudflare als Proxy vor einem eigenen nginx: `TRUSTED_PROXY_HOPS=2`.

---

## PowerDNS anbinden

### PowerDNS vorbereiten

```ini
# /etc/powerdns/pdns.conf
api=yes
api-key=dein-sicherer-api-key
webserver=yes
webserver-address=0.0.0.0
webserver-port=8081
webserver-allow-from=127.0.0.1,192.0.2.0/24   # Adresse(n), von denen das Panel zugreift
# enable-lua-records=yes   # nur wenn LUA-/Geo-Records genutzt werden
```

Danach `systemctl restart pdns`.

LUA-Records (dynamische Antworten, Failover, Geo-Funktionen) brauchen `enable-lua-records=yes` (oder `shared`) in der
`pdns.conf` **jedes** Servers, der die Zone ausliefert; für Geo-Funktionen zusätzlich das geoip-Backend mit
GeoIP-Datenbank und `edns-subnet-processing=yes`. Wer LUA-Records im Panel anlegen darf, steuert
**Einstellungen → DNS-Optionen → LUA-Records** (Standard: nur Admins). Anleitung:
[LUA- & Geo-Records](https://pdns-manager.gemtecgames.com/docs/features/lua-geo/).

### Server im Panel eintragen

1. Als Admin anmelden, **Einstellungen → DNS-Server → Server hinzufügen**.
2. Name (z. B. `server1`), URL (z. B. `http://pdns-server:8081`) und API-Key eintragen.
3. **Verbindung testen**, speichern.

Mehrere Server sind möglich. Mit **„Speichern: Ja/Nein“** je Server wird festgelegt, ob das Panel dorthin schreibt.
Schreibvorgänge laufen zuerst auf den Server aus der Ansicht und danach auf alle weiteren schreibbaren Server, die die
Zone führen. Ändert sich die URL eines Servers, muss der API-Key neu eingegeben werden.

---

## Docker Compose anpassen

Eigene Anpassungen gehören in eine `compose.override.yaml` im Projektordner. Docker Compose liest sie automatisch
zusätzlich zur `compose.yaml`; `./update.sh` lässt sie unberührt.

### Eigene DNS-Resolver

Der Backend-Container nutzt die DNS-Auflösung des Docker-Hosts (bis 2.4.x waren `8.8.8.8`/`8.8.4.4` fest
eingetragen). Wer andere Resolver braucht:

```yaml
# compose.override.yaml
services:
  backend:
    dns:
      - 192.0.2.53
      - 192.0.2.54
```

Interne Namen (PowerDNS-, SMTP-, LDAP-Server) müssen über diese Resolver auflösbar bleiben.

### Weitere Variablen und eigenes Netz

```yaml
# compose.override.yaml
services:
  backend:
    environment:
      LOG_FORMAT: json
      CONTENT_SECURITY_POLICY: "default-src 'self'; ..."
    networks:
      - dns-manager-net
      - mein-netz

networks:
  mein-netz:
    external: true
```

Danach `docker compose up -d`.

---

## Monitoring

- **`GET /health`** (ohne Anmeldung): `status` (`healthy`, `degraded` oder `unhealthy`), `database` und je
  PowerDNS-Server `healthy`/`unreachable`. HTTP 503 nur, wenn die Datenbank nicht erreichbar ist; `degraded` (HTTP 200),
  wenn ein PowerDNS-Server nicht antwortet oder die Verschlüsselung nicht aktiv ist. Weitere Angaben (Verschlüsselung,
  Migrationsfehler, nicht geladene Server, Hintergrund-Aufgaben) gibt es nur bei Abfrage aus dem Container selbst
  (`127.0.0.1`), z. B. für `update.sh`. Geeignet für Uptime-Checks.
- **Prometheus `GET /metrics`:** aus, bis ein Scrape-Token besteht. Entweder unter **Einstellungen → Monitoring →
  Prometheus-Metriken** einschalten und einen Token erzeugen (wird einmal angezeigt und verschlüsselt gespeichert) oder
  `METRICS_TOKEN` in der `.env` setzen (dann ist die Panel-Einstellung gesperrt). Beispiel:
  ```yaml
  # prometheus.yml
  scrape_configs:
    - job_name: pdns-manager
      scheme: https
      metrics_path: /metrics
      authorization:
        type: Bearer
        credentials_file: /etc/prometheus/pdns-manager.token   # Rechte 600
      static_configs:
        - targets: ["dns.example.com"]
  ```
  Die Metriken sind in [docs/PANEL-API.md](docs/PANEL-API.md#metriken) beschrieben.
- **Systemstatus:** **Einstellungen → Monitoring** zeigt Hintergrund-Aufgaben, fehlgeschlagene Migrationen, nicht
  geladene PowerDNS-Server und den Zustand der Verschlüsselung (Admins mit Browser-Anmeldung).

---

## Updates

### Automatisch mit `update.sh`

Voraussetzung: Projektordner mit Git (nach `install.sh` oder `git clone`).

```bash
cd /pfad/zu/PowerDNS-PDNS-MANAGER
./update.sh
```

Ablauf:

1. Warnung bei lokal geänderten versionierten Dateien (z. B. editierte `compose.yaml`), dann `git fetch` mit Tags und
   Wechsel auf das neueste Release-Tag `v*` (bzw. `git pull`, wenn `main` ausgecheckt ist).
2. Bei einem Major-Sprung (z. B. 2.x → 3.x) eine Warnbox mit den wichtigsten Änderungen und die Frage
   „Trotzdem fortfahren? (j/y/n)“.
3. Angebot eines Datenbank-Dumps `backup_<version>_<zeitstempel>.sql` im Projektordner (Rechte 0600).
4. Image bauen (`--no-cache` nur bei Versionswechsel oder `--rebuild`), Rechte von `/app/data` und Uploads für den
   App-Benutzer korrigieren, `docker compose up -d`.
5. Bis zu 120 Sekunden auf das Backend warten. Startet es nicht, zeigt das Skript die letzten Log-Zeilen und endet
   mit Exit-Code 1.
6. Hinweise zu nicht aktiver Verschlüsselung, nicht lesbaren Geheimnissen, Migrationsfehlern und nicht geladenen
   Servern; danach die Schlüsselkopie (siehe [Schlüssel sichern](#schlüssel-sichern)).

| Schalter | Wirkung |
|---|---|
| `--rebuild` | Image immer ohne Cache bauen |
| `--no-backup` | keine Frage nach dem Datenbank-Dump |
| `--skip-fetch` | kein `git fetch`/Wechsel (nur neu bauen und starten) |
| `--no-key-backup` | keine Schlüsselkopie anlegen |
| `--backup-key-only` | nur den Schlüssel des laufenden Backends sichern, kein Update |
| `--help` | Kurzhilfe |

### Manuell

```bash
( umask 077; docker exec dns-manager-db sh -c 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb-dump --single-transaction -u root dns_manager' > backup_$(date +%Y%m%d).sql )   # aeltere Images: mysqldump statt mariadb-dump
git fetch origin --tags --force --prune
git checkout v3.0.0                 # gewünschtes Release-Tag, oder: git checkout main && git pull
docker compose build --no-cache backend
docker compose up -d
```

---

## Upgrade von 2.x auf 3.0

3.0 ist ein Major-Release. Die Datenbank wird beim ersten Start automatisch migriert; einige Änderungen betreffen
Skripte, Reverse-Proxy und Backups. Die vollständige Liste steht im
[Changelog im README](README.md#v300--major); die API-Änderungen in [docs/PANEL-API.md](docs/PANEL-API.md).

### Vor dem Update

- **Dump anlegen** (`./update.sh` bietet ihn an, Rechte 0600). Dieser Dump enthält die Geheimnisse noch **im
  Klartext** – sicher verwahren und löschen, sobald 3.0 läuft und ein neuer Dump existiert.
- **Eigene Datenbank:** MariaDB ab 10.6 oder MySQL ab 8.0; der Datenbank-Benutzer braucht `ALTER TABLE`.
- **Webhook-Empfänger prüfen:** In 2.3.7 bis 2.4.x wurden Webhooks wegen eines Fehlers nie verschickt. Ab 3.0 wird
  wirklich zugestellt – mit Payload Version 2 und den neuen Headern `X-DNS-Manager-Event`, `X-DNS-Manager-Delivery`
  und `X-DNS-Manager-Attempt`.
- **Skripte prüfen:** `/api/v1/settings/*`, die Token-Verwaltung und die Webhook-Verwaltung gehen nur noch mit einer
  Browser-Anmeldung, nicht mehr per API-Token (403). Weitere Änderungen: Abschnitt „Breaking“ im Changelog.

### Was beim ersten Start automatisch passiert

- Schema-Migration (neue Tabellen und Spalten, u. a. `users.sessions_revoked_at`); wiederholbar, weitere Starts
  ändern nichts. Fehlt danach eine Spalte (z. B. ohne `ALTER`-Recht), bricht der Start mit einer klaren Meldung ab.
- Alle Klartext-Geheimnisse werden verschlüsselt. Ohne `SECRET_ENCRYPTION_KEY` erzeugt das Backend einmalig
  `/app/data/.secret_key` im Volume `backend_data`.
- Alte Audit-Einträge bekommen die Zone nachgetragen.
- Bestehende API-Tokens von Admins erhalten die Freigabe „Admin-Funktionen“ (`allow_admin`) und funktionieren wie
  bisher; alle Bestandstokens gelten danach für alle Zonen ohne Ablauf (Kennzeichen „Weitreichend“). Unter 2.x
  gelöschte Tokens gelten als widerrufen.
- Alle Konten bleiben lokale Konten; OIDC, LDAP und die automatische Kontoanlage sind aus.
- Neue Einstellungen fehlen in der Datenbank und gelten mit Standardwert, z. B. LUA-Records „nur Administratoren“.

### Direkt nach dem Update

1. **Schlüssel sichern:** Einstellungen → Sicherheit öffnen. Steht dort „Schlüssel nur im Docker-Volume“:
   `./update.sh --backup-key-only` und das Schlüssel-Backup-Verzeichnis getrennt sichern; steht der Schlüssel in der
   `.env`, die `.env` getrennt sichern.
2. **Roter Hinweis „Geheimnisse nicht entschlüsselbar“:** die genannten Werte neu eintragen; bei PowerDNS-Servern
   danach die Zonen abgleichen.
3. **Einstellungen → Monitoring → Systemstatus:** keine Migrationsfehler, keine nicht geladenen Server.
4. **SMTP:** einmal „Verbindung testen“; erscheint „Passwort kann nicht entschlüsselt werden“, das Passwort neu
   eintragen.
5. **Webhooks:** je Webhook „Test senden“ und das Zustellprotokoll ansehen; Webhooks mit „URL unlesbar“ oder „Secret
   nicht lesbar“ neu eintragen; ältere, nicht mehr bekannte Ereignisfilter durch die Auswahl ersetzen.
6. **API-Tokens:** alle „Weitreichend“-Tokens auf die nötigen Zonen beschränken, Leserecht setzen, wo Schreiben nicht
   nötig ist, Laufzeit vergeben; `allow_admin` nur, wo Skripte wirklich Admin-Funktionen brauchen. Admins prüfen in
   der Benutzerliste, welche Konten aktive Tokens haben, und widerrufen Unbenutztes.
7. **Audit-Log → „Aufbewahrung“** festlegen (Standard unbegrenzt). Das Löschen alter Einträge entfernt auch deren
   Verlauf und die Möglichkeit zum Zurücksetzen; Einträge aus Versionen vor 3.0 lassen sich nicht zurücksetzen.
8. **Skripte anpassen** (siehe Changelog „Breaking“): u. a. `/bulk` mit leerer `records`-Liste → `delete`, bei
   `expected` einen Fingerprint für jedes geänderte RRset; 404 statt 502 bei fehlender Zone; neue Ergebniswerte von
   `POST /zones`; DNSSEC-Antworten `already_enabled`, 409 mit `detail.code` und 409 `parent_ds_present`;
   `reset-password` ohne Body erzwingt einen Passwortwechsel; Server-URL-Wechsel nur mit neuem `api_key`; LUA per
   Token nur mit `allow_admin`; `TYPEnnn` → 422; eine Änderung von `general.require_totp` über `PUT /settings/sso`
   nur mit `step_up`.
9. **Server mit „Speichern: Nein“:** DNSSEC-Änderungen dort sind jetzt gesperrt (403).
10. **DNSSEC-Zonen** öffnen: Bei mehreren Servern mit getrennten Datenbanken auf „andere Schlüssel“ bzw. „nicht
    signiert“ achten. Nach Schlüsseländerungen prüfen, ob das NOTIFY gesendet wurde (`notified` in der Antwort bzw.
    Tab „Propagation“).
11. **Vorlagen** mit sehr kurzer oder langer TTL einmal öffnen und speichern (erlaubt sind 60–604800 Sekunden).
12. **Bulk-Editor:** Auswahl-Checkboxen und Text-Editor erscheinen nur mit Schreibrecht auf die Zone. Einmal an einem
    RRset mit PowerDNS-Kommentar per Mehrfachauswahl die TTL setzen und prüfen, dass der Kommentar erhalten bleibt.
13. **Benutzerliste:** Badges für ausstehenden Passwortwechsel, 2FA und Passkeys prüfen. Für Reset-Links per E-Mail
    müssen SMTP und die öffentliche Basis-URL (Einstellungen → Profil; ersatzweise `WEBAUTHN_ORIGIN`) gesetzt sein.
    Fehlt beides, verschickt das Panel keine Reset-Mails und schreibt beim Start eine Warnung ins Log.
14. **Sprache und Datumsformate** im Profil prüfen; wer eine eigene `CONTENT_SECURITY_POLICY` setzt, die
    Browser-Konsole auf CSP-Meldungen prüfen.

### Nur bei Nutzung bestimmter Funktionen

- **Reverse-Proxy und DynDNS:** `TRUST_PROXY_HEADERS=true` (ggf. `TRUSTED_PROXY_HOPS`), `/nic/update` durchreichen,
  `Authorization` nicht loggen, öffentliche Basis-URL prüfen (siehe [DynDNS hinter dem Proxy](#dyndns-hinter-dem-proxy)).
- **Prometheus:** `/metrics` im Proxy nur für den Prometheus-Server, Token als Datei mit Rechten 600.
- **Propagations-Check, DNSKEY- und Elternzonen-Prüfung:** unter Einstellungen → Monitoring die externen
  DNS-Abfragen einschalten (für die DNSKEY-Prüfung zusätzlich „autoritative Nameserver prüfen“) und ausgehend
  UDP/TCP 53 erlauben. Die Resolver sehen die abgefragten Zonennamen.
- **SSO:** öffentliche Basis-URL, Redirect-URI `https://<host>/api/v1/auth/oidc/callback`, LDAP-CA,
  `AUTH_COOKIE_MAX_AGE` auf 8–24 Stunden (siehe [Single Sign-On](#single-sign-on-oidc)).
- **LUA-/Geo-Records:** `enable-lua-records` in jeder `pdns.conf` der Zone; die Policy „Alle mit Schreibrecht“ nur
  bewusst wählen (Health-Checks bauen Verbindungen aus dem DNS-Netz auf).
- **Eigene Resolver** statt Host-DNS: [compose.override.yaml](#eigene-dns-resolver).

### Downgrade auf 2.4.x

Nach dem ersten 3.0-Start kann 2.4.x die verschlüsselten Geheimnisse nicht lesen. Zwei Wege:

- **Dump von vorher einspielen** (der Dump aus `./update.sh` vor dem Update), dann auf die alte Version wechseln.
- **`prepare-downgrade`:** schreibt die Geheimnisse zurück in Klartext, deaktiviert Panel-Tokens mit Einschränkungen,
  die 2.4.x nicht kennt (Zonen, nur Lesen, Ablaufdatum, Admin-Tokens ohne Freigabe), und löscht die
  Migrations-Marker:
  ```bash
  docker compose stop backend
  docker compose run --rm --name pdnsmgr-secrets-cli backend python -m app.cli.secrets prepare-downgrade        # Trockenlauf
  docker compose run --rm --name pdnsmgr-secrets-cli backend python -m app.cli.secrets prepare-downgrade --yes
  git checkout <altes Release-Tag>
  docker compose build --no-cache backend
  docker compose up -d
  ```
  Beim erneuten Upgrade laufen Token-Migration und Audit-Nachtrag wieder; unter 2.4.x gelöschte Tokens bleiben
  widerrufen. Neue 3.0-Spalten ignoriert 2.4.x.

**Konten mit SSO-/LDAP-Anmeldung** (Weg über `prepare-downgrade`): Sie haben kein nutzbares Panel-Passwort und
können sich unter 2.4.x nicht anmelden – 2.4.x kennt weder OIDC noch LDAP. Der Trockenlauf von `prepare-downgrade`
nennt ihre Zahl. Noch unter 3.0 sicherstellen, dass mindestens ein lokaler Admin mit bekanntem Passwort existiert,
und Konten, die unter 2.4.x weiterarbeiten sollen, in lokale Konten umwandeln: Benutzerverwaltung → „Passwort &
Sicherheit“ → „Externe Anmeldung“ → „In lokales Konto umwandeln“ (mit Passwort-Bestätigung; API
`POST /api/v1/auth/users/{id}/convert-to-local`).

---

## Backup & Restore

### Was gesichert werden muss

| Was | Wo | Hinweis |
|---|---|---|
| Datenbank | Volume `mariadb_data` bzw. Dump | Zonenrechte, Benutzer, Audit-Log, verschlüsselte Geheimnisse |
| `.env` | Projektordner | DB-Passwörter, `JWT_SECRET_KEY`, ggf. `SECRET_ENCRYPTION_KEY` |
| Schlüssel für Geheimnisse | `.env`, `SECRET_ENCRYPTION_KEY_FILE` oder `/app/data/.secret_key` (Volume `backend_data`) | **getrennt vom Dump** sichern; Kopie mit `./update.sh --backup-key-only` |
| Schlüssel-Backup-Verzeichnis | `${PDNSMGR_KEY_BACKUP_DIR:-$HOME/.pdnsmgr-keys}` | eigenes Backup, nicht zusammen mit Stack-Ordner und Dump |
| Interne Daten | Volume `backend_data` (`/app/data`) | automatisch erzeugter JWT- und Geheimnis-Schlüssel |
| Logo/Uploads | Volume `backend_uploads` | eigenes Logo |

Die PowerDNS-Daten selbst sichert das Panel nicht – sie liegen in den Backends der PowerDNS-Server.

### Backup erstellen

```bash
# Datenbank (Root-Passwort aus dem Container-Environment ueber MYSQL_PWD, damit es nicht in der
# Prozessliste steht). Das MariaDB-11-Image hat nur mariadb-dump. ./update.sh bietet den Dump auch an.
( umask 077; docker exec dns-manager-db sh -c 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb-dump --single-transaction -u root dns_manager' > backup.sql )   # aeltere Images: mysqldump statt mariadb-dump

# Schlüssel (falls er nicht in der .env steht)
./update.sh --backup-key-only

# Volumes (Container vorher stoppen, sonst ist die Kopie der Datenbankdateien nicht konsistent).
# Der echte Volume-Name hat den Projektordner als Präfix, daher erst ermitteln:
docker compose stop
for v in mariadb_data backend_data backend_uploads; do
  VOL=$(docker volume ls -q -f name=$v)
  docker run --rm -v "$VOL":/data -v "$(pwd)":/backup alpine tar czf "/backup/$v.tar.gz" -C /data .
done
docker compose up -d
```

`backend_data.tar.gz` enthält den Schlüssel – nicht im selben Backup wie `backup.sql` bzw. `mariadb_data.tar.gz`
aufbewahren.

### Restore

1. Gleiche oder neuere 3.x-Version auschecken, `.env` zurückspielen.
2. Schlüssel bereitstellen: Steht er in der gesicherten `.env`, ist nichts zu tun. Sonst den Inhalt der Schlüsselkopie
   als `SECRET_ENCRYPTION_KEY=...` in die `.env` eintragen (oder `backend_data` zurückspielen).
3. Nur die Datenbank starten und den Dump einspielen:
   ```bash
   docker compose up -d mariadb
   docker compose stop backend        # falls es schon läuft
   docker exec -i dns-manager-db sh -c 'mysql -u root -p"$MARIADB_ROOT_PASSWORD" dns_manager' < backup.sql
   docker compose up -d
   ```
4. Einstellungen → Sicherheit prüfen: Fingerprint wie vorher, keine nicht lesbaren Einträge.

Passt der Schlüssel nicht zum Dump, startet das Backend nicht (`KEY_MISMATCH` bzw. `KEY_MISSING`, siehe unten).
Volumes aus einem `tar`-Backup zurückspielen (Container gestoppt):

```bash
docker compose stop
VOL=$(docker volume ls -q -f name=mariadb_data)
docker run --rm -v "$VOL":/data -v "$(pwd)":/backup alpine sh -c 'cd /data && tar xzf /backup/mariadb_data.tar.gz'
docker compose up -d
```

---

## Troubleshooting

### Container startet nicht

```bash
docker compose logs -f
docker compose ps
```

Compose meldet „DB_PASSWORD ist leer …“: `.env` fehlt oder ist unvollständig – `./setup.sh` ausführen oder die
Passwörter eintragen. MariaDB braucht beim ersten Start einige Sekunden, das Backend wartet darauf.

### Start abgebrochen

Das Backend-Log beginnt mit `==== PDNS Manager: Start abgebrochen – <Grund> (<CODE>) ====` und nennt die Auswege.

| Code | Ursache | Lösung |
|---|---|---|
| `KEY_MISSING` | verschlüsselte Werte in der DB, aber kein Schlüssel | gesicherten Schlüssel als `SECRET_ENCRYPTION_KEY` in die `.env`; ohne Backup: `reset-unreadable` (siehe [Schlüssel verloren](#schlüssel-verloren)) |
| `KEY_MISMATCH` | kein Wert lässt sich mit den konfigurierten Schlüsseln entschlüsseln | ursprünglichen Schlüssel als `SECRET_ENCRYPTION_KEY` oder `SECRET_ENCRYPTION_KEY_PREVIOUS` eintragen |
| `KEY_INVALID` | `SECRET_ENCRYPTION_KEY` ist kein gültiger Schlüssel (44 Zeichen urlsafe-Base64) | Wert prüfen (Tippfehler, Anführungszeichen, abgeschnitten) |
| `KEY_CONFIG` | nur `SECRET_ENCRYPTION_KEY_PREVIOUS` gesetzt | neuen Schlüssel als `SECRET_ENCRYPTION_KEY` eintragen |
| `KEY_FILE_MISSING`, `KEY_FILE_INVALID`, `KEY_FILE_UNREADABLE` | Schlüsseldatei fehlt, ist beschädigt oder nicht lesbar | Datei bzw. Rechte korrigieren oder gesicherten Schlüssel als `SECRET_ENCRYPTION_KEY` eintragen |
| `SCHEMA_INCOMPLETE`, `SCHEMA_TOO_NARROW` | Schema-Migration unvollständig (z. B. fehlendes `ALTER`-Recht) | Rechte des DB-Benutzers prüfen, Meldungen der Migration weiter oben im Log lesen |
| `MIGRATION_FAILED` | Datenbankfehler bei der Verschlüsselung | nichts wurde verändert; Ursache im Log beheben, neu starten |

`./update.sh` zeigt diese Zeilen nach einem gescheiterten Start an. Zurück auf die vorige Version: Dump einspielen und
das alte Release-Tag auschecken (siehe [Downgrade auf 2.4.x](#downgrade-auf-24x)).

### Geheimnisse werden nicht verschlüsselt

`/health` meldet `degraded`, `update.sh` meldet „Geheimnisse werden NICHT verschlüsselt“: Das Backend konnte
`/app/data/.secret_key` nicht anlegen (Volume aus einer alten Installation gehört `root`). Beheben:

```bash
docker compose exec -u root backend chown -R 1001:1001 /app/data && docker compose restart backend
```

### Roter Hinweis: Geheimnisse nicht entschlüsselbar

Einzelne Werte passen nicht zum Schlüssel. **Einstellungen → Sicherheit** listet sie mit Sprung zum Neu-Eintragen.
Ein PowerDNS-Server mit nicht lesbarem API-Key wird nicht geladen; Änderungen melden ihn als
`skipped (not loaded: api key unreadable)`. Nach dem Neu-Eintragen die Zonen dieses Servers abgleichen.

### Webhook kommt nicht an

Zuerst **Einstellungen → API & Sicherheit → Webhooks → Zustellprotokoll** öffnen: Status, HTTP-Code und Fehlercode
(z. B. `ssrf_blocked`, `timeout`, `url_unreadable`) je Versuch. Mit `BACKGROUND_WORKERS_ENABLED=false` werden Ereignisse
nur gesammelt; die Karte zeigt das als Hinweis. Ziele in privaten Netzen brauchen `WEBHOOK_ALLOW_PRIVATE_URLS=true`.

### DynDNS: `badauth` oder `badip`

- `badauth`: Token falsch, deaktiviert oder gesperrt. Ein Token, der im Query-String der URL stand, wird sofort
  gesperrt und braucht ein neues Secret. Den Token nur als Basic-Auth-Passwort oder `Authorization: Bearer` senden.
- `badip` hinter einem Proxy: `TRUST_PROXY_HEADERS=true` setzen oder die IP per `myip` mitsenden.

### Propagations-Check zeigt nur die Panel-Server

Die externen DNS-Abfragen sind standardmäßig aus. Unter **Einstellungen → Monitoring → Propagations-Check**
freischalten und ausgehend UDP/TCP 53 erlauben. PowerDNS hält Antworten im Paketcache (Standard 20 Sekunden) – frische
Änderungen können kurz verdeckt sein.

### `/metrics` liefert 404

Der Endpunkt ist aus, bis ein Scrape-Token besteht (Panel oder `METRICS_TOKEN`). 401 = falscher oder fehlender Token.

### SSO-Anmeldung scheitert

Die Login-Seite zeigt den Grund (`/login?sso_error=<code>`), das Audit-Log den Eintrag `LOGIN_FAILED`. Häufig: falsche
Redirect-URI beim Anbieter, fehlende öffentliche Basis-URL, Konto nicht freigegeben (Gruppen/Domains,
automatische Kontoanlage aus), Anbieter nicht erreichbar. Bei abgeschalteter lokaler Anmeldung kommen Admins über
`/login?local=1` weiter lokal hinein.

Ist LDAP eingeschaltet, aber nicht erreichbar oder falsch konfiguriert, antwortet jede Anmeldung mit falschem oder
unbekanntem Passwort mit 503 („Der Anmeldedienst (LDAP) ist nicht erreichbar …“ bzw. „Die LDAP-Anmeldung ist
fehlerhaft konfiguriert …“) – auch für lokale Konten, damit sich während einer Störung nicht ermitteln lässt, welche
lokalen Konten es gibt. Lokale Konten mit richtigem Passwort melden sich weiter sofort an. Jeder dieser Versuche
zählt als Fehlversuch (Login-Sperre). Die Ursache steht im Audit-Log: `LOGIN_FAILED` mit `reason`
`ldap_unavailable` bzw. `ldap_config` (bei lokalen Konten `bad_credentials` mit `ldap_error`). „Verbindung testen“
unter Einstellungen → Anmeldung / SSO prüft die Verbindung.

### Admin-Passwort vergessen

Normalfall: Ein anderer Admin setzt das Passwort unter **Benutzer → Passwort & Sicherheit** zurück (Zufallspasswort
oder Reset-Link per E-Mail). Notfallweg auf der Konsole, wenn es keinen anderen Admin gibt:

```bash
docker compose exec backend python -c "
from app.core.database import async_session
from app.models.models import User
from app.core.auth import hash_password
import asyncio

async def reset():
    async with async_session() as db:
        admin = await db.get(User, 1)   # User-ID 1 = erster Admin
        admin.hashed_password = hash_password('neues-passwort')
        await db.commit()
        print('Passwort zurueckgesetzt.')

asyncio.run(reset())
"
```

Bestehende Sitzungen des Kontos enden damit. Für Konten mit SSO-/LDAP-Anmeldung gilt das Panel-Passwort nicht.

---

## Tipps für den Produktivbetrieb

1. Keine Standard-Zugangsdaten, `.env` mit Rechten 600.
2. Immer HTTPS, Port 5380 nur lokal binden.
3. Updates regelmäßig einspielen, vorher den Changelog lesen.
4. Monitoring: `/health` für Uptime-Checks, `/metrics` für Prometheus.
5. Backups automatisieren – Datenbank-Dump und Schlüssel getrennt.

## Weitere Ressourcen

- [README](README.md) – Überblick, Changelog
- [docs/PANEL-API.md](docs/PANEL-API.md) – API für Skripte, Webhooks, DynDNS, Metriken
- [SECURITY.md](SECURITY.md) – Sicherheitslücken melden
- [Dokumentation](https://pdns-manager.gemtecgames.com) – ausführliche Anleitungen
- [Issue Tracker](https://github.com/29barra29/PowerDNS-PDNS-MANAGER/issues)
