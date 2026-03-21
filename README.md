# 🌐 DNS Manager

Ein modernes, selbst gehostetes Admin-Panel für **PowerDNS Authoritative Server**.
Ersetzt PowerDNS-Admin mit einer schlankeren, schnelleren und stabileren Lösung.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)
![PowerDNS](https://img.shields.io/badge/PowerDNS-4.x-orange.svg)
![Version](https://img.shields.io/badge/version-v2.3.2-blue.svg)

---

## ✨ Features

Fokus: **Was du mit PowerDNS und dem Panel machst** 

- **Mehrere PowerDNS-Server (4.x)** – Beliebig viele Server im Panel anlegen; Zonen und Records zentral pflegen. **Verbindungstest** pro Ziel; Server-Einträge direkt in den **Einstellungen** bearbeiten.
- **DNS-Records & Zonen** – Gängige Typen per Formular (A, AAAA, CNAME, MX, TXT, SRV, …); dazu ALIAS, DNAME, SVCB/HTTPS, **DNSSEC**-Records (DS, DNSKEY, RRSIG, …) und weitere PowerDNS-Typen als **RDATA-Text**. **DNSSEC** pro Zone ein-/ausschalten. **Suche** über alle Server (auch Teilnamen).
- **Zonen-Vorlagen** – Eigene Vorlagen mit NS, SOA und Standard-Records; beim Anlegen einer Zone auswählen (gleiche Record-Auswahl wie oben).
- **Pro-Server „Speichern“** – Pro Server-Eintrag steuerbar, ob **diese** Instanz Zonen schreibt (praktisch bei **einer** PowerDNS-DB und mehreren Server-Zeilen).
- **SMTP** – **E-Mail-Versand** konfigurierbar (Benachrichtigungen, Passwort-Reset): Server, TLS, Test-Mail und Verbindungstest.
- **Benutzer** – Rollen **Admin** und **Benutzer**; mehrere Logins.
- **Audit-Log** – Änderungen nachvollziehbar protokolliert.


---

## 📋 Was ist neu (Changelog)

Hier die **letzten beiden Releases** in Kurzform (ältere Versionen: [GitHub Releases](https://github.com/29barra29/dns-manager/releases) oder Commit-Historie).

### v2.3.2

- **Updates & Sicherheit:** Abgleich mit **GitHub** (`releases/latest`, sonst neuester Tag) etwa **alle 30 Minuten**; bei neuerer Version als lokal installiert: **roter Hinweis** an der Sidebar „Einstellungen“ und am Reiter **„Updates“** (nur Admin). Öffnet man den Reiter „Updates“, gilt die Version als **gesehen** (gespeichert im Browser). **Session-Check** ebenfalls im 30-Minuten-Takt (`getMe`); ungültige Session → Login. *(Öffentliche GitHub-API: bei privatem Repo kann die Abfrage ausbleiben.)*
- **Docker:** Volume **`backend_uploads`** für `static_new/uploads` – **Custom-Logo bleibt** nach Image-Update erhalten.
- **DNS-UI:** Zusätzliche Record-Typen (u. a. ALIAS, DNAME, SVCB/HTTPS, DNSSEC-RRs) als **RDATA** mit **Hilfetexten & Beispielen**; gleiche Typen in **Vorlagen**; Layout-Fixes (Zone: Name mit **FQDN-Vorschau**, SRV/Vorlagen-Zeilen).
- **Sonstiges:** `SECURITY.md` um **v2.3.x** ergänzt; Frontend-ESLint bereinigt; README-Changelog auf **2 Releases** verkürzt.

### v2.3.1

- Branding (Logo, Tagline, Creator), **i18n** DE/EN, Auth-Seiten Register/Forgot/Reset, Custom-Logo auf Login & Sidebar, zentrale `VERSION`, Local Storage nur für Sprache – Details in Features und in älteren Release-Notes auf GitHub.

---

## 🚀 Schnellstart

### Voraussetzungen

- [Docker](https://docs.docker.com/get-docker/) + [Docker Compose](https://docs.docker.com/compose/install/)
- Linux, MacOS oder Windows mit WSL2
- Port 5380 frei

### Installation

#### Option 1: One-Click Installation (Empfohlen) 🎯

```bash
curl -sSLO https://raw.githubusercontent.com/29barra29/dns-manager/main/install.sh && bash install.sh
```

#### Option 2: Interaktives Setup 🔧

```bash
git clone https://github.com/29barra29/dns-manager.git
cd dns-manager
./setup.sh   # Interaktiver Setup-Assistent
docker compose up -d
```

#### Option 3: Manuelle Installation 📝

```bash
git clone https://github.com/29barra29/dns-manager.git
cd dns-manager
cp .env.example .env
nano .env  # Konfiguration anpassen
docker compose up -d
```

### Erster Zugriff

1. Öffne **http://localhost:5380** im Browser
2. **Bei aktivierter Registrierung:** Der Setup-Wizard führt dich durch die Einrichtung
3. **Ohne Registrierung:** Login mit deinen konfigurierten Credentials

### PowerDNS-Server einrichten

**Über das Admin-Panel (empfohlen):**
1. Einloggen als Admin
2. Gehe zu **Einstellungen** → **DNS-Server**
3. Klicke **Server hinzufügen**
4. Trage Name, URL und API-Key ein
5. Nutze **Verbindung testen** zum prüfen
6. Speichern!

---

## 🛠️ Technologie-Stack

| Komponente | Technologie |
|---|---|
| **Frontend** | React 19 + Vite 7 + Tailwind CSS 4 |
| **Backend** | Python 3.12 + FastAPI + SQLAlchemy (async) + Pydantic |
| **Datenbank** | MariaDB 11 |
| **Auth** | JWT (Bearer Token) |
| **DNS-Engine** | PowerDNS Authoritative |
| **Container** | Docker + Docker Compose |

---

## 📁 Projektstruktur

```
dns-manager/
├── VERSION               # Zentrale Versionsnummer (eine Stelle für die ganze App)
├── compose.yaml          # Docker Compose Konfiguration
├── .env.example          # Vorlage für Umgebungsvariablen
├── .env                  # Deine lokale Konfiguration (nicht in Git!)
├── scripts/
│   └── update-readme-from-version.sh   # README-Badge/Beispiele aus VERSION aktualisieren
│
├── backend/              # FastAPI Backend
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py           # App-Einstiegspunkt, Static Files (uploads)
│       ├── core/
│       │   ├── config.py     # Konfiguration
│       │   ├── database.py   # DB-Verbindung
│       │   └── auth.py       # JWT-Authentifizierung
│       ├── models/
│       │   └── models.py     # Datenbank-Models
│       ├── routers/
│       │   ├── auth.py       # Login, Registrierung, Passwort-Reset
│       │   ├── servers.py    # Server-Info
│       │   ├── zones.py      # Zonen-Verwaltung
│       │   ├── records.py    # Record-Verwaltung
│       │   ├── dnssec.py     # DNSSEC
│       │   ├── search.py     # Suche + Audit-Log
│       │   ├── setup.py      # Setup-Wizard API
│       │   ├── settings.py   # Server, SMTP, App-Info/Branding, Logo-Upload
│       │   └── templates.py  # Zonen-Vorlagen (CRUD)
│       ├── schemas/
│       │   └── dns.py        # Pydantic-Schemas + Validierung
│       ├── services/
│       │   ├── pdns_client.py # PowerDNS API Client
│       │   └── email_service.py # SMTP E-Mail-Versand
│       └── static_new/
│           └── uploads/      # Hochgeladene Logos (custom-logo.*), persistent
│
└── frontend/             # React Frontend
    ├── package.json
    ├── vite.config.js
    ├── tailwind.config.js
    └── src/
        ├── main.jsx           # Einstieg, i18n-Provider
        ├── App.jsx
        ├── api.js             # API-Client (inkl. getAppInfo, uploadAppLogo)
        ├── i18n.js            # Mehrsprachigkeit (DE/EN), Local Storage für Sprache
        ├── locales/
        │   ├── de.json        # Deutsche Übersetzungen (Auth, Branding, UI)
        │   └── en.json        # Englische Übersetzungen
        ├── components/
        │   └── Layout.jsx     # Sidebar, Navigation, Custom-Logo (Branding)
        └── pages/
            ├── LoginPage.jsx        # Login + Sprachumschalter
            ├── RegisterPage.jsx     # Registrierung
            ├── ForgotPasswordPage.jsx   # Passwort vergessen
            ├── ResetPasswordPage.jsx    # Passwort zurücksetzen (Token)
            ├── SetupWizard.jsx       # Ersteinrichtungs-Assistent
            ├── DashboardPage.jsx
            ├── ZonesPage.jsx         # + Vorlagen-Auswahl
            ├── ZoneDetailPage.jsx    # Records bearbeiten
            ├── SearchPage.jsx
            ├── AuditLogPage.jsx
            ├── UsersPage.jsx
            └── SettingsPage.jsx     # Server, Vorlagen, SMTP, Branding (Logo, Tagline, Creator)
```

**Hinweis:** u. a. `i18n.js`, `locales/`, Branding, `static_new/uploads` (Logo-Volume in Docker), Update-Hinweis & Session-Check (siehe Changelog v2.3.2). Sprache nur im Local Storage (keine Tokens).

---

## 🔄 Updates

Nach einem Update: Änderungen und Neuerungen stehen oben unter **„Was ist neu (Changelog)“**.

### Automatisches Update

```bash
cd dns-manager
./update.sh
```

Das Update-Script macht folgendes:
1. Holt die neueste Version von GitHub
2. Baut die Container neu
3. Startet die Anwendung neu
4. Behält deine Datenbank und Einstellungen
5. **Logo und Uploads** bleiben erhalten (persistentes Volume `backend_uploads` für `static_new/uploads`)

### Manuelles Update

```bash
cd dns-manager
git pull origin main
docker compose build --no-cache backend
docker compose up -d
```

### Update von einer bestimmten Version

```bash
cd dns-manager
git fetch --tags
git checkout v2.3.2  # Oder gewünschte Version
docker compose build --no-cache
docker compose up -d
```

### Backup vor Update (Empfohlen)

```bash
# Datenbank sichern
docker exec dns-manager-db mysqldump -u root -p dns_manager > backup_$(date +%Y%m%d).sql

# Update durchführen
./update.sh
```

---

## 🔧 PowerDNS vorbereiten

Stelle sicher, dass dein PowerDNS-Server die API aktiviert hat.

In der `/etc/powerdns/pdns.conf`:
```ini
api=yes
api-key=dein-sicherer-api-key
webserver=yes
webserver-address=0.0.0.0
webserver-port=8081
webserver-allow-from=0.0.0.0/0
```

Danach PowerDNS neustarten:
```bash
systemctl restart pdns
```

---

## 👥 Rollen

| Rolle | Rechte |
|---|---|
| **Admin** | Alle Zonen sehen, Zonen erstellen/löschen, Benutzer verwalten, Server konfigurieren, Audit-Log einsehen |
| **Benutzer** | Nur zugewiesene Zonen sehen, Records bearbeiten, Suche nutzen |

---

## 🔒 Sicherheitshinweise

### Basis-Absicherung (Pflicht)
- ✅ JWT-Secret wird automatisch generiert
- ✅ Sichere Passwörter werden beim Setup erstellt
- ⚠️ Ändere das Admin-Passwort nach dem ersten Login
- ⚠️ Aktiviere HTTPS über einen Reverse Proxy

### Empfohlene Maßnahmen
- Nutze Cloudflare Tunnel oder anderen Reverse Proxy
- Aktiviere Fail2Ban für Brute-Force Schutz
- Beschränke Ports mit Firewall-Regeln
- Regelmäßige Backups einrichten
- Updates zeitnah installieren

### Für öffentlichen Zugriff
```nginx
# Beispiel nginx Reverse Proxy mit SSL
server {
    listen 443 ssl http2;
    server_name dns.example.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:5380;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## 📄 Lizenz

MIT License – Frei nutzbar, auch kommerziell.

---

## 📚 Dokumentation

- [Installations-Anleitung](INSTALL.md)
- [Wiki](https://github.com/29barra29/dns-manager/wiki)
- [API-Dokumentation](http://localhost:5380/docs)
- [Issue Tracker](https://github.com/29barra29/dns-manager/issues)

## 🤝 Mitwirken

Pull Requests sind willkommen! Bei größeren Änderungen bitte zuerst ein Issue erstellen.

### Version (eine Stelle)

Die App-Version steht **nur in der Datei `VERSION`** im Projektroot. Backend, API und Web-Oberfläche lesen sie von dort. Vor einem Release: Inhalt von `VERSION` anpassen (z. B. `2.3.2`), dann `./scripts/update-readme-from-version.sh` ausführen, damit Badge und Beispiele in der README angepasst werden.

### Entwicklung

```bash
# Backend entwickeln
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload

# Frontend entwickeln
cd frontend
npm install
npm run dev
```

## 🐛 Fehlerbehebung

### Container startet nicht
```bash
docker compose logs -f
docker compose down
docker compose up -d
```

### Admin-Passwort vergessen
```bash
# Neues Passwort setzen
docker compose exec backend python -c "
from app.core.database import async_session
from app.models.models import User
from app.core.auth import hash_password
import asyncio

async def reset():
    async with async_session() as db:
        # Admin user holen (ID 1)
        admin = await db.get(User, 1)
        admin.hashed_password = hash_password('neues-passwort')
        await db.commit()
        print('Passwort zurückgesetzt!')

asyncio.run(reset())
"
```

---

## 🌟 Credits

Entwickelt von [29barra29](https://github.com/29barra29) – für die self-hosting Community.  
Teilweise mit KI-Unterstützung entwickelt.  
Pull Requests und Contributors sind willkommen!

---

*Made with ❤️ for the self-hosting community*
