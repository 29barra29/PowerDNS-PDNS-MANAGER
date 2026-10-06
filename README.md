# PDNS Manager

Ein Web-Panel für **PowerDNS Authoritative Server** zum Self-Hosten. Entstanden aus dem Wunsch, PowerDNS-Admin durch etwas Aufgeräumteres mit aktuellem Stack zu ersetzen.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)
![PowerDNS](https://img.shields.io/badge/PowerDNS-4.x-orange.svg)
![Version](https://img.shields.io/badge/version-v3.0.0-blue.svg)

---

## Demo

![PDNS Manager – Einstellungen / Profil](docs/screenshots/settings-profile.jpg)

*Beispiel-Ansicht: Einstellungen → Profil mit angepasstem Branding (Logo, App-Name).*

Ein Video-Walkthrough (Installation, ersten Server anbinden, Zone + DNSSEC anlegen) folgt auf YouTube. Sobald es online ist, steht der Link hier. Wer zwischendurch Fragen hat: einfach ein Issue aufmachen.

## Dokumentation

Ausführliche Doku, Screenshots, Setup-Guides, Changelog & FAQ:
**[pdns-manager.gemtecgames.com](https://pdns-manager.gemtecgames.com)**

Dieses README gibt den Schnellüberblick (Installation, Stack, Troubleshooting, Changelog). Installation und Betrieb im Detail – Umgebungsvariablen, Reverse-Proxy, Schlüssel für die verschlüsselten Geheimnisse, Backup und das Upgrade auf 3.0 – stehen in [INSTALL.md](INSTALL.md), die API für Skripte in [docs/PANEL-API.md](docs/PANEL-API.md). Anleitungen zu einzelnen Funktionen werden auf der Doku-Seite gepflegt.

---

## Was es kann

**DNS**
- **Mehrere PowerDNS-Server (4.x) parallel verwalten** – mit Verbindungstest, Suche über alle Server (parallel, mit Hinweis bei nicht erreichbaren Servern) und „Speichern: Ja/Nein“ je Server. Änderungen gehen zuerst an den Server aus der Ansicht und danach an alle weiteren schreibbaren Server, die die Zone führen; die Antwort nennt das Ergebnis je Server.
- **Zonen + Records** – A, AAAA, CNAME, MX, TXT, NS, SRV, CAA, PTR, TLSA, SSHFP und LUA per Formular; ALIAS, DNAME, SVCB/HTTPS, LOC, NAPTR, SPF, OPENPGPKEY und die DNSSEC-Typen als RDATA-Text mit Hilfetexten. TTL 60 Sekunden bis 7 Tage, Filter in der Record-Tabelle, Records deaktivieren.
- **Bulk-Editor** – Mehrfachauswahl (löschen, TTL setzen, (de)aktivieren) und Text-Editor im BIND-Format mit drei Modi; vor jedem Speichern eine Vorschau je RRset, Schutz vor zwischenzeitlichen Änderungen.
- **Record-Historie & Rollback** – Tab „Verlauf“ je Zone mit Vorher/Nachher je RRset; Änderungen lassen sich mit Vorschau zurücksetzen.
- **Zonen-Export und NOTIFY** – Export als BIND-Zonendatei, NOTIFY an die Secondaries per Knopf.
- **DNSSEC** – Aktivieren mit Optionen (CSK oder KSK + ZSK, ECDSA, Ed25519/Ed448, RSA, NSEC oder NSEC3), Schlüsselverwaltung mit Schutzregeln, Rollover-Assistent mit DNSKEY- und Elternzonen-Prüfung, DS-Assistent für den Registrar; bei Master-Zonen Serial-Erhöhung und NOTIFY.
- **Reverse-DNS automatisch** – PTR-Pflege bei A/AAAA-Änderungen in Reverse-Zonen, die das Panel verwaltet; fremde PTRs werden nie überschrieben.
- **LUA- & Geo-Records** – dynamische Records (Failover, gewichtete Verteilung, Antwort nach Land/Kontinent/nächstem Server) mit Vorlagen, Live-Prüfung und Statusanzeige der PowerDNS-Server; wer sie schreiben darf, legt ein Admin fest.
- **Propagations-Check** – vergleicht Serial und Inhalte zwischen den Panel-Servern und – nach Freischaltung – mit den autoritativen Nameservern und öffentlichen Resolvern.
- **Zonen-Vorlagen** – NS, SOA und Standard-Records, beim Anlegen einer Zone auswählbar.

**Benutzer und Sicherheit**
- **Benutzer, Rollen und Zonenrechte** – Admin und Benutzer, je Zone „Lesen“ oder „Verwalten“; Passwort-Reset (Zufallspasswort oder Link), 2FA/Passkeys zurücksetzen, „Alle Zugänge widerrufen“, erzwungener Passwortwechsel.
- **Single Sign-On** – OpenID Connect (z. B. Keycloak, Authentik, Microsoft Entra ID) und LDAP/Active Directory, Rollen optional aus Gruppen, automatische Kontoanlage nur mit Einschränkung; lokaler Notfallzugang für Admins.
- **2FA (TOTP) und Passkeys / WebAuthn** – passwortlose Anmeldung per Fingerabdruck, Gesicht oder Sicherheitsschlüssel; der private Schlüssel verlässt das Gerät nie. Für Produktion per `WEBAUTHN_RP_ID` an die Domain bindbar.
- **Verschlüsselte Geheimnisse** – PowerDNS-API-Keys, Webhook-Secrets und -URLs, 2FA-Geheimnisse, SMTP-Passwort, Captcha- und SSO-Secrets liegen verschlüsselt in der Datenbank; Statuskarte unter Einstellungen → Sicherheit.
- **Audit-Log** – jede relevante Änderung mit Zeitpunkt, Benutzer, Client-IP und Details; Filter, Detailansicht, CSV-Export und einstellbare Aufbewahrung.
- **Captcha** – optional auf Login, Registrierung und Passwort-Reset (Cloudflare Turnstile, hCaptcha, Google reCAPTCHA v2), Prüfung serverseitig.

**Integrationen**
- **API-Tokens für Skripte** – mit den Rechten des Benutzers, zusätzlich einschränkbar auf Zonen, Leserecht und eine Laufzeit; Admin-Funktionen nur mit ausdrücklicher Freigabe.
- **Webhooks** – Ereignisse zu Records, Zonen, DNSSEC und DynDNS mit HMAC-Signatur, Zustellung aus einer Warteschlange mit Wiederholungen, Zustellprotokoll und „Test senden“.
- **DynDNS** – Router (z. B. FRITZ!Box, ddclient, inadyn) aktualisieren freigegebene Hostnamen über `/nic/update` (dyndns2-kompatibel) mit eigenen, auf Hostnamen beschränkten Tokens.
- **ACME / Auto-TLS** – Tokens nur für `_acme-challenge`-TXT-Records in freigegebenen Zonen; fertiges certbot-Hook-Skript (`scripts/certbot-dns-dnsmanager.sh`).
- **Monitoring** – Prometheus-Endpunkt `/metrics` (mit Scrape-Token) und `/health` für Uptime-Checks.

**Oberfläche**
- **SMTP und Welcome-Mail** – Passwort-Reset und Benachrichtigungen; Welcome-Mail mit Platzhaltern (`{username}`, `{login_url}`, …), Live-Vorschau und Test-Senden.
- **Branding** – App-Name, Tagline und Logo; das Logo bleibt bei Updates im Volume `backend_uploads` erhalten.
- **Mehrsprachig** – Deutsch, Englisch, Serbisch, Kroatisch, Bosnisch und Ungarisch; Datumsangaben im Format der gewählten Sprache. Die Sprache wird im Profil gespeichert.
- **Mobile-tauglich** – einklappbare Sidebar, Tabellen scrollen horizontal.

**Panel-API & Skripte:** Die Weboberfläche nutzt dieselbe REST-API unter `/api/v1`. Skripte authentifizieren sich mit einem **Panel-API-Token** im Header `Authorization: Bearer …` (Einstellungen → API & Sicherheit → „API-Token (Panel)“). Einstellungen sowie die Verwaltung von Tokens und Webhooks gehen nur mit einer Browser-Anmeldung. Endpunkte, Rechte, Fehlerformate, Webhooks, DynDNS und Metriken: [`docs/PANEL-API.md`](docs/PANEL-API.md). Optional: interaktive API unter `/docs`, wenn `DOCS_ENABLED=true` gesetzt ist.

## Was es bewusst nicht macht

- Kein DHCP, kein Recursor, kein Slave-DNS-Setup-Tool – das hier verwaltet **Authoritative Zones**.
- Keine Multi-Tenant-Mandantentrennung (keine getrennten „Kunden"). Wer das braucht, ist mit PowerDNS-Admin oder einer kommerziellen Lösung besser bedient.
- Kein eingebauter Reverse-Proxy / kein TLS – das macht Caddy / nginx / Traefik / Cloudflare Tunnel davor.
- Keine Pflege des PowerDNS-geoip-Backends (YAML-Zonen sind nicht über die HTTP-API erreichbar) – Geo-Antworten gibt es über LUA-Records.
- Kein automatischer Abgleich zwischen PowerDNS-Servern mit getrennten Datenbanken; Abweichungen zeigt der Propagations-Check.

---

## Installation

### Voraussetzungen

Docker und Docker Compose. Port `5380` muss frei sein – wer ihn ändern oder nur lokal binden will, setzt `HOST_PORT=…` bzw. `BIND_ADDR=127.0.0.1` in der `.env` (die `compose.yaml` selbst nicht editieren, sonst scheitert `update.sh` am `git checkout`). Mitgeliefert wird MariaDB 11; eine eigene Datenbank muss MariaDB ab 10.6 oder MySQL ab 8.0 sein. Details: [INSTALL.md](INSTALL.md#voraussetzungen).

### Variante A – One-Liner

Holt das Repo, fragt ein paar Sachen ab, erzeugt eine fertige `.env` und startet die Container:

```bash
curl -sSLO https://raw.githubusercontent.com/29barra29/PowerDNS-PDNS-MANAGER/main/install.sh && bash install.sh
```

### Variante B – Klonen und Setup-Wizard

Selber Effekt, nur ohne den Download-Wrapper:

```bash
git clone https://github.com/29barra29/PowerDNS-PDNS-MANAGER.git
cd PowerDNS-PDNS-MANAGER
./setup.sh
docker compose up -d
```

### Variante C – Manuell

Wer keinen Wizard mag und alle Variablen selbst setzen will:

```bash
git clone https://github.com/29barra29/PowerDNS-PDNS-MANAGER.git
cd PowerDNS-PDNS-MANAGER
cp .env.example .env
# PFLICHT: DB_ROOT_PASSWORD und DB_PASSWORD ausfüllen (z. B. openssl rand -base64 32),
# sonst bricht compose absichtlich mit einer Fehlermeldung ab.
# EMPFOHLEN: JWT_SECRET_KEY (openssl rand -hex 64) und SECRET_ENCRYPTION_KEY
# (openssl rand -base64 32 | tr '+/' '-_') setzen. Fehlen sie, erzeugt das Backend die
# Schlüssel einmalig im Volume backend_data.
nano .env
chmod 600 .env
docker compose up -d
```

Alle Variablen erklärt [INSTALL.md](INSTALL.md#umgebungsvariablen).

### Erster Login

1. Browser auf `http://localhost:5380`.
2. Ist `ENABLE_REGISTRATION=true` in der `.env` gesetzt (Default ist `false`), wird der Setup-Wizard im Browser angezeigt – der erste angelegte User ist automatisch Admin. Anschließend stellt sich die Registrierung selbst ab.
3. Hat das Setup ein festes Admin-Passwort vergeben, ist der Username `admin`. Wurde gar kein Passwort gesetzt, generiert das Backend beim ersten Start eines und legt es ab unter `/app/.initial-admin-password` im Container:
   ```bash
   docker compose exec backend cat /app/.initial-admin-password
   ```

### PowerDNS-Server eintragen

Geht direkt im Panel: **Einstellungen → DNS-Server → Server hinzufügen**, dort Name, URL (z. B. `http://pdns:8081`) und API-Key eintragen, **Verbindung testen** drücken, speichern. Mehrere Server gleichzeitig sind kein Problem.

Damit das funktioniert, muss PowerDNS die HTTP-API anbieten. Minimal-Konfiguration in `/etc/powerdns/pdns.conf`:

```ini
api=yes
api-key=dein-sicherer-api-key
webserver=yes
webserver-address=0.0.0.0
webserver-port=8081
webserver-allow-from=127.0.0.1,192.0.2.0/24   # Adresse(n), von denen das Panel zugreift
# enable-lua-records=yes   # nur wenn LUA-/Geo-Records genutzt werden
```

Dann `systemctl restart pdns`.

---

## Updates

> **Von 2.x auf 3.0:** Vor dem Update den Abschnitt „Vor dem Update lesen“ im [Changelog](#v300--major) und
> [INSTALL.md → Upgrade von 2.x auf 3.0](INSTALL.md#upgrade-von-2x-auf-30) lesen. 3.0 verschlüsselt die gespeicherten
> Geheimnisse; den Schlüssel getrennt vom Datenbank-Backup sichern.

Solange das Projekt mit `git clone` installiert wurde (also bei `install.sh` oder Variante B), genügt:

```bash
cd /pfad/zu/PowerDNS-PDNS-MANAGER
./update.sh
```

`update.sh` macht der Reihe nach:

1. `git fetch` mit Tags und Wechsel auf das neueste `v*`-Tag (oder `git pull`, falls `main` ausgecheckt ist). Bei einem Major-Sprung erscheint eine Warnbox mit Bestätigung.
2. Angebot eines DB-Dumps (`backup_<version>_<zeitstempel>.sql`, Rechte 0600).
3. `docker compose build backend` (ohne Cache bei Versionswechsel), Rechte der Volumes korrigieren, `up -d`, bis zu 120 Sekunden auf das Backend warten – bei einem Startabbruch zeigt es die letzten Log-Zeilen.
4. Liegt der Schlüssel für die Geheimnisse nur im Docker-Volume, legt es eine Kopie in `~/.pdnsmgr-keys` (bzw. `PDNSMGR_KEY_BACKUP_DIR`) ab.

Datenbank, `.env` und Logo bleiben unangetastet. Schalter: `--rebuild`, `--no-backup`, `--skip-fetch`, `--no-key-backup`, `--backup-key-only` (nur Schlüssel sichern), `--help`.

Manuell auf eine bestimmte Version wechseln (vorher einen Dump anlegen, siehe [INSTALL.md](INSTALL.md#backup--restore)):

```bash
cd PowerDNS-PDNS-MANAGER
git fetch origin --tags --force --prune
git checkout v3.0.0              # oder: git checkout main && git pull
docker compose build --no-cache backend
docker compose up -d
```

---

## Sicherheit

Was automatisch passiert:

- `setup.sh`/`install.sh` erzeugen DB-Passwörter, `JWT_SECRET_KEY` und `SECRET_ENCRYPTION_KEY`; die `.env` bekommt `chmod 600`. Fehlen die Schlüssel (etwa bei Variante C), erzeugt das Backend sie einmalig im Volume `backend_data`.
- Gespeicherte Geheimnisse liegen verschlüsselt in der Datenbank (Fernet, `enc:v1:`); Passwörter als bcrypt-Hash (`pwdlib`), API-, ACME- und DynDNS-Tokens nur als SHA-256-Hash.
- Login-Fehlversuche werden je IP und je Benutzername gezählt (5 Fehlversuche für einen Namen in 15 Minuten sperren ihn); 2FA-Codes und Passkey-Challenges sind nur einmal gültig.
- Zustandsändernde Browser-Anfragen müssen von der eigenen Seite kommen (CSRF-Schutz); eine Content-Security-Policy ist standardmäßig aktiv, die Schrift wird lokal ausgeliefert.
- Webhook-Ziele in privaten Netzen sind gesperrt (SSRF-Schutz), es wird an die geprüfte IP gesendet.
- `/docs` ist aus (`DOCS_ENABLED=false`), `/metrics` ohne Scrape-Token aus, `/health` zeigt Details nur aus dem Container selbst.
- Passwörter und Tokens in URL-Parametern werden im Access-Log maskiert.

Was du selbst noch machen solltest:

- Admin-Passwort nach dem ersten Login ändern.
- Den **Schlüssel für die Geheimnisse sichern** – getrennt vom Datenbank-Dump (siehe [INSTALL.md → Geheimnisse & Schlüssel](INSTALL.md#geheimnisse--schlüssel)).
- Reverse-Proxy mit TLS davorschalten. Sobald HTTPS läuft, in der `.env` `AUTH_COOKIE_SECURE=true` setzen und einmal `docker compose up -d`.
- Port `5380` nicht öffentlich lassen: `BIND_ADDR=127.0.0.1` in der `.env` (eine Host-Firewall greift bei Docker-Ports nicht). Hinter dem Proxy `TRUST_PROXY_HEADERS=true` setzen – sonst teilen sich alle Nutzer eine Login-Sperre und DynDNS erkennt die Router-IP nicht.
- `ENABLE_REGISTRATION=false` setzen, sobald alle Accounts angelegt sind.
- API-Tokens auf Zonen, Leserecht und eine Laufzeit beschränken; `/metrics` im Proxy nur für Prometheus freigeben.
- Optional Fail2Ban auf die Reverse-Proxy-Logs.

Kompletter nginx-Block (inklusive `/metrics` und DynDNS) und Traefik-/Cloudflare-Tunnel-Beispiele stehen in [INSTALL.md](INSTALL.md#https-mit-reverse-proxy). Sicherheitslücken bitte nicht öffentlich melden, sondern wie in [SECURITY.md](SECURITY.md) beschrieben.

---

## Stack

| Komponente | Was läuft hier |
|---|---|
| Frontend | React 19 + Vite 8 (Rolldown) + Tailwind CSS 4 + i18next 26; Schrift Inter lokal (`@fontsource-variable/inter`) |
| Backend | Python 3.12 + FastAPI 0.141 + SQLAlchemy 2 (async) + Pydantic 2.13 |
| Datenbank | MariaDB 11 (Async-Treiber `aiomysql`); extern ab MariaDB 10.6 / MySQL 8.0 |
| Auth | JWT in HttpOnly-Cookie, Hashing über `pwdlib` + bcrypt, TOTP (`pyotp`), Passkeys (`webauthn`), SSO: OIDC (`authlib`, `joserfc`) und LDAP (`ldap3`) |
| Geheimnisse | Fernet-Verschlüsselung über `cryptography` |
| DNS | PowerDNS Authoritative 4.x (über die HTTP-API), DNS-Abfragen und -Parser über `dnspython` |
| Monitoring | `prometheus_client` |
| Container | Docker Compose, Multi-Stage Build, Backend läuft als Non-Root |

### Abhängigkeiten & Lizenzen

Der PDNS Manager steht unter der MIT-Lizenz. Die vollständige, gepinnte Liste der Python-Pakete steht in `backend/requirements.lock`, die der Frontend-Pakete in `frontend/package-lock.json`. Neu in 3.0:

| Paket | Version | Lizenz | Zweck |
|---|---|---|---|
| Authlib | 1.8.0 | BSD-3-Clause | OIDC-Anmeldung |
| joserfc | 1.7.5 | BSD-3-Clause | Prüfung der ID-Tokens (OIDC) |
| ldap3 | 2.9.1 | LGPL-3.0 | LDAP/Active Directory |
| prometheus_client | 0.26.0 | Apache-2.0 AND BSD-2-Clause | Prometheus-Metriken |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause | Verschlüsselung der Geheimnisse (jetzt direkt gepinnt, Version unverändert) |
| @fontsource-variable/inter | 5.3.0 | OFL-1.1 | lokal ausgelieferte Schrift (statt Google Fonts) |

**Hinweis zu ldap3 (LGPL-3.0):** `ldap3` wird unverändert als eigenständige Bibliothek genutzt und als Python-Paket in das Docker-Image installiert. Der Quellcode der Bibliothek ist öffentlich verfügbar (PyPI und Projekt-Repository); wer sie durch eine andere, schnittstellenkompatible Version ersetzen möchte, kann `backend/requirements.lock` anpassen und das Image neu bauen. Die LGPL gilt nur für `ldap3` selbst, nicht für den PDNS Manager.

---

## Projektstruktur (grob)

```
PowerDNS-PDNS-MANAGER/
├── VERSION                # einzige Stelle, an der die App-Version steht
├── compose.yaml           # Stack-Definition (nicht editieren – eigene Anpassungen in compose.override.yaml)
├── .env.example           # Vorlage – wird zu .env (nicht im Git)
├── install.sh / setup.sh / update.sh
├── docs/                  # PANEL-API.md, Release-Notes-Fragmente, Screenshots
├── scripts/               # certbot-Hook, README-Versions-Sync, Locale-Werkzeuge, E2E- und UI-Tests (scripts/e2e)
├── backend/               # FastAPI-App, Tests, Dockerfile
└── frontend/              # React-App (wird im Backend-Image als Static ausgeliefert)
```

---

## Troubleshooting

Ausführlicher in [INSTALL.md → Troubleshooting](INSTALL.md#troubleshooting).

### Compose meldet „DB_PASSWORD ist leer …" beim Start

Ohne gesetzte Passwörter wird die DB nicht initialisiert. Lösung:

```bash
./setup.sh                          # legt eine vollständige .env an
# oder manuell: cp .env.example .env und Passwörter eintragen
docker compose up -d
```

### Backend startet nicht: „Start abgebrochen – …"

Das Backend bricht den Start ab, wenn der Schlüssel für die Geheimnisse fehlt oder nicht passt (`KEY_MISSING`, `KEY_MISMATCH` …) oder das Datenbankschema nicht migriert werden konnte. Die Log-Zeilen (`docker compose logs backend`) nennen die Ursache und die Auswege; Tabelle der Codes in [INSTALL.md](INSTALL.md#start-abgebrochen).

### Roter Hinweis „Geheimnisse nicht entschlüsselbar"

Einstellungen → Sicherheit listet die betroffenen Werte mit Sprung zum Neu-Eintragen. PowerDNS-Server mit nicht lesbarem API-Key sind „nicht geladen“ – nach dem Neu-Eintragen die Zonen abgleichen.

### Webhook kommt nicht an

Einstellungen → API & Sicherheit → Webhooks → **Zustellprotokoll** zeigt je Versuch Status, HTTP-Code und Fehlercode.

### Backend logt „JWT_SECRET_KEY ist nicht in der .env gesetzt"

Schlüssel nachreichen, dann neu starten:

```bash
echo "JWT_SECRET_KEY=$(openssl rand -hex 64)" >> .env
docker compose up -d
```

Achtung: Wer den Wert später noch einmal ändert, loggt damit alle bestehenden Sessions aus.

### Container starten nicht / hängen

```bash
docker compose logs -f
docker compose down
docker compose up -d
```

MariaDB braucht beim allerersten Start ein paar Sekunden, bis sie healthy ist. Das Backend wartet automatisch (über `depends_on: condition: service_healthy`).

### Admin-Passwort vergessen

Normalfall: Ein anderer Admin setzt es unter **Benutzer → Passwort & Sicherheit** zurück. Gibt es keinen anderen Admin, hilft die Konsole:

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

---

## Was ist neu (Changelog)

Hier die letzten Releases. Komplette Historie: [GitHub Releases](https://github.com/29barra29/PowerDNS-PDNS-MANAGER/releases).

### v3.0.0 – Major

*Oktober 2026.* Major-Release für den Einsatz im Unternehmen: Änderungen an Records sind nachvollziehbar und lassen sich zurücksetzen, gespeicherte Geheimnisse liegen verschlüsselt in der Datenbank, API-Tokens lassen sich auf Zonen, Leserecht und eine Laufzeit beschränken, und die Anmeldung über OIDC oder LDAP/Active Directory ist möglich. **Die Datenbank wird beim ersten Start automatisch migriert; ein Downgrade auf 2.4.x geht danach nur mit dem Dump von vorher oder nach `prepare-downgrade`.** Schritt für Schritt: [INSTALL.md → Upgrade von 2.x auf 3.0](INSTALL.md#upgrade-von-2x-auf-30).

**Vor dem Update lesen (Breaking)**

*Betrieb und Daten*

- **Gespeicherte Geheimnisse werden verschlüsselt** – PowerDNS-API-Keys, Webhook-Secrets **und Webhook-URLs**, 2FA-Geheimnisse, SMTP-Passwort, Captcha-Secret, SSO-Secrets und Metrik-Token. Der Schlüssel kommt aus `SECRET_ENCRYPTION_KEY` in der `.env` oder wird beim ersten Start als `/app/data/.secret_key` (Volume `backend_data`) erzeugt. Im zweiten Fall legt `./update.sh` eine Kopie in `${PDNSMGR_KEY_BACKUP_DIR:-$HOME/.pdnsmgr-keys}` ab – nie im Stack-Ordner, nie neben dem Dump. Dieses Verzeichnis bzw. die `.env` getrennt vom Datenbank-Backup sichern: Ohne Schlüssel sind die Geheimnisse aus einem Backup nicht lesbar.
- **Downgrade-Grenze:** Nach dem ersten 3.0-Start kann 2.4.x die Geheimnisse nicht lesen. Zurück nur mit dem Dump von vor dem Update oder nach `python -m app.cli.secrets prepare-downgrade --yes`. Der Dump von vor dem Update enthält die Geheimnisse noch im Klartext.
- **Start-Abbruch statt halbem Schema:** Fehlt dem Datenbank-Benutzer das `ALTER`-Recht oder fehlt bzw. passt der Schlüssel nicht, startet das Backend nicht und nennt die Ursache im Log.
- **Eigene Datenbank:** MariaDB ab 10.6 bzw. MySQL ab 8.0 (`SKIP LOCKED`). Nur **ein** Backend-Prozess darf die Webhook-Warteschlange abarbeiten; `BACKGROUND_WORKERS_ENABLED=false` sammelt Ereignisse nur.
- **`compose.yaml`:** Der feste DNS-Eintrag `8.8.8.8`/`8.8.4.4` entfällt – der Container nutzt die Resolver des Docker-Hosts, eigene per `compose.override.yaml`. Die Standard-Content-Security-Policy enthält keine Google-Fonts-Hosts mehr (eine selbst gesetzte `CONTENT_SECURITY_POLICY` bleibt).
- **Neue Umgebungsvariablen:** `SECRET_ENCRYPTION_KEY` (+ `SECRET_ENCRYPTION_KEY_PREVIOUS`, `SECRET_ENCRYPTION_KEY_FILE`), `BACKGROUND_WORKERS_ENABLED`, `METRICS_TOKEN`, `SSO_ALLOW_INSECURE` (nur für Tests) und für `update.sh` `PDNSMGR_KEY_BACKUP_DIR`. `TRUST_PROXY_HEADERS`/`TRUSTED_PROXY_HOPS` sind hinter einem Reverse-Proxy jetzt auch für DynDNS nötig.
- **`/health`** liefert von außen nur noch `status`, `database` und `servers`; Details nur bei Abfrage aus dem Container. **`/metrics`** ist neu und ohne Scrape-Token aus (404); mit `METRICS_TOKEN` ist die Panel-Einstellung gesperrt. `GET /api/v1/metrics` per Token nur mit `allow_admin`.
- **Externe DNS-Abfragen** (Propagations-Check, DNSKEY- und Elternzonen-Prüfung) erst nach Freischaltung durch einen Admin; der Container braucht dafür ausgehend UDP/TCP 53, die Resolver sehen die Zonennamen. Gemeinsames Limit: 10 Prüfungen pro Minute und Benutzer (429 mit `Retry-After`).

*Anmeldung, Tokens, Sitzungen*

- **Nur noch mit Browser-Anmeldung** (per API-Token 403): alle Endpunkte unter `/api/v1/settings/*` (Server, SMTP, Captcha, Branding, ACME-Tokens, API-Key-Anzeige, SSO, DynDNS, PTR, Monitoring, LUA), die Verwaltung von Panel-Tokens, DynDNS-Tokens und Webhooks (die Webhook-Liste bleibt per Token lesbar, ohne Ziel-URL) sowie die Anzeige von 2FA-Status und Passkeys.
- **Admin-Endpunkte per Token nur mit `allow_admin`.** Bestehende Tokens von Admins bekommen die Freigabe beim Update automatisch. Alle Bestandstokens gelten für alle Zonen ohne Ablauf („Weitreichend“).
- Panel-Tokens nur noch aus dem `Authorization`-Header (nicht aus dem Cookie). Pausierte, abgelaufene und widerrufene Tokens → 401; ein Widerruf ist endgültig, in 2.x gelöschte Tokens gelten als widerrufen.
- `GET /auth/me` enthält den neuen Block `auth`. Zonenlisten (Suche, Zonenzahl der Server) zeigen nur noch sichtbare Zonen, die Suche liefert `truncated`. Interne PowerDNS-URL und Server-Statistiken sehen nur Admins.
- **Erzwungener Passwortwechsel:** Bis zum Wechsel erlaubt die Browser-Sitzung nur Profil, Passwortwechsel und Abmelden, sonst 403 mit Header `X-Password-Change-Required`. `PUT /auth/users/{id}/reset-password` **ohne Body erzwingt jetzt den Wechsel** (`{"must_change_password": false}` schaltet das ab).
- **Login-Sperren je IP (IPv6 je /64) und je Benutzername:** 5 Fehlversuche für einen Namen in 15 Minuten sperren ihn von jeder Adresse. Gilt auch für 2FA, Passkey, LDAP-Verknüpfung und die Bestätigung kritischer Änderungen; ein nicht erreichbares oder falsch konfiguriertes LDAP zählt als Fehlversuch.
- **„Alle Zugänge widerrufen“ und der Admin-Passwort-Reset beenden bestehende Browser-Sitzungen** (danach 401 „Sitzung abgelaufen – bitte erneut anmelden“). DynDNS-Tokens werden dabei endgültig gesperrt, auch pausierte; aktivieren lassen sie sich erst nach „Neues Secret“. `revoked.dyndns_tokens` zählt die neu gesperrten Tokens.
- Benutzerverwaltung: Das eigene Konto lässt sich nicht deaktivieren, herabstufen oder per Admin-Werkzeug zurücksetzen; der letzte **aktive** Admin ist geschützt; eine doppelte E-Mail-Adresse ergibt 409 statt 500.
- Konten mit SSO-/LDAP-Anmeldung haben kein Panel-Passwort: kein Passwort-Login, kein „Passwort vergessen“, kein Passwortwechsel, keine Passkeys. Ist die lokale Anmeldung abgeschaltet, sind auch Registrierung und „Passwort vergessen“ für Nicht-Admins aus.
- OIDC-Rücksprünge sind gedrosselt: nach 20 Fehlschlägen in 5 Minuten je IP `sso_error=rate_limited`. Ist OIDC aus, endet der Callback mit `disabled` ohne Audit-Eintrag.

*Records, Zonen, DNSSEC*

- **Bulk** `POST /records/{server}/{zone}/bulk`: `create` mit leerer `records`-Liste → 422 (zum Löschen `delete`); leere Anfrage → 422 statt 400; `create` und `delete` ohne `content` für dasselbe RRset → 422; fehlt ein zu löschender Wert → 404 mit `detail.issues`; mit `expected` braucht jedes geänderte RRset einen Fingerprint, sonst 409 (`force` übergeht das).
- **Record-Endpunkte:** fehlt die Zone auf dem Server aus der URL → 404 statt 502; fehlt ein einzelner Wert auf einem weiteren Server → `skipped (no matching content)` statt Fehler; neuer Status `skipped (no changes needed)`; `details.zone` endet mit Punkt, Record-Einträge im Audit-Log haben `details.version = 2`.
- **Mehrere Server:** zuerst der Server aus der URL, danach die weiteren, je auf Basis ihres eigenen Stands (Werte, die nur dort existieren, bleiben erhalten); Bulk je Server atomar; ist der Ausgang am ersten Server unklar, meldet das Panel „unklar – bitte Zone neu laden“. Das Zurücksetzen bringt alle schreibbaren Server auf den Vorher-Stand des Servers aus der URL.
- Generische Typangaben wie `TYPE65402` → 422.
- **LUA-Records schreiben nach dem Update nur Admins** (Policy `admin`); API-Tokens brauchen dafür `allow_admin`. Bei Policy `disabled` → 403 beim Anlegen und Ändern (Löschen bleibt erlaubt) und beim Zonen-Import von Dateien mit LUA-Zeilen. Bestehende LUA-Records bleiben unverändert.
- **Zonen-Import bei Policy `disabled`:** Die Prüfung liest die Datei wie PowerDNS (auch `IN IN LUA`, `TYPE065402`, `$GENERATE`, `$INCLUDE`; im Zweifel wird gesperrt). Der 403-Text endet mit „Betroffene Zeilen: N, M.“; das Fehler-Audit `IMPORT` zählt in `details.lua_count` die betroffenen Zeilen (bisher Records) und nennt sie in `details.lines`.
- Die Import-Vorschau vergleicht Namen im Record-Inhalt absolut (NS-, SOA-, MX-, CNAME-Ziele ohne Schein-Unterschiede) und liefert neu `lua_count`, `lua_issues`, `lua_policy`, `lua_blocked`, `lua_blocked_lines`.
- **`POST /zones`:** ungültige `dnssec_options` → 422; DNSSEC nur auf dem **ersten** angelegten Server, neue Ergebniswerte `created; dnssec-skipped` und `created; dnssec-error: <Grund>`; SOA-Hostmaster `hostmaster.<zone>`; „Speichern: Nein“ gilt auch für ausdrücklich gewählte Server.
- **DNSSEC-API:** `enable` auf einer signierten Zone → 200 mit `already_enabled` (nur inaktive Schlüssel → 409); `activate`/`deactivate`/`DELETE` → 409 bei Schutzregeln (`detail.code`) bzw. 403 auf Servern mit „Speichern: Nein“ – das gilt für alle schreibenden DNSSEC-Endpunkte; vorsignierte Zonen → 409; DNSSEC-Änderungen an Master-/Producer-Zonen erhöhen den SOA-Serial; Fehler als `PowerDNS (<server>): <meldung>`.
- **`POST /dnssec/…/disable`:** neuer 409 `parent_ds_present` (`force_possible: true`), wenn die Prüfung freigeschaltet ist und die Elternzone noch einen DS veröffentlicht; `{"force": true}` erzwingt. Im Panel deaktiviert ein eigener Dialog mit Pflicht-Haken; das DS-Fenster hat keinen Abschalt-Knopf mehr.
- **Vorlagen:** TTL außerhalb 60–604800 → 422; Delegations-NS sind löschbar, geschützt bleiben SOA und Apex-NS. LUA-Zeilen in Vorlagen werden geprüft.

*Einstellungen*

- **SMTP:** `PUT /settings/smtp` ohne `password` (oder mit der Maske) **behält** das Passwort, `""` löscht es. Wer Host, Port, Benutzer oder Verschlüsselung ändert, muss das Passwort neu mitschicken (400 `secret_reentry_required`), auch bei `POST /settings/smtp/test`. Gleiches gilt für LDAP-Server, Bind-DN, OIDC-Issuer und Client-ID.
- **PowerDNS-Server:** `PUT /settings/servers/{id}` mit geänderter `url` ohne neuen `api_key` → 400 `secret_reentry_required` mit `fields: ["url"]`. `GET /settings/servers/{id}/api-key` → 409 bei nicht lesbarem Key, 503, wenn der Audit-Eintrag nicht geschrieben werden kann.
- `PUT /settings/app-info`: `app_base_url: ""` leert die Basis-URL, `app_logo_url: ""` entfernt das Logo samt Datei.
- **Nicht lesbare Geheimnisse** werden gemeldet statt still gelöscht: Server `api_key_status: "unreadable"` (nicht geladen, auch nicht nach Speichern ohne neuen Key), SMTP `password_unreadable`, Captcha `secret_key_unreadable` (Prüfung bis zur Neueingabe ausgesetzt), Webhook `url_unreadable` (nichts wird gesendet).
- Die Versionsprüfung bei GitHub läuft nur noch in Admin-Sitzungen, höchstens alle 6 Stunden.
- DynDNS-Reparaturen (ein veralteter Server wurde angeglichen) lassen sich im Zonenverlauf nicht zurücksetzen (Sperrgrund `dyndns_repair`).

*Webhooks*

- **Webhooks werden ab 3.0 wirklich verschickt** – in 2.3.7 bis 2.4.x lief die Zustellung wegen eines fehlenden `await` nie. Empfänger vor dem Update prüfen.
- Payload Version 2 (`event_id`, `delivery_id`, `actor`, `zone`, `server`, `audit_log_id`; die bisherigen Felder stehen in `data`), Signatur über die gespeicherten Bytes, neue Header `X-DNS-Manager-Event`, `X-DNS-Manager-Delivery`, `X-DNS-Manager-Attempt`. Neue Ereignisse `zone.created|updated|deleted`, `dnssec.*`, `record.rollback`, `record.ptr_synced`, `dyndns.updated`; `record.bulk` enthält höchstens 200 `changes`.
- Die Eingabezeile „Ereignisse (kommagetrennt)“ entfällt, das Ziel wird nur als Host angezeigt. Beim Widerruf aller Zugänge werden wartende **und** fehlgeschlagene Zustellungen `cancelled`.

*Oberfläche*

- Neue Protokoll-Seite (Filter in der Adresse, Blättern); die Liste „letzte 200 Einträge ohne Filter“ entfällt.
- Neue Token-Verwaltung: Die alte Eingabezeile und die einfache Liste entfallen; „Widerrufen“ ist endgültig, „Pausieren“ umkehrbar.
- Die Standardsprache des Servers überschreibt eine gespeicherte Browser- oder Profilwahl nicht mehr.
- `/users` und `/audit` zeigen Nicht-Admins eine „Kein Zugriff“-Karte; der Tab „Monitoring“ erscheint nur mit Admin-Browser-Anmeldung.

**Nach dem Update prüfen**

- **Einstellungen → Sicherheit:** Verschlüsselung aktiv, Schlüssel gesichert (Fingerprint vergleichen). Bei „Schlüssel nur im Docker-Volume“ `./update.sh --backup-key-only` und das Schlüssel-Backup-Verzeichnis getrennt sichern.
- **Roter Hinweis „Geheimnisse nicht entschlüsselbar“:** die genannten Werte neu eintragen; PowerDNS-Server mit unlesbarem Key neu eintragen und danach ihre Zonen abgleichen.
- **Einstellungen → Monitoring → Systemstatus:** keine Migrationsfehler, keine nicht geladenen Server.
- **API-Tokens:** alle „Weitreichend“-Tokens auf die nötigen Zonen beschränken, Leserecht und Laufzeit setzen, `allow_admin` nur wo nötig. Admins prüfen in der Benutzerliste, welche Konten aktive Tokens haben.
- **Webhooks:** Empfänger bekommen jetzt wirklich Zustellungen – je Webhook „Test senden“ und das Zustellprotokoll ansehen; Webhooks mit „URL unlesbar“ oder „Secret nicht lesbar“ neu eintragen.
- **Reverse-Proxy:** `/metrics` nur für den Prometheus-Server, `/nic/update` durchreichen, `TRUST_PROXY_HEADERS=true` für DynDNS (sonst erscheint im Admin-Bereich ein gelber Hinweis).
- **Audit-Log → „Aufbewahrung“** festlegen (Standard unbegrenzt). Einträge aus Versionen vor 3.0 lassen sich nicht zurücksetzen.
- SMTP einmal testen; Vorlagen mit sehr kurzer oder langer TTL einmal speichern; Sprache und Datumsformate prüfen.
- Skripte gegen die Punkte oben prüfen.

**Geheimnisse verschlüsselt**

- Statuskarte unter **Einstellungen → Sicherheit**: Modus, Schlüsselquelle, Fingerprint (kein Geheimnis), Zusatzschlüssel, Zähler je Feld, Liste nicht lesbarer Einträge mit Sprung zum Neu-Eintragen. Ein roter Hinweis über jeder Seite meldet Probleme (für die Sitzung ausblendbar).
- Schlüsselwechsel über `SECRET_ENCRYPTION_KEY_PREVIOUS`; der nächste Start schlüsselt alles um.
- Kommandozeile `python -m app.cli.secrets` im Backend-Container: `status`, `generate-key`, `key-info`, `reset-unreadable`, `prepare-downgrade`, `decrypt-all` (schreibende Kommandos nur mit `--yes`).
- Wer die Zieladresse (SMTP-Host, LDAP-Server, OIDC-Issuer, PowerDNS-URL) ändert, muss das gespeicherte Passwort bzw. Secret neu eingeben – es wird nie an ein geändertes Ziel geschickt.
- Server-Liste mit „API-Key nicht lesbar“/„API-Key fehlt“; ein solcher Server wird nicht geladen und erscheint in Speicher-Ergebnissen als `skipped (not loaded: …)`.

**Scoped API-Tokens**

- Panel-Tokens lassen sich auf ausgewählte Zonen, „Nur lesen“ und eine Laufzeit (7–365 Tage oder ohne Ablauf; per API 1–3650 Tage) beschränken. Die Rechte eines Tokens sind immer die Schnittmenge aus Benutzerrechten und Token-Einschränkungen.
- „Admin-Funktionen erlauben“ (nur für Admins, nur ohne Zonen-Beschränkung): Zonen anlegen, importieren, löschen, Vorlagen verwalten, Benutzerliste und Audit-Log lesen. Einstellungen, Benutzerverwaltung und Zugangsdaten gehen nie per Token.
- Bearbeiten, Pausieren und Widerrufen je Token; Liste mit Zonen, Ablauf, „zuletzt benutzt“ (Zeit und IP) und Kennzeichen „Weitreichend“. Admins sehen die Tokens eines Benutzers und können sie widerrufen.

**Single Sign-On (OIDC, LDAP/Active Directory)**

- Anmeldung über OpenID Connect (Authorization Code Flow mit PKCE, strenge Prüfung des ID-Tokens) und LDAP/Active Directory (nur LDAPS oder StartTLS mit Zertifikatsprüfung, mehrere Server als Failover). Konten werden nur über eine stabile externe ID zugeordnet, nie über Benutzername oder E-Mail.
- Automatische Kontoanlage standardmäßig aus und nur mit erlaubten Gruppen, Domains oder ausdrücklicher Bestätigung; LDAP-Gruppen als vollständiger DN. Rollen optional aus Gruppen, ohne den letzten aktiven Admin herabzustufen.
- 2FA nach OIDC (abschaltbar), Selbst-Verknüpfung bestehender Konten, Umwandeln in ein lokales Konto, Notfallzugang `/login?local=1`. Kritische Änderungen verlangen eine erneute Bestätigung (Passwort und ggf. 2FA bzw. eine höchstens 10 Minuten alte SSO-Anmeldung); alle aktiven lokalen Admins bekommen danach eine E-Mail, sofern SMTP eingerichtet ist.
- Einstellungen unter **Einstellungen → Anmeldung / SSO** mit Vorlagen (Keycloak, Authentik, Entra ID, Active Directory, OpenLDAP, FreeIPA) und Verbindungstest.

**Benutzerverwaltung, Passwort-Reset & Zugangs-Widerruf**

- Dialog „Passwort & Sicherheit“: Passwort setzen, Zufallspasswort (einmal angezeigt), Reset-Link per E-Mail (24 Stunden gültig, einmal verwendbar), 2FA und Passkeys zurücksetzen.
- Erzwungener Passwortwechsel beim Anlegen und Zurücksetzen; Badges für Passwortwechsel, 2FA, Passkeys, aktive Tokens und SSO/LDAP.
- „Alle Zugänge widerrufen“: API-Tokens endgültig, DynDNS-Tokens gesperrt, Webhooks deaktiviert (offene Zustellungen verworfen), Browser-Sitzungen beendet, optional 2FA und Passkeys zurückgesetzt. Aktivieren/Deaktivieren direkt in der Liste, Rollenwechsel mit Rückfrage.

**Record-Historie & Audit-Log**

- Jede Record-Änderung speichert den vollständigen Zustand der betroffenen RRsets vorher und nachher (Audit-Format Version 2). Tab **„Verlauf“** je Zone mit Vorher/Nachher-Ansicht, Filtern und Verlauf je Record (Uhr-Symbol in der Zeile).
- **Zurücksetzen** mit Vorschau und Konfliktprüfung (`force` nach Bestätigung); bereits zurückgesetzte Stände zählen nicht als Konflikt. SOA, DNSSEC-Records und `_acme-challenge` werden nie zurückgesetzt; PTRs in Reverse-Zonen setzt ein Rollback nicht mit zurück (eigene `PTR_SYNC`-Einträge im Verlauf der Reverse-Zone).
- Protokoll-Seite mit Filtern (Aktion, Typ, Status, Benutzer, Zone, Server, Zeitraum, Volltext), teilbarer Adresse, Detailansicht und CSV-Export der gefilterten Einträge (geschützt gegen Formel-Injection). Einträge speichern Zone, Benutzername und Client-IP zum Zeitpunkt der Aktion.
- **Aufbewahrung** einstellbar: 0 = unbegrenzt oder 7–3650 Tage, stündliche Bereinigung. Nicht-Admins sehen im Zonenverlauf keine Client-IPs, Token-Kennungen oder PowerDNS-Fehlertexte.

**Webhooks mit Warteschlange**

- Ereignisse landen in derselben Transaktion wie Änderung und Audit-Eintrag in einer Warteschlange: Was gespeichert ist, wird gemeldet, was zurückgerollt wird, nicht.
- Zustellung im Hintergrund mit Wiederholungen nach ca. 1, 2, 4, 8 und 16 Minuten (6 Versuche); `Retry-After` wird beachtet, HTTP 410 und gesperrte Ziele beenden sofort. Jeder Versuch sendet denselben Body mit derselben Delivery-ID (Zustellung mindestens einmal).
- Zustellprotokoll je Webhook (30 Tage), „Test senden“, „Erneut senden“, Bearbeiten, Aktivieren/Deaktivieren, neues Secret; Auslöser „nur eigene Änderungen“ oder „alle Änderungen in meinen Zonen“; Ereignisauswahl als Baum.

**Bulk-Editor**

- Mehrfachauswahl in der Record-Tabelle (löschen, TTL setzen, (de)aktivieren) und **Text-Editor** im BIND-Format mit den Modi „Ergänzen“, „Ersetzen“ und „Geladene RRsets bearbeiten“ (versteht `$ORIGIN`, `$TTL`, Kommentare, Klammern, LUA/ALIAS und deaktivierte Werte).
- Vorschau vor jedem Speichern mit Vorher/Nachher je RRset und blockierenden Problemen; Löschungen müssen bestätigt werden. Wurde die Zone zwischen Vorschau und Speichern geändert, wird nichts überschrieben.
- Neuer Endpunkt `…/bulk/preview`; `/bulk` kennt zusätzlich `merge`, `set_ttl`, `set_disabled`, `expected`, `force` und `manage_ptr`.

**DNSSEC**

- Aktivieren mit Optionen: CSK oder KSK + ZSK, ECDSA P-256 (Standard) oder P-384, Ed25519, Ed448, RSA-SHA256/512 mit 2048/3072/4096 Bit, NSEC oder NSEC3 (neuer Standard `1 0 0 -` nach RFC 9276; bestehende Zonen bleiben unverändert). Auch beim Anlegen einer Zone.
- DNSSEC-Karte mit tatsächlichem Zustand, Hinweisen und Vergleich mit anderen Servern; Schlüsseltabelle (aktivieren, veröffentlichen, löschen) mit Schutzregeln für den letzten aktiven Schlüssel; NSEC/NSEC3 nachträglich ändern; Schlüssel hinzufügen.
- **Rollover-Assistent** für KSK/CSK und ZSK mit Wartezeiten, DS zum Kopieren und den Prüfungen „DNSKEY auf allen NS prüfen“ (fragt jeden autoritativen Nameserver ab) und „Elternzone prüfen“ (DS bei öffentlichen Resolvern) – beide nach Freischaltung unter Einstellungen → Monitoring.
- Neuer DS-Assistent (SHA-256 je aktuellem Schlüssel, Status „aktuell/neu/veraltet“). Fehlgeschlagene Schlüssel-Aktionen werden protokolliert; neue Webhook-Ereignisse `dnssec.key_created|published|unpublished`, `dnssec.nsec3_changed`.

**DynDNS**

- `GET /nic/update` (dyndns2-kompatibel, Textantwort) und `GET|POST /api/v1/dyndns/update` (JSON) für Router und Skripte; Antworten `good`, `nochg`, `badauth`, `nohost`, `notfqdn`, `numhost`, `badip`, `dnserr`, `911`, `abuse`.
- Eigene DynDNS-Tokens (`dnsmgr_ddns_…`) je Benutzer, nur für die gelisteten Hostnamen (A/AAAA), mit TTL und optionaler PTR-Pflege; Anleitung für FRITZ!Box, andere Router, curl und ddclient in der Einmal-Anzeige.
- **Der Token gehört nie in die URL:** Steht er im Query-String, wird die Anfrage abgelehnt und der Token sofort gesperrt (`DYNDNS_TOKEN_REVOKED`); aktivieren lässt er sich erst nach „Neues Secret“ (409 bis dahin). Basic-Auth-Passwort oder `Authorization: Bearer`.
- Missbrauchsschutz (über 30 Anfragen in 5 Minuten je Token → `911` mit `Retry-After`, ab 300 → `abuse`), Sperre der IP bei wiederholten ungültigen Tokens, Nachziehen veralteter Server, Admin-Schalter für den Endpunkt und private IP-Adressen.

**Reverse-DNS (PTR) automatisch**

- Option „PTR in Reverse-Zone mitpflegen“ für A/AAAA im Record-Dialog (mit Live-Prüfung je IP), im Bulk-Editor und für DynDNS; beim Löschen wird der passende PTR mit entfernt. Fremde PTRs werden nie überschrieben, Classless-Delegationen erkannt; jede Änderung steht als `PTR_SYNC` im Verlauf der Reverse-Zone.
- Admin-Standard unter **Einstellungen → DNS-Optionen** (ab Werk aus); API-Aufrufe ohne `manage_ptr` folgen ihm. Das Panel sendet `manage_ptr` immer ausdrücklich.

**Propagations-Check**

- Tab **„Propagation“** je Zone: Serial, NOTIFY-Stand und Antwortzeit je Panel-Server, autoritativem Nameserver und öffentlichem Resolver (Standard 1.1.1.1, 8.8.8.8, 9.9.9.9), optional Vergleich eines Records und des kompletten Zoneninhalts zwischen den Panel-Servern. Getrennte Datenbanken werden erkannt.
- Höchstens 8 Sekunden je Prüfung, Ergebnisse 10 Sekunden zwischengespeichert; Leserecht auf die Zone genügt (auch Tokens mit Zonen-Scope).

**Prometheus-Metriken**

- `/metrics` mit Scrape-Token (im Panel erzeugt oder `METRICS_TOKEN`): HTTP, PowerDNS-API, Logins, Webhooks, DynDNS, Propagations-Checks, Migrationsfehler, Hintergrund-Aufgaben, nicht lesbare Geheimnisse; nicht geladene Server erscheinen mit `pdnsmgr_pdns_server_up 0`.
- Tab **„Monitoring“** (Admins): Systemstatus, Freischaltung der externen DNS-Abfragen, Token-Verwaltung mit Beispielen für `prometheus.yml` und Grafana.

**LUA- & Geo-Records**

- Neuer Typ „LUA – dynamischer Record (Skript)“ mit Ziel-Typ (A, AAAA, CNAME, TXT, MX, SRV, PTR, CAA, NAPTR, LOC, SPF, HTTPS, SVCB, SSHFP, TLSA) und Lua-Code (höchstens 4000 Zeichen, einzeilig, keine doppelten Anführungszeichen im Code).
- Vorlagen für Failover (Port-/HTTP-Check), gewichtete Verteilung, Netz des Clients sowie Geo-Funktionen (`pickclosest`, `country`, `continent`, `latlon`); Live-Prüfung, Syntax-Hilfe und Warnbox mit dem LUA-Status jedes beteiligten PowerDNS-Servers.
- Policy unter **Einstellungen → DNS-Optionen → LUA-Records**: „Nur Administratoren“ (Standard), „Alle mit Schreibrecht“ oder „Deaktiviert“. Voraussetzung auf PowerDNS-Seite: `enable-lua-records=yes` in jeder `pdns.conf`, die die Zone ausliefert.
- Filter in der Record-Tabelle für alle Typen; die Import-Vorschau versteht LUA- und ALIAS-Zeilen.

**Zonenansicht: Export und NOTIFY**

- Knopf „Export“ (BIND-Zonendatei `<zone>.txt`, auch mit Leserecht) und „NOTIFY senden“ (mit Schreibrecht, für Master-, Producer- und Slave-Zonen); beides steht im Audit-Log (`ZONE_EXPORT`, `ZONE_NOTIFY`).

**Oberfläche & Übersetzungen**

- Schnellerer Start: Seiten und Sprachen (außer Englisch) werden erst bei Bedarf geladen; offene Tabs laden nach einem Update einmal neu statt mit einem Fehler stehen zu bleiben. Einstellungen sind per `/settings?tab=<reiter>` verlinkbar.
- Records: TTL als Zahlenfeld mit Vorgaben (60 Sekunden bis 7 Tage), deaktivierte Records gekennzeichnet, CAA mit vorbelegtem Flag und Tag, unbekannte Typen als Rohtext bearbeitbar, Löschen mit Rückfrage je Wert, bessere Prüfung von Namen.
- Ungarisch, Serbisch, Bosnisch und Kroatisch vollständig, korrekte Pluralformen in allen sechs Sprachen, Datum und Uhrzeit im Format der gewählten Sprache. Die Schrift wird lokal ausgeliefert – keine Anfragen an Google Fonts.
- Tastaturbedienung (Fokus im Dialog, Tab bleibt im Dialog, ESC schließt, Fokus-Rückgabe) in allen neuen und überarbeiteten Dialogen; bei übereinanderliegenden Dialogen reagiert nur der oberste.

**Behoben**

- Webhooks: Wegen eines fehlenden `await` wurde seit 2.3.7 kein Webhook zugestellt.
- Neue Zonen: Als SOA-Hostmaster stand bei zwei oder mehr Nameservern fälschlich der zweite; jetzt `hostmaster.<zone>`, der von PowerDNS vergebene Serial bleibt erhalten.
- Bulk-Änderungen auf mehreren Servern: Teilfehler und abweichende Stände weiterer Server führten zu Fehlern bzw. überschrieben dort vorhandene Werte.
- `POST /zones` beachtete „Speichern: Nein“ bei ausdrücklich gewählten Servern nicht.
- Die Suche kappte die Trefferliste vor der Zonenrechte-Prüfung (eigene Treffer wurden von fremden verdrängt).
- DNSSEC-Endpunkte prüften „Speichern: Nein“ nicht und protokollierten fehlgeschlagene Aktionen nicht.
- Eine bereits vergebene E-Mail-Adresse ergab beim Anlegen/Ändern eines Benutzers einen Serverfehler (jetzt 409).
- Der letzte Admin ließ sich deaktivieren (der Schutz galt nur beim Herabstufen und zählte deaktivierte Admins mit); das eigene Konto ließ sich deaktivieren und herabstufen.
- Die Login-Laufzeit verriet, ob ein Benutzername existiert (behoben für die Anmeldung ohne LDAP).
- Die Login-Sperre zählte nur je IP; jetzt zusätzlich je Benutzername.
- Verbindungsabbrüche zu PowerDNS brachen den Fan-out ab bzw. führten zu einem Serverfehler (500); nach einem Abbruch am ersten Server prüft das Panel jetzt nach, ob die Änderung angekommen ist.
- Audit- und Webhook-Einträge konnten verloren gehen, wenn das Speichern erst nach der Antwort scheiterte; geschrieben wird jetzt vor der Antwort.
- Ein neuer Panel-Token verschwand beim Klick auf „Kopieren“, auch wenn das Kopieren fehlschlug.
- Beim Löschen eines Records blieb unbemerkt, wenn ein weiterer Server die Änderung nicht übernahm.
- Ein Record mit 7200 s TTL zeigte beim Bearbeiten „1 Min“; `contest.de` in der Zone `test.de` galt als fremder Name; Delegations-NS ließen sich nicht löschen.
- Profilfelder ließen sich nicht leeren; nach einem Ladefehler der App-Einstellungen wurden beim Speichern Standardwerte geschrieben; SMTP-Angaben aus dem Einrichtungsassistenten wurden nicht gespeichert.

**API** (Details: [docs/PANEL-API.md](docs/PANEL-API.md))

- Neu: Zonenverlauf und Rollback (`/zones/{server}/{zone}/history…`), Propagation (`…/propagation`), Bulk-Vorschau (`/records/{server}/{zone}/bulk/preview`), DNSSEC `status`, `keys` (POST/PUT), `nsec3`, `parent-ds`, `dnskey-check`, LUA (`/lua/policy`, `/lua/server-status`), DynDNS (`/nic/update`, `/dyndns/…`), PTR (`/ptr/config`, `/ptr/lookup`), Audit-Log `/{id}` und `/settings`, Webhook-Test und Zustellprotokoll, Token-Bearbeitung und Admin-Tokenverwaltung, Benutzer-Werkzeuge (`send-reset-link`, `reset-2fa`, Passkeys entfernen, `access-summary`, `revoke-access`, `convert-to-local`), SSO (`/auth/sso/providers`, `/auth/oidc/…`, Verknüpfung), Einstellungen für Geheimnis-Status, SSO, DynDNS, PTR, Propagation, Metriken, Monitoring und LUA, Prometheus `/metrics`.
- Geändert: Audit-Log mit Filtern und `total`, CSV mit den Spalten `zone_name;username;revert_of_id` am Ende; Fan-out-Status `skipped (no changes needed)`, `skipped (no matching content)`, `skipped (not loaded: …)`; Record-Antworten mit `details.ptr` bei PTR-Pflege; `GET /auth/users` mit `auth_source`, `panel_token_count` und Block `sso`; `POST /auth/login/2fa` mit optionalem `two_factor_token`; `POST /setup/register` liefert das vollständige Benutzerobjekt.

**Betrieb, Skripte & Tests**

- `update.sh`: Dump mit Rechten 0600, Schlüsselkopie außerhalb des Stack-Ordners, Rechte von `/app/data` vor dem Start korrigiert, Warten auf das Backend (bis 120 Sekunden) mit Log-Ausgabe bei Startabbruch, Major-Box beim Sprung auf 3.x, neue Schalter `--backup-key-only` und `--no-key-backup`.
- `setup.sh` erzeugt `SECRET_ENCRYPTION_KEY` (vorhandene Werte werden übernommen), `install.sh` setzt ihn auch ohne `setup.sh`.
- Passwörter und Tokens in URL-Parametern werden im Access-Log als `***` maskiert.
- Neue End-to-End-Tests gegen MariaDB, zwei PowerDNS-Server und einen Webhook-Empfänger (Neuinstallation, Update einer 2.4.1-Datenbank, Downgrade-Weg) und automatische Browser-Tests der Oberfläche (`scripts/e2e/run-e2e.sh --ui`). Die Prüfungen bei jedem Pull Request umfassen die Vollständigkeit der Übersetzungen und Frontend-Tests.

**Abhängigkeiten & Lizenzen**

- Neu: Authlib 1.8.0 und joserfc 1.7.5 (BSD-3-Clause, OIDC), ldap3 2.9.1 (LGPL-3.0, unverändert als Bibliothek genutzt), prometheus_client 0.26.0 (Apache-2.0 AND BSD-2-Clause), `@fontsource-variable/inter` (OFL-1.1). `cryptography` ist jetzt direkt gepinnt (50.0.1, unverändert). Alle übrigen Python-Pakete wie in 2.4.1; alle Pakete liegen als Wheels für amd64 und arm64 vor.

### v2.4.1

Sicherheits- und Stabilitäts-Release nach einem vollständigen Code-Review. **Kein Schema-Bruch** – `./update.sh` reicht.

**Nach dem Update bitte prüfen**

- Alle Nutzer müssen sich einmal neu anmelden (Sessions sind jetzt an das Passwort gebunden).
- **Einstellungen → Profil → Öffentliche Basis-URL** (nur als Admin sichtbar) eintragen (z. B. `https://dns.example.com`), sonst werden keine Passwort-Reset-Mails mehr verschickt. Alternativ `WEBAUTHN_ORIGIN` in der `.env` setzen. Neuinstallationen setzen den Wert beim Setup automatisch.
- Ist `WEBAUTHN_RP_ID` auf eine übergeordnete Domain gesetzt (z. B. `example.com` bei Panel auf `dns.example.com`), muss jetzt `WEBAUTHN_ORIGIN=https://dns.example.com` gesetzt sein.
- Passkeys verlangen jetzt PIN oder Biometrie (User-Verification). Sicherheitsschlüssel ohne PIN funktionieren nicht mehr als Passkey; Passwort-Login (mit TOTP) bleibt für diese Nutzer möglich.
- Hinter einem Reverse-Proxy `TRUST_PROXY_HEADERS=true` setzen und den `Host`-Header durchreichen (`proxy_set_header Host $host`), sonst greift das Login-Rate-Limit für alle Nutzer gemeinsam bzw. der CSRF-Schutz lehnt Anfragen ab.

**Behoben (Sicherheit)**

- DNSSEC: Der Einzel-Key-Endpunkt lieferte den privaten Zonenschlüssel auch an Nutzer mit Nur-Lese-Recht aus. `privatekey` wird jetzt nie mehr ausgegeben, der Endpunkt verlangt Schreibrecht.
- Sessions und Passwort-Reset-Links sind an den aktuellen Passwort-Hash gebunden: Passwortwechsel oder Reset invalidiert alle bestehenden Sessions, ein Reset-Link funktioniert nur einmal.
- Reset-Links werden nicht mehr aus dem `Host`-Header der Anfrage gebaut, sondern nur aus der konfigurierten App-Basis-URL (Einstellungen → Profil → Öffentliche Basis-URL, beim Setup automatisch gesetzt) bzw. `WEBAUTHN_ORIGIN`.
- E-Mail-Adressen werden validiert (genau eine Adresse), der SMTP-Versand geht nur an diese Adresse, STARTTLS/SSL prüfen Zertifikate, Nutzerwerte in Mail-Templates werden HTML-escaped.
- TOTP-Codes und WebAuthn-Challenges sind nur einmal gültig (Replay-Schutz). Passkeys verlangen User-Verification (PIN/Biometrie), die Origin-Prüfung akzeptiert nur noch konfigurierte Origins bzw. den exakten RP-Host.
- Passwort, E-Mail, TOTP, Passkeys und Panel-Tokens lassen sich nur noch aus einer Browser-Session verwalten, nicht mit einem Panel-API-Token.
- Hinter Reverse-Proxy: `TRUST_PROXY_HEADERS` wertet jetzt den vom eigenen Proxy angehängten `X-Forwarded-For`-Eintrag aus (neu: `TRUSTED_PROXY_HOPS`), validiert IPs und verhindert das Umgehen des Login-Rate-Limits. Das Rate-Limit legt bei Lese-Checks keine Einträge mehr an.
- Abhängigkeiten: `python-multipart` 0.0.32 (vier CVEs), Frontend-Pakete per `npm audit` bereinigt (vite, react-router, postcss).

**Behoben (Funktion)**

- Der Papierkorb in der Record-Tabelle löscht nur noch den geklickten Wert, nicht mehr das komplette RRset (z. B. alle MX- oder TXT-Werte). Audit-Log und Webhook enthalten den gelöschten Inhalt.
- Der Bulk-Endpunkt `POST /records/{server}/{zone}/bulk` war durch die allgemeine Record-Route verdeckt und nie erreichbar.
- Fehlt `JWT_SECRET_KEY` in der `.env`, wird ein Schlüssel einmalig im internen Daten-Volume `backend_data` abgelegt und wiederverwendet (vorher: bei jedem Neustart neue Sessions). Dotfiles werden aus `/uploads` nie ausgeliefert.
- Content-Security-Policy (neu, standardmäßig aktiv) lässt Swagger/ReDoc, externe Logo-URLs und alle Captcha-Provider zu.
- CSRF-Schutz für Cookie-Sessions: zustandsändernde API-Aufrufe müssen von der eigenen Seite kommen (`Sec-Fetch-Site`/`Origin`), Bearer-Token-Aufrufe sind davon unberührt.
- Webhooks verbinden sich zur geprüften IP (kein DNS-Rebinding mehr), CGNAT-/NAT64-Bereiche werden geblockt.
- Audit-Log deckt jetzt auch Logins (Erfolg/Fehlschlag mit IP), Benutzer-, Rollen- und Zonenrechte-Änderungen, Passwort-/2FA-/Passkey-/Token-Aktionen sowie Server-, SMTP- und ACME-Token-Änderungen ab. Fehler-Einträge gehen nicht mehr durch das Rollback verloren.
- Zonenrechte werden beim Löschen einer Zone mit entfernt; `kind`, `masters` und `account` einer Zone dürfen nur Admins ändern.
- PowerDNS-Validierungsfehler (422) werden als Fehler gemeldet statt als „Zone fehlt“ übersprungen; Zonen-Import legt die Zone auf jedem schreibbaren Server an; Fan-out-Teilfehler werden in der Oberfläche angezeigt.
- Ein deaktivierter, aus `PDNS_SERVERS` importierter Server führt beim Neustart nicht mehr in eine Crash-Schleife.
- Zeitstempel kommen mit UTC-Offset, die Oberfläche zeigt Audit-Log, letzten Login und Token-Nutzung damit in korrekter Ortszeit.
- Mailversand und SMTP-Test blockieren den Server nicht mehr.
- Skripte: `install.sh` sichert eine vorhandene `.env`, bevor ein Ordner überschrieben wird; `update.sh` gibt dem Uploads-Volume den App-Benutzer.
- Abhängigkeiten aktualisiert (fastapi 0.141, uvicorn 0.53, sqlalchemy 2.0.52, pydantic 2.13.5, pydantic-settings 2.15, cryptography 50), `.dockerignore` ergänzt, tote Dateien (`test.py`, `Dockerfile.simple`) entfernt.
- Doku: korrekter Clone-Ordner, funktionierender Installations-One-Liner, Reverse-Proxy-Hinweise zu `TRUST_PROXY_HEADERS` und Port-Bindung, SECURITY.md aktualisiert, `docs/PANEL-API.md` an die tatsächliche API angepasst.
- Die Laufzeit des Session-Tokens folgt jetzt `AUTH_COOKIE_MAX_AGE` (vorher lief das Token nach 24 h ab, obwohl der Cookie 30 Tage galt).

**Betrieb, Skripte**

- Port-Bindung ohne Eingriff in die `compose.yaml`: `BIND_ADDR` (z. B. `127.0.0.1` hinter einem Reverse-Proxy) und `HOST_PORT` kommen aus der `.env`; `update.sh` warnt vor `git checkout`, wenn versionierte Dateien lokal geändert wurden. `.env.example` lässt die DB-Passwörter leer, damit der Compose-Schutz greift.
- `setup.sh` fragt kein SMTP mehr ab – die Werte landeten in der `.env`, wurden aber nie ausgewertet; SMTP wird im Panel unter Einstellungen → SMTP konfiguriert. Der Wizard schreibt jetzt `TRUST_PROXY_HEADERS`/`TRUSTED_PROXY_HOPS` (Ja bei der HTTPS-Frage → `true`), akzeptiert `j`/`y`, hat einen Default für den Admin-Modus und nennt bei festem Passwort den Benutzernamen `admin`.
- `install.sh` nutzt `sudo` auch für `docker inspect`, fasst nach einem Tarball-Download kein übergeordnetes Fremd-Git-Repo mehr an und nennt am Ende den passenden Login-Weg; `update.sh` übergibt das DB-Passwort für den Dump per `MYSQL_PWD` statt als Argument.

### v2.4.0

Passwortlose Anmeldung mit **Passkeys / WebAuthn**. Kein Schema-Bruch – `./update.sh` reicht (eine neue Tabelle `webauthn_credentials` und eine Spalte werden automatisch angelegt, bestehende Logins bleiben unverändert).

**Passkeys (FIDO2 / WebAuthn)**

- **„Mit Passkey anmelden" auf der Login-Seite** – ein Klick, dann Fingerabdruck/Gesicht/PIN oder Sicherheitsschlüssel. Funktioniert usernameless (discoverable credentials), es muss also kein Benutzername getippt werden.
- **Verwaltung unter Einstellungen → API & Sicherheit** – beliebig viele Passkeys pro Konto anlegen, benennen (z. B. „iPhone", „YubiKey") und einzeln entfernen.
- **Sicher & standardkonform:** Server speichert nur den öffentlichen Schlüssel; der private Schlüssel verlässt das Gerät nie. Phishing-resistent, an die Domain gebunden. Backend `py_webauthn`, Frontend `@simplewebauthn/browser`.
- **Stateless-Challenges:** kurzlebige, signierte Challenge-Tokens (gleiches Muster wie der 2FA-Pending-Token) – kein zusätzlicher Server-State.
- **Rate-Limiting** greift auch beim Passkey-Login. Ein Passkey gilt als starke Multi-Faktor-Anmeldung (Besitz des Geräts + Biometrie/PIN), daher entfällt dabei die zusätzliche TOTP-Abfrage. Der klassische Passwort-Login inklusive TOTP-2FA bleibt unverändert.
- **Domain-Erkennung automatisch** (localhost wie Produktiv-Domain). Für Produktion per `WEBAUTHN_RP_ID` (ohne Schema/Port) fixierbar – siehe `.env.example`. Passkeys benötigen HTTPS (Ausnahme: `http://localhost` zum Testen).

### v2.3.8

Branding-Rename und Härtung der Installations-/Update-Skripte. **Kein Schema-Bruch, keine technischen Identifier geändert** – `./update.sh` reicht. Bestehende Installationen behalten Datenbank, Container, Tokens, Webhook-Header und Cookies unverändert.

**Branding**

- Anzeigename überall „**PDNS Manager**" (vorher „DNS Manager"). Repo umbenannt zu [`29barra29/PowerDNS-PDNS-MANAGER`](https://github.com/29barra29/PowerDNS-PDNS-MANAGER) – alle Skripte, Docs, Locales (en/de/sr/hr/bs/hu, je 770 Keys), README-Badges und E-Mail-Templates ziehen mit.
- Bewusst **nicht** umbenannt (würde existierende Installationen brechen): DB-Name `dns_manager`, Container `dns-manager-api`/`dns-manager-db`, Token-Prefixes `dnsmgr_*`, Webhook-Header `X-DNS-Manager-Signature`, Cookie-Namen.

**install.sh**

- **Tarball-Fallback pinnt jetzt das neueste Release-Tag** statt blind `main` zu ziehen (vorher landeten Tarball-User auf instabilem main). API-Lookup bei `api.github.com/repos/.../releases/latest`, Fallback mit klarer Warnung.
- **Port-Check mit Fallback-Kette** `lsof → ss → netstat` – funktioniert auch auf Minimal-Distros ohne `lsof`.
- **`openssl`-Verfügbarkeit wird geprüft**, bevor sichere Passwörter generiert werden – sonst klare Fehlermeldung statt kryptischem Compose-Abbruch.
- **Aktive Healthcheck-Polling-Schleife** auf `GET /health` (max. 120 s) statt blindem `sleep 10` und reinem `docker ps`. Backend ist erst als „healthy" markiert, wenn der Endpoint wirklich antwortet (DB-Init / Migrationen können dauern).
- **Generischere Container-Prüfung** via `compose ps -q <service>` + `docker inspect`, nicht mehr von festen Container-Namen abhängig.
- Default-Install-Pfad: `./pdns-manager` (vorher `./dns-manager`) – betrifft nur Neu-Installationen.

**setup.sh**

- **Zeitgestempeltes `.env`-Backup** (`.env.backup.<YYYYMMDD-HHMMSS>`), kein Überschreiben älterer Backups mehr.
- **Sprach-Prompt** im Standalone-Aufruf (vorher hartkodiert „de").
- **Atomisches Schreiben** in `.env.tmp` → `mv .env`, dazu `trap` für Cleanup bei Ctrl-C – keine halbfertigen `.env`-Dateien mehr.
- **`WEBHOOK_ALLOW_PRIVATE_URLS=false`** wird mit erklärendem Kommentar in die `.env` geschrieben.

**update.sh**

- **`--no-cache` nur noch bei tatsächlichem Versionswechsel** oder explizitem `--rebuild`-Flag. Spart 3-5 min bei Patch-Updates ohne Dependency-Änderung.
- **DB-Backup-Frage vor jedem Update** (überspringbar mit `--no-backup`). Schreibt `backup_<version>_<ts>.sql` via `mysqldump --single-transaction` direkt in den Repo-Ordner.
- **Major-Version-Sprung wird gemeldet** mit roter Warn-Box und aktiver Bestätigung – verhindert versehentliche Migrationen ohne Changelog-Lektüre.
- **Generische Compose-Statusliste** statt hartkodiertem `name=dns-manager`-Filter.
- `git fetch` bewusst **ohne `--prune-tags`** – lokale Maintainer-Tags überleben.
- Neue Flags: `--rebuild`, `--no-backup`, `--skip-fetch`, `--help`.

**Dokumentation**

- `INSTALL.md`, `CONTRIBUTING.md`, `SECURITY.md`, `docs/PANEL-API.md`, `frontend/README.md` durchgehend auf neuen Namen / neue Repo-URL umgestellt.

---

## Mitwirken

PRs sind willkommen, bei größeren Sachen vorher gerne ein Issue. Ablauf, Tests und PR-Checkliste stehen in [CONTRIBUTING.md](CONTRIBUTING.md), der Aufbau des Frontends in [frontend/README.md](frontend/README.md).

### Eine neue Sprache beisteuern

Aktuell drin: Deutsch, Englisch, Serbisch, Kroatisch, Bosnisch, Ungarisch. Weitere kommen gerne als PR – Ablauf:

1. `frontend/src/locales/en.json` als Vorlage kopieren, z. B. zu `it.json`, und alle Werte übersetzen (Keys nicht anfassen, Platzhalter wie `{{count}}` übernehmen, Pluralformen laut `Intl.PluralRules` der Sprache).
2. In `frontend/src/i18n.js` einen Eintrag in `LANGUAGES` (Code, Label, Flag-Emoji) ergänzen – die Datei wird automatisch nachgeladen.
3. Damit die Sprache im Profil gespeichert werden kann, den Code in `PROFILE_LANGUAGES` in `backend/app/routers/auth.py` ergänzen. E-Mails gibt es auf Deutsch und Englisch; andere Sprachen erhalten englische E-Mails.
4. `cd frontend && npm run check:locales -- --strict` muss ohne Fehler durchlaufen, dann PR aufmachen.

### Übersetzungen pflegen

`frontend/src/locales/en.json` ist die Referenz; alle sechs Sprachdateien haben dieselben Keys. Neue oder geänderte Texte kommen nicht direkt in die Sprachdateien, sondern als **Fragment** je Sprache nach `frontend/src/locales/fragments/<name>.<sprache>.json` (Format und Regeln: [frontend/src/locales/fragments/README.md](frontend/src/locales/fragments/README.md)). Deutsch und Englisch von Hand, die übrigen Sprachen übersetzt – nicht aus dem Englischen kopiert.

```bash
cd frontend
node scripts/merge-locale-fragments.mjs --check     # Fragmente auf Konflikte prüfen, schreibt nichts
node scripts/check-locales.mjs --with-fragments     # Regeln auf dem zusammengeführten Stand prüfen
```

Im Pull Request reichen die Fragmente. Vor dem Release übernimmt `npm run merge:locales` sie in die Sprachdateien und löscht sie; danach muss `npm run check:locales -- --strict` (wie in der CI) fehlerfrei sein.

Die Prüfung meldet fehlende Keys und Pluralformen, abweichende Platzhalter, leere Werte, unbekannte Keys im Code und Werte, die unverändert aus dem Englischen stammen (Ausnahmen mit Begründung in `frontend/scripts/locale-allowlist.d/`). Fehlt zur Laufzeit trotzdem ein Key, zeigt die Oberfläche den englischen Text.

### Versionspflege (für Maintainer)

Die App-Version steht **ausschließlich** in der Datei `VERSION` im Projektroot. Backend, API und UI lesen sie von dort. Vor einem Release:

```bash
echo "X.Y.Z" > VERSION                     # neue Version, z. B. 3.0.1
./scripts/update-readme-from-version.sh   # Badge + git-checkout-Beispiel im README anpassen
```

Die Release-Notes eines Releases entstehen aus den Fragmenten unter `docs/release-notes/<version>/` (Übersicht für 3.0.0: [docs/release-notes/3.0.0/INDEX.md](docs/release-notes/3.0.0/INDEX.md)).

### Lokale Entwicklung ohne Docker

```bash
# Backend (braucht eine erreichbare MariaDB, DATABASE_URL setzen)
cd backend
pip install -r requirements.lock
DATABASE_URL='mysql+aiomysql://user:pass@localhost:3306/dns_manager' uvicorn app.main:app --reload --port 5380

# Frontend (in zweitem Terminal) – Vite auf http://localhost:3000, /api geht an localhost:5380
cd frontend
npm install
npm run dev
```

Tests und Prüfungen: siehe [CONTRIBUTING.md](CONTRIBUTING.md#tests-und-prüfungen).

---

## Lizenz

MIT – also nutzbar wie es passt, auch in Firmenkontext. Mitgelieferte Abhängigkeiten behalten ihre eigenen Lizenzen (siehe [Abhängigkeiten & Lizenzen](#abhängigkeiten--lizenzen)).

## Credits

Gebaut von [29barra29](https://github.com/29barra29). Teilweise mit KI-Unterstützung entwickelt – wer Bugs findet oder Verbesserungen sieht: Issue oder PR auf.
