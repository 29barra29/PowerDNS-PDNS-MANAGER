# PDNS Manager

Ein Web-Panel für **PowerDNS Authoritative Server** zum Self-Hosten. Entstanden aus dem Wunsch, PowerDNS-Admin durch etwas Aufgeräumteres mit aktuellem Stack zu ersetzen.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)
![PowerDNS](https://img.shields.io/badge/PowerDNS-4.x-orange.svg)
![Version](https://img.shields.io/badge/version-v2.4.1-blue.svg)

---

## Demo

![PDNS Manager – Einstellungen / Profil](docs/screenshots/settings-profile.jpg)

*Beispiel-Ansicht: Einstellungen → Profil mit angepasstem Branding (Logo, App-Name).*

Ein Video-Walkthrough (Installation, ersten Server anbinden, Zone + DNSSEC anlegen) folgt auf YouTube. Sobald es online ist, steht der Link hier. Wer zwischendurch fragen hat: einfach ein Issue aufmachen.

## Dokumentation

Ausführliche Doku, Screenshots, Setup-Guides, Changelog & FAQ:
**[pdns-manager.gemtecgames.com](https://pdns-manager.gemtecgames.com)**

Dieses README gibt nur den Schnellüberblick (Installation, Stack, Troubleshooting). Tiefergehende Anleitungen — PowerDNS-Anbindung, ACME / Auto-TLS, Webhooks, Multi-Server-Sync, API-Token, Branding — stehen auf der Doku-Seite und werden dort gepflegt.

---

## Was es kann

- **Mehrere PowerDNS-Server (4.x) parallel verwalten** – mit Verbindungstest pro Eintrag, Bearbeiten direkt aus den Einstellungen, Suche über alle Server hinweg (auch Teil-Strings).
- **Zonen + Records** – die üblichen Typen (A, AAAA, CNAME, MX, TXT, SRV) per Formular; dazu ALIAS, DNAME, SVCB/HTTPS und sämtliche DNSSEC-Records (DS, DNSKEY, RRSIG, …) als RDATA-Text mit Hilfetexten und Beispielen.
- **DNSSEC** pro Zone an- und ausschalten.
- **Zonen-Vorlagen** – eigene Templates mit NS, SOA und Standard-Records, beim Anlegen einer neuen Zone auswählbar.
- **Multi-Server an einer DB** – pro Server-Eintrag steuerbar, ob diese Instanz auch tatsächlich schreibt. Praktisch wenn mehrere PowerDNS-Server auf dieselbe Backend-DB zeigen.
- **Benutzer mit zwei Rollen** – Admin sieht alles, Benutzer nur die ihm zugewiesenen Zonen.
- **Audit-Log** – jede relevante Änderung mit Zeitstempel und User.
- **SMTP** – optional für Passwort-Reset und Benachrichtigungen, mit Test-Mail-Funktion.
- **Welcome-Mail** – optional bei Selbst-Registrierung, Subject und Body mit Platzhaltern (`{username}`, `{login_url}`, …) frei editierbar, Live-Vorschau und Test-Senden in den Einstellungen.
- **Captcha** – optional gegen Bots/Brute-Force auf Login, Registrierung und Passwort-Reset. Cloudflare Turnstile, hCaptcha oder Google reCAPTCHA v2; Site-Key + Secret werden im Admin-Panel gepflegt, Verifikation läuft serverseitig.
- **Passkeys / WebAuthn (Anmeldung mit einem Klick)** – passwortlose Anmeldung per Fingerabdruck, Gesicht oder Sicherheitsschlüssel (Touch ID, Face ID, Windows Hello, YubiKey …). Jeder Nutzer kann mehrere Passkeys unter **Einstellungen → API & Sicherheit** anlegen; auf der Login-Seite gibt es dann den Button „Mit Passkey anmelden". Der private Schlüssel verlässt nie das Gerät, der Server speichert nur den öffentlichen Schlüssel – phishing-sicher. Ein Passkey ist bereits starke Multi-Faktor-Anmeldung (Gerät + Biometrie/PIN), daher entfällt beim Passkey-Login die zusätzliche TOTP-Abfrage; Passwort-Login mit TOTP bleibt parallel nutzbar. Domain wird automatisch erkannt, für Produktion per `WEBAUTHN_RP_ID` fixierbar.
- **Branding** – eigener App-Name, Tagline und Logo (z. B. Firmenlogo); Logo bleibt bei Updates über das Volume `backend_uploads` erhalten.
- **Mehrsprachige Oberfläche** – Sprach-Dropdown mit Flaggen, aktuell Deutsch, Englisch, Serbisch, Kroatisch, Bosnisch und Ungarisch. Default in der `.env` einstellbar. Fehlt mal ein Key, fällt die UI automatisch auf Englisch zurück; weitere Sprachen kommen per PR an `frontend/src/locales/<code>.json`.
- **Mobile-tauglich** – einklappbare Sidebar mit Hamburger-Menü, Tabellen scrollen horizontal statt das Layout zu sprengen.
- **ACME / Auto-TLS** – scoped API-Tokens fürs DNS-01-Challenge. certbot legt `_acme-challenge`-TXT-Records selbst an, Zertifikate erneuern sich vollautomatisch. Token kann ausschließlich `_acme-challenge.*`-TXTs in den freigegebenen Zonen schreiben/löschen, nichts anderes; fertiges Hook-Skript (`scripts/certbot-dns-dnsmanager.sh`) liegt im Repo.

**Panel-API & Skripte:** Voller API-Zugriff (wie die Web-UI) geht per **Panel-API-Token** im Header `Authorization: Bearer …` (siehe Einstellungen → API & Sicherheit). Kurzdoku und curl-Beispiel: [`docs/PANEL-API.md`](docs/PANEL-API.md). Optional: interaktive API unter `/docs`, wenn `DOCS_ENABLED=true` gesetzt ist.

## Was es bewusst nicht macht

- Kein DHCP, kein Recursor, kein Slave-DNS-Setup-Tool – das hier verwaltet **Authoritative Zones**.
- Keine Multi-Tenant-Mandantentrennung (keine getrennten „Kunden"). Wer das braucht, ist mit PowerDNS-Admin oder einer kommerziellen Lösung besser bedient.
- Kein eingebauter Reverse-Proxy / kein TLS – das macht Caddy / nginx / Traefik / Cloudflare Tunnel davor.

---

## Installation

### Voraussetzungen

Docker und Docker Compose. Port `5380` muss frei sein – wer ihn ändern oder nur lokal binden will, setzt `HOST_PORT=…` bzw. `BIND_ADDR=127.0.0.1` in der `.env` (die `compose.yaml` selbst nicht editieren, sonst scheitert `update.sh` am `git checkout`).

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
# EMPFOHLEN: JWT_SECRET_KEY setzen – fehlt er, erzeugt das Backend einmalig einen
# Schlüssel im Volume backend_data (Warnung im Log). Beispiel:
#   sed -i "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=$(openssl rand -hex 64)|" .env
nano .env
chmod 600 .env
docker compose up -d
```

### Erster Login

1. Browser auf `http://localhost:5380`.
2. Ist `ENABLE_REGISTRATION=true` in der `.env` gesetzt (Default ist `false`), wird der Setup-Wizard im Browser angezeigt – der erste angelegte User ist automatisch Admin. Anschließend stellt sich die Registrierung selbst ab.
3. Hat das Setup ein festes Admin-Passwort vergeben, ist der Username `admin`. Wurde gar kein Passwort gesetzt, generiert das Backend beim ersten Start eines und legt es ab unter `/app/.initial-admin-password` im Container plus einmalig im Container-Log:
   ```bash
   docker compose logs backend | grep -i "initial admin"
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
webserver-allow-from=0.0.0.0/0
```

Dann `systemctl restart pdns`.

---

## Updates

Solange das Projekt mit `git clone` installiert wurde (also bei `install.sh` oder Variante B), genügt:

```bash
cd /pfad/zu/dns-manager
./update.sh
```

`update.sh` macht der Reihe nach:

1. `git fetch` mit Tags. Nach der One-Click-Installation steht der Checkout meist auf einem Release-Tag, nicht auf `main` – ein nacktes `git pull` würde da nichts machen. Das Skript wechselt deshalb auf das jeweils neueste `v*`-Tag (oder aktualisiert `main`, falls das die aktuelle Branch ist).
2. `docker compose build --no-cache backend` und `up -d`.
3. Datenbank, `.env` und Logo bleiben unangetastet (`backend_uploads`-Volume).

Vor dem Update lohnt sich ein DB-Dump – `update.sh` bietet ihn automatisch an (`backup_<version>_<zeitstempel>.sql`). Manuell, mit dem Root-Passwort aus dem Container-Environment (ein nacktes `-p` würde ohne Terminal ein leeres Backup schreiben):

```bash
docker exec dns-manager-db sh -c 'mysqldump --single-transaction -u root -p"$MARIADB_ROOT_PASSWORD" dns_manager' > backup_$(date +%Y%m%d).sql
```

Manuell auf eine bestimmte Version wechseln:

```bash
cd PowerDNS-PDNS-MANAGER
git fetch origin --tags --force --prune
git checkout v2.4.1              # oder: git checkout main && git pull
docker compose build --no-cache backend
docker compose up -d
```

---

## Sicherheit

Was beim Setup automatisch passiert:

- `JWT_SECRET_KEY` wird mit `openssl rand -hex 64` erzeugt. Fehlt er in der `.env` (etwa bei Variante C), erzeugt das Backend seit 2.4.1 einmalig einen Schlüssel im Volume `backend_data` und warnt im Log – für Produktion trotzdem selbst setzen.
- DB-Passwörter werden zufällig generiert.
- `.env` bekommt `chmod 600`.
- Die OpenAPI-/Swagger-Doku unter `/docs` ist standardmäßig aus (`DOCS_ENABLED=false`). Wer sie braucht, setzt die Variable in der `.env` auf `true`.
- Passwort-Hashing über `pwdlib` mit bcrypt. Alte Hashes aus passlib-Zeiten bleiben gültig.

Was du selbst noch machen solltest:

- Admin-Passwort nach dem ersten Login ändern.
- Reverse-Proxy mit TLS davorschalten (Caddy ist mit Abstand am schnellsten aufgesetzt). Sobald HTTPS läuft, in der `.env` `AUTH_COOKIE_SECURE=true` setzen und einmal `docker compose up -d` – sonst bleibt der Login-Cookie unter HTTPS unzuverlässig.
- `ENABLE_REGISTRATION=false` setzen, sobald alle Accounts angelegt sind.
- Port `5380` nicht öffentlich lassen: `BIND_ADDR=127.0.0.1` in der `.env` setzen (eine Host-Firewall greift bei Docker-Ports nicht; die `compose.yaml` nicht editieren, sonst scheitert `update.sh` am `git checkout`). Hinter dem Proxy `TRUST_PROXY_HEADERS=true` setzen, sonst teilen sich alle Nutzer ein Login-Rate-Limit.
- Optional Fail2Ban auf die Reverse-Proxy-Logs.

Kompletter nginx-Block und Traefik-/Cloudflare-Tunnel-Beispiele stehen in [INSTALL.md](INSTALL.md) im Abschnitt „HTTPS mit Reverse Proxy".

---

## Stack

| Komponente | Was läuft hier |
|---|---|
| Frontend | React 19 + Vite 8 (Rolldown) + Tailwind CSS 4 + i18next 26 |
| Backend | Python 3.12 + FastAPI 0.136 + SQLAlchemy 2 (async) + Pydantic 2.13 |
| Datenbank | MariaDB 11 (Async-Treiber `aiomysql`) |
| Auth | JWT in HttpOnly-Cookie, Hashing über `pwdlib` + bcrypt |
| DNS | PowerDNS Authoritative 4.x (über die HTTP-API) |
| Container | Docker Compose, Multi-Stage Build, Backend läuft als Non-Root |

---

## Projektstruktur (grob)

```
dns-manager/
├── VERSION                # einzige Stelle, an der die App-Version steht
├── compose.yaml           # Stack-Definition
├── .env.example           # Vorlage – wird zu .env (nicht im Git)
├── install.sh / setup.sh / update.sh
├── scripts/               # Maintainer-Skripte (z. B. README-Versions-Sync)
├── backend/               # FastAPI-App + Dockerfile
└── frontend/              # React-App (wird im Backend-Image als Static ausgeliefert)
```

---

## Troubleshooting

### Compose meldet „DB_PASSWORD ist leer …" beim Start

Das ist die seit v2.3.3 eingebaute Schutz-Schiene: ohne gesetzte Passwörter wird die DB nicht initialisiert. Lösung:

```bash
./setup.sh                          # legt eine vollständige .env an
# oder manuell: cp .env.example .env und Passwörter eintragen
docker compose up -d
```

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

### v2.3.7

Größeres Release mit Records-Sync, Stabilität und vollständiger i18n. Kein Schema-Bruch – `./update.sh` reicht.

**Records & Multi-Server-Sync**

- **Fan-out beim Schreiben:** `POST/PUT/DELETE` und Bulk-Updates auf Records werden jetzt automatisch auf **alle** Server angewendet, die `Speichern: Ja` (`allow_writes=true`) haben **und** die Zone führen. Read-only Server werden hart abgelehnt (HTTP 403) statt heimlich übergangen. Per-Server-Status (ok / skipped / error) liegt im Response-Body unter `details`.
- **Frontend kennt schreibbare Server:** `/api/v1/servers` liefert pro Server `allow_writes` mit. Klick auf eine Zone in der Liste navigiert automatisch zu einem schreibbaren Server (sofern vorhanden); ist nur ein Read-Only-Server erreichbar, gibt es im Zonen-Detail einen klaren Hinweis und Schalter zum Wechsel.
- **Synchronisierte Anzeige:** Zonenliste, Suche und Dashboard fassen identische Zonen/Records über mehrere Server zu jeweils einer Zeile zusammen. Server, auf denen die Zone existiert, werden als Badges angezeigt – kein doppeltes Auflisten mehr.
- **Zonen-Detail-Banner:** Aktueller Server schreibgeschützt → rote Warnung + Wechsel-Button auf einen Peer mit `allow_writes=true`. Zusätzliche schreibbare Peers vorhanden → Info-Banner, dass jede Änderung auf allen entsprechenden Servern landet.

**Zonenrechte (Lesen / Voll)**

- In **Benutzer → Zonen zuweisen** pro Zone wählbar. „Nur lesen“ blockiert API-seitig alle schreibenden Aktionen (Records, DNSSEC ein/aus, NOTIFY). In der Zonendetailansicht erscheint ein Hinweis, Buttons sind deaktiviert.

**Sicherheit**

- **Login-Rate-Limit:** IP-basierte Sperre nach mehreren fehlgeschlagenen Logins (HTTP 429, gleitendes Fenster) – Brute-Force-Schutz, ohne legitime User dauerhaft auszusperren.
- **Webhook SSRF-Schutz:** Webhook-URLs werden vor dem Speichern und vor jedem Versand geprüft. Localhost / private / link-local / multicast-Ziele sind standardmäßig blockiert. Wer das in einem internen Netz braucht, setzt `WEBHOOK_ALLOW_PRIVATE_URLS=true` in der `.env`.
- **PowerDNS-Fehler:** 5xx-Antworten von PowerDNS landen mit vollständigem Detail im Backend-Log; nach außen geht eine kundenfreundliche Meldung ohne Stacktrace-Leck.

**i18n**

- Alle 6 Sprachen (en / de / sr / hr / bs / hu) sind jetzt **vollständig synchronisiert** auf identische Key-Liste (770 Keys). Vorher fehlten in bs/hr/hu/sr ca. 60 Keys (Integrations-Tab, common-Errors, Zonen-Detail-Hinweise, Zone-Import). Pflege weiter via `node scripts/sync-locales.mjs`.

**Tests**

- Pytest-Suite mit echten Modultests: DNSSEC-Parse, ACME-Helfer, Zonen-ACL, Login-Rate-Limit, Webhook-URL-Validierung, Health.
- `backend/pytest.ini` für konsistente Test-Pfade.

**Audit**

- Admin kann das Log als **CSV** exportieren (`GET /api/v1/audit-log/export`, Semikolon + UTF-8-BOM für Excel) – Button im Audit-Log.

**Health-Check**

- `GET /health` prüft die Datenbank mit `SELECT 1` (kein fester `database: connected` mehr). Wenn die DB weg ist: HTTP 503 + `status: unhealthy`.
- `compose.yaml` hängt einen Healthcheck am Backend-Service – `depends_on: condition: service_healthy` zieht dadurch sauber.

**UX & Stabilität**

- `AppErrorBoundary`: Statt weißer Seite bei einem Frontend-Runtime-Fehler erscheint ein verständlicher Hinweis mit „Neu laden“-Button.
- `App.jsx` zeigt klare Meldungen bei Setup-/Auth-Problemen statt eines unendlichen Spinners.
- Leere Record-Listen zeigen Hinweis + Add-Button statt einer leeren Tabelle.
- **DNSSEC-Registrar-Karte** in eigene Komponente ausgelagert (`ZoneDnssecRegistrarCard.jsx`).
- **Settings → API & Sicherheit** kapselt Tokens / TOTP / Webhooks in einem Panel; QR-Code für TOTP läuft über das `qrcode`-Paket (vorher zerschoss `react-qr-code` den Build → weiße Seite).

**Build / Deployment**

- `backend/Dockerfile` prüft nach dem Frontend-Copy explizit, dass `index.html`, `assets/` und mindestens ein gebautes JS-Bundle vorhanden sind – sonst bricht der Build sofort ab.
- `compose.yaml`: Backend-Healthcheck via `/health`, neuer Env-Eintrag `WEBHOOK_ALLOW_PRIVATE_URLS`.

**CI**

- Frontend-ESLint läuft wieder „hart“ (ohne `|| true`).

**Docs**

- `docs/PANEL-API.md` ausgebaut: Authentifizierungswege, Endpoint-Beispiele für Zonen / Records / DNSSEC, Webhook-Signaturprüfung mit Code-Snippet.
- `frontend/README.md` ist kein Vite-Boilerplate mehr.

---

## Mitwirken

PRs sind willkommen, bei größeren Sachen vorher gerne ein Issue. Details in [CONTRIBUTING.md](CONTRIBUTING.md).

### Eine neue Sprache beisteuern

Aktuell drin: Deutsch, Englisch, Serbisch, Kroatisch, Bosnisch, Ungarisch. Weitere kommen gerne als PR – Ablauf:

1. `frontend/src/locales/en.json` als Vorlage kopieren, z. B. zu `it.json`.
2. Werte übersetzen, Keys nicht anfassen.
3. In `frontend/src/i18n.js` einen Eintrag in `LANGUAGES` (Code, Label, Flag-Emoji) und im `resources`-Block ergänzen.
4. PR aufmachen.

Fehlt mal ein Key, ist das nicht schlimm – die UI fällt automatisch auf Englisch zurück.

### Versionspflege (für Maintainer)

Die App-Version steht **ausschließlich** in der Datei `VERSION` im Projektroot. Backend, API und UI lesen sie von dort. Vor einem Release:

```bash
echo "2.3.5" > VERSION
./scripts/update-readme-from-version.sh   # Badge + git-checkout-Beispiele anpassen
```

### Übersetzungen pflegen

`frontend/src/locales/en.json` ist die *Source of Truth* – jede UI-Zeichenkette landet dort zuerst. Die anderen Sprachen (`de`, `sr`, `hr`, `bs`, `hu`) liegen daneben.

Wenn du in einem PR neue Keys einbaust:

```bash
node scripts/sync-locales.mjs
```

Das Script:

- ergänzt fehlende Keys in allen anderen Sprachen mit dem englischen Wert (Fallback),
- entfernt Keys, die nicht mehr in `en.json` existieren,
- listet pro Datei auf, was hinzugefügt/entfernt wurde,
- lässt bestehende Übersetzungen unangetastet.

Danach `git diff frontend/src/locales/` prüfen und committen. Selber muss man so nur `en.json` (und optional `de.json`) pflegen – fehlende Übersetzungen in den anderen Sprachen werden zur Laufzeit über `fallbackLng: 'en'` automatisch englisch angezeigt, bis jemand einen Übersetzungs-PR schickt.

### Lokale Entwicklung ohne Docker

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend (in zweitem Terminal)
cd frontend
npm install
npm run dev
```

---

## Lizenz

MIT – also nutzbar wie es passt, auch in Firmenkontext.

## Credits

Gebaut von [29barra29](https://github.com/29barra29). Teilweise mit KI-Unterstützung entwickelt – wer Bugs findet oder Verbesserungen sieht: Issue oder PR auf.
