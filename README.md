# DNS Manager

Ein Web-Panel für **PowerDNS Authoritative Server** zum Self-Hosten. Entstanden aus dem Wunsch, PowerDNS-Admin durch etwas Aufgeräumteres mit aktuellem Stack zu ersetzen.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)
![PowerDNS](https://img.shields.io/badge/PowerDNS-4.x-orange.svg)
![Version](https://img.shields.io/badge/version-v2.3.4-blue.svg)

---

## Demo

Ein Video-Walkthrough (Installation, ersten Server anbinden, Zone + DNSSEC anlegen) folgt auf YouTube. Sobald es online ist, steht der Link hier. Wer zwischendurch fragen hat: einfach ein Issue aufmachen.

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
- **Branding** – eigener App-Name, Tagline und Logo (z. B. Firmenlogo); Logo bleibt bei Updates über das Volume `backend_uploads` erhalten.
- **DE/EN-Oberfläche** – Sprachumschalter unten links, Default in der `.env` einstellbar.

## Was es bewusst nicht macht

- Kein DHCP, kein Recursor, kein Slave-DNS-Setup-Tool – das hier verwaltet **Authoritative Zones**.
- Keine Multi-Tenant-Mandantentrennung (keine getrennten „Kunden"). Wer das braucht, ist mit PowerDNS-Admin oder einer kommerziellen Lösung besser bedient.
- Kein eingebauter Reverse-Proxy / kein TLS – das macht Caddy / nginx / Traefik / Cloudflare Tunnel davor.

---

## Installation

### Voraussetzungen

Docker und Docker Compose. Port `5380` muss frei sein – wer ihn ändern will, passt das `ports:`-Mapping in der `compose.yaml` an.

### Variante A – One-Liner

Holt das Repo, fragt ein paar Sachen ab, erzeugt eine fertige `.env` und startet die Container:

```bash
curl -sSLO https://raw.githubusercontent.com/29barra29/dns-manager/main/install.sh && bash install.sh
```

### Variante B – Klonen und Setup-Wizard

Selber Effekt, nur ohne den Download-Wrapper:

```bash
git clone https://github.com/29barra29/dns-manager.git
cd dns-manager
./setup.sh
docker compose up -d
```

### Variante C – Manuell

Wer keinen Wizard mag und alle Variablen selbst setzen will:

```bash
git clone https://github.com/29barra29/dns-manager.git
cd dns-manager
cp .env.example .env
# WICHTIG: DB_ROOT_PASSWORD, DB_PASSWORD und JWT_SECRET_KEY ausfüllen,
# sonst startet compose mit einer Fehlermeldung. Beispiel:
#   sed -i "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=$(openssl rand -hex 64)|" .env
nano .env
chmod 600 .env
docker compose up -d
```

### Erster Login

1. Browser auf `http://localhost:5380`.
2. Ist `ENABLE_REGISTRATION=true` (Default beim Setup-Wizard), wird der Setup-Wizard im Browser angezeigt – der erste angelegte User ist automatisch Admin. Anschließend stellt sich die Registrierung selbst ab.
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

Vor dem Update lohnt sich ein DB-Dump:

```bash
docker exec dns-manager-db mysqldump -u root -p dns_manager > backup_$(date +%Y%m%d).sql
```

Manuell auf eine bestimmte Version wechseln:

```bash
cd dns-manager
git fetch origin --tags --force --prune --prune-tags
git checkout v2.3.4              # oder: git checkout main && git pull
docker compose build --no-cache backend
docker compose up -d
```

---

## Sicherheit

Was beim Setup automatisch passiert:

- `JWT_SECRET_KEY` wird mit `openssl rand -hex 64` erzeugt – ohne den Schlüssel sind nach jedem Container-Restart alle Logins ungültig.
- DB-Passwörter werden zufällig generiert.
- `.env` bekommt `chmod 600`.
- Die OpenAPI-/Swagger-Doku unter `/docs` ist standardmäßig aus (`DOCS_ENABLED=false`). Wer sie braucht, setzt die Variable in der `.env` auf `true`.
- Passwort-Hashing über `pwdlib` mit bcrypt. Alte Hashes aus passlib-Zeiten bleiben gültig.

Was du selbst noch machen solltest:

- Admin-Passwort nach dem ersten Login ändern.
- Reverse-Proxy mit TLS davorschalten (Caddy ist mit Abstand am schnellsten aufgesetzt). Sobald HTTPS läuft, in der `.env` `AUTH_COOKIE_SECURE=true` setzen und einmal `docker compose up -d` – sonst bleibt der Login-Cookie unter HTTPS unzuverlässig.
- `ENABLE_REGISTRATION=false` setzen, sobald alle Accounts angelegt sind.
- Port `5380` über die Firewall nur lokal oder hinter dem Reverse-Proxy erreichbar lassen.
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

### Backend logt „JWT_SECRET_KEY ist leer"

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

Hier die letzten beiden Releases. Komplette Historie: [GitHub Releases](https://github.com/29barra29/dns-manager/releases).

### v2.3.4

Kleines Patch-Release aus einem User-Issue auf GitHub. Drei Kleinigkeiten, kein Breaking Change – `./update.sh` reicht.

- **Logo-Darstellung:** nicht-quadratische Logos (SVG/PNG) wurden durch `object-cover` ins Quadrat geschnitten. Jetzt mit `object-contain`, das Seitenverhältnis bleibt erhalten – überall, wo das Logo auftaucht (Sidebar, Login, Register, Forgot/Reset-Password, Setup-Wizard, Settings-Vorschau).
- **E-Mail-Sprache:** Passwort-Reset- und Test-Mail waren hart auf Deutsch. Jetzt folgen beide der Sprache des jeweiligen Nutzers (`preferred_language` im Profil), dann `DEFAULT_LANGUAGE` aus der `.env`, dann Englisch. Neues Modul `backend/app/services/email_templates.py` – weitere Sprachen landen dort zentral.
- **Installer-Hinweis:** die Meldung „Do not run as root" / „Bitte nicht als root ausführen" erklärt jetzt selbst, was man stattdessen tun soll (normaler User mit sudo-Rechten; Docker-Befehle nutzen `sudo` automatisch, wenn nötig).

### v2.3.3

Hauptsächlich Sicherheits- und Aufräumarbeit, keine sichtbaren neuen Features. Bestehende Installationen können einfach `./update.sh` laufen lassen, ohne dass etwas in der DB anders wird.

- **Backend-Updates:** `python-jose` 3.3 → 3.5 (CVE-2024-33663 / Algorithm-Confusion); FastAPI 0.115 → 0.136, Uvicorn 0.34 → 0.44, SQLAlchemy 2.0.49, Pydantic 2.13, Alembic 1.18. Alles Minor-Sprünge, keine Migrationen nötig.
- **Frontend-Updates:** Vite 7 → 8 (mehrere Path-Traversal-CVEs gefixt, neuer Rolldown-Bundler), React 19.2.5, lucide-react 1.x, i18next 26, ESLint 10. `npm audit` ist bei 0.
- **Passwort-Hashing:** weg von `passlib` (seit 2020 nicht mehr maintained), hin zu `pwdlib` + bcrypt 4.3. Bestehende Hashes bleiben gültig, kein Re-Login nötig.
- **`compose.yaml` fail-safe:** die alten Defaults `${DB_ROOT_PASSWORD:-changeme-root}` / `${DB_PASSWORD:-changeme-password}` sind raus. Wenn die `.env` fehlt, bricht Compose mit klarer Fehlermeldung ab – statt eine Datenbank mit bekannten Default-Passwörtern zu initialisieren.
- **Setup-Wizard:** fragt jetzt nach HTTPS-Reverse-Proxy und setzt `AUTH_COOKIE_SECURE` entsprechend, schreibt die neuen Variablen (`AUTH_COOKIE_*`, `ALLOWED_ORIGINS`, `DOCS_ENABLED`) automatisch mit, setzt `chmod 600` auf die `.env`. Das alte verwirrende `APP_VERSION` in der `.env` ist raus – die Version kommt nur noch aus der `VERSION`-Datei.
- **`install.sh`-Fallback** (wenn `setup.sh` fehlt) generiert jetzt korrekt Passwörter und JWT-Secret. Vorher konnten Platzhalter im Worst Case stehen bleiben.
- **`update.sh`** zeigt vorher → nachher die Version und warnt, falls `JWT_SECRET_KEY` in der `.env` fehlt.

---

## Mitwirken

PRs sind willkommen, bei größeren Sachen vorher gerne ein Issue. Details in [CONTRIBUTING.md](CONTRIBUTING.md).

### Versionspflege (für Maintainer)

Die App-Version steht **ausschließlich** in der Datei `VERSION` im Projektroot. Backend, API und UI lesen sie von dort. Vor einem Release:

```bash
echo "2.3.4" > VERSION
./scripts/update-readme-from-version.sh   # Badge + git-checkout-Beispiele anpassen
```

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
