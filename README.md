# 🌐 DNS Manager

Ein modernes, selbst gehostetes Admin-Panel für **PowerDNS Authoritative Server**.
Ersetzt PowerDNS-Admin mit einer schlankeren, schnelleren und stabileren Lösung.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)
![PowerDNS](https://img.shields.io/badge/PowerDNS-4.x-orange.svg)
![Version](https://img.shields.io/badge/version-v2.3.1-blue.svg)

---

## ✨ Features

- 🖥️ **Modernes Dashboard** – Server-Status, Zonen-Übersicht auf einen Blick
- 🌍 **Multi-Server Support** – Mehrere PowerDNS-Server von einem Panel verwalten
- 📝 **DNS-Record Management** – Alle Record-Typen (A, AAAA, CNAME, MX, TXT, SRV, ...) erstellen, bearbeiten und löschen
- 📋 **Zonen-Vorlagen (Templates)** – Eigene Vorlagen mit Standard-Nameservern, SOA-Einstellungen und DNS-Records definieren. Beim Erstellen neuer Zonen einfach eine Vorlage auswählen – alles wird automatisch übernommen
- 📧 **E-Mail (SMTP)** – SMTP-Server konfigurieren für E-Mail-Versand (Benachrichtigungen, Passwort-Reset). Mit Verbindungstest und Test-E-Mail
- ✅ **Eingabe-Validierung** – Domain-Namen, IP-Adressen und andere Felder werden automatisch auf Gültigkeit geprüft
- 🔒 **DNSSEC** – Aktivieren/Deaktivieren direkt im Panel
- 🔍 **Suche** – Server-übergreifende Suche nach Zonen und Records
- 👥 **Benutzerverwaltung** – Rollenbasiert (Admin / Benutzer)
- 🔐 **Zonen-Zuordnung** – Admins können festlegen, welche Zonen ein User sehen darf
- ⚙️ **Server-Verwaltung im UI** – PowerDNS-Server direkt im Admin-Panel hinzufügen/bearbeiten
- 🧪 **Verbindungstest** – Live-Test ob ein PowerDNS-Server erreichbar ist
- 📋 **Audit-Log** – Alle Änderungen nachvollziehbar protokolliert
- 🎨 **Dark Mode** – Modernes, dunkles Design
- 🚀 **Setup-Wizard** – Einfache Ersteinrichtung mit interaktivem Assistenten
- 🔐 **Sichere Defaults** – Automatische Generierung von Secrets und Passwörtern
- 🔍 **Teil-Suche** – Suche mit Teilnamen (z. B. „example“ findet „example.de“), Live-Suche ab 3 Zeichen
- ✏️ **Pro-Server „Speichern“** – Pro DNS-Server einstellbar, ob Zonen hier geschrieben werden (ideal bei 1 DB mit mehreren Server-Einträgen)
- 📢 **Fehlermeldungen im Modal** – Beim Erstellen einer Zone erscheinen Fehler direkt im Popup
- 📊 **Ergebnis pro Server** – Nach Zonen-Erstellung: Anzeige pro Server (erstellt / vorhanden / Fehler)
- 📄 **Zentrale Version** – Eine `VERSION`-Datei, alle Anzeigen (API, UI, Setup) lesen daraus
- 🎨 **Branding (Login/Setup)** – Eigenes Logo, Footer- und Creator-Text in den Einstellungen; Anzeige auf Login, Registrierung, Passwort vergessen/Reset und im Setup-Wizard
- 🌐 **Mehrsprachigkeit (i18n)** – Login, Registrierung, Passwort-Reset, Setup-Wizard und Einstellungen auf Deutsch/Englisch mit Sprachumschalter

---

## 📋 Was ist neu (Changelog)

### v2.3.1

- **Branding:** Eigenes Logo, Tagline (Footer-Text) und Creator-Text in den Admin-Einstellungen („Branding (Login/Setup)“). Logo-Upload (PNG/JPG/WEBP/SVG, max. 2 MB), optional „Logo entfernen“. API: `GET/PUT /settings/app-info`, `POST /settings/app-logo`; Logo wird unter `static_new/uploads/custom-logo.*` gespeichert.
- **Logo überall:** Das eingestellte Logo erscheint auf Login, Registrierung, Passwort vergessen, Passwort-Reset und im Setup-Wizard – sowie in der **Sidebar** (Dashboard und alle eingeloggten Seiten).
- **Mehrsprachigkeit (i18n):** Login-, Registrierungs-, Forgot-/Reset- und Setup-Seiten sowie Einstellungen nutzen vollständig die Übersetzungen (DE/EN). Sprachumschalter auf der Login-Seite; gewählte Sprache wird im Benutzerprofil gespeichert und beim nächsten Login übernommen.
- **Locales:** Erweiterte `de.json` und `en.json` um alle Keys für Branding, Login, Register, Forgot, Reset und Setup.
- **Projektstruktur:** README um `i18n.js`, `locales/`, Auth-Seiten (Register, Forgot, Reset), Branding und `static_new/uploads` ergänzt.
- **Local Storage:** Nur die gewählte Sprache (z. B. `locale=de`) wird clientseitig gespeichert – keine Tokens oder sensiblen Daten; JWT bleibt in Memory/HttpOnly-Cookie-Konzept.

### v2.2.2

- **Suche:** Teil-Suche (PowerDNS-Wildcard), z. B. „example“ findet „example.de“; Live-Suche ab 3 Zeichen
- **DNS-Server Einstellungen:** Option „Auf diesem Server speichern“ pro Server (Haken = Schreibzugriff). Bei gemeinsamer DB nur bei einem Server aktivieren, dann keine doppelten Schreibversuche mehr
- **Neue Zone erstellen:** Fehlermeldungen erscheinen im Modal; Ergebnis pro Server (z. B. server1/server2) wird angezeigt
- **Zone erstellen (Backend):** Zone wird auf jedem Server mit Schreibrecht angelegt; 409 Conflict = „vorhanden“ (z. B. gleiche DB)
- **Version:** Eine zentrale `VERSION`-Datei; Backend, API, Frontend (Über-Tab), Setup und README-Script nutzen sie. Release: nur `VERSION` anpassen, dann `./scripts/update-readme-from-version.sh`
- **SECURITY.md:** Zweisprachig (EN/DE), Hinweis auf „Report a vulnerability“ im GitHub Security-Tab
- **Release:** Script `scripts/update-readme-from-version.sh` aktualisiert README-Badge und Versionsbeispiele aus der `VERSION`-Datei

### v2.2.1

- Zonen-Vorlagen, Setup-Wizard, SMTP, Multi-Server, Audit-Log, Dark Mode (siehe Features)

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

**Hinweis (v2.3.1):** Neu bzw. erweitert: `frontend/src/i18n.js`, `locales/` (DE/EN), Auth-Seiten Register/Forgot/Reset, Branding in Settings und Backend (`settings.py`, `static_new/uploads`). Sprache wird im Local Storage gespeichert (Sicherheit: nur UI-Präferenz, keine sensiblen Daten).

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
git checkout v2.3.1  # Oder gewünschte Version
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

Die App-Version steht **nur in der Datei `VERSION`** im Projektroot. Backend, API und Web-Oberfläche lesen sie von dort. Vor einem Release: Inhalt von `VERSION` anpassen (z. B. `2.3.1`), dann `./scripts/update-readme-from-version.sh` ausführen, damit Badge und Beispiele in der README angepasst werden.

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
