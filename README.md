# 🌐 DNS Manager

Ein modernes, selbst gehostetes Admin-Panel für **PowerDNS Authoritative Server**.
Ersetzt PowerDNS-Admin mit einer schlankeren, schnelleren und stabileren Lösung.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)
![PowerDNS](https://img.shields.io/badge/PowerDNS-4.x-orange.svg)
![Version](https://img.shields.io/badge/version-v2.1.0-blue.svg)

---

## ✨ Features

- 🖥️ **Modernes Dashboard** – Server-Status, Zonen-Übersicht auf einen Blick
- 🌍 **Multi-Server Support** – Mehrere PowerDNS-Server von einem Panel verwalten
- 📝 **DNS-Record Management** – Alle Record-Typen (A, AAAA, CNAME, MX, TXT, SRV, ...)
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

---

## 🚀 Schnellstart

### Voraussetzungen

- [Docker](https://docs.docker.com/get-docker/) + [Docker Compose](https://docs.docker.com/compose/install/)
- Linux, MacOS oder Windows mit WSL2
- Port 5380 frei

### Installation

#### Option 1: One-Click Installation (Empfohlen) 🎯

```bash
curl -sSL https://raw.githubusercontent.com/29barra29/dns-manager/main/install.sh | bash
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
| **Frontend** | React 19 + Tailwind CSS |
| **Backend** | Python FastAPI |
| **Datenbank** | MariaDB 11 |
| **Auth** | JWT (Bearer Token) |
| **DNS-Engine** | PowerDNS Authoritative |
| **Container** | Docker + Docker Compose |

---

## 📁 Projektstruktur

```
dns-manager/
├── compose.yaml          # Docker Compose Konfiguration
├── .env.example          # Vorlage für Umgebungsvariablen
├── .env                  # Deine lokale Konfiguration (nicht in Git!)
│
├── backend/              # FastAPI Backend
│   ├── Dockerfile
│   ├── requirements.txt
│   └── app/
│       ├── main.py           # App-Einstiegspunkt
│       ├── core/
│       │   ├── config.py     # Konfiguration
│       │   ├── database.py   # DB-Verbindung
│       │   └── auth.py       # JWT-Authentifizierung
│       ├── models/
│       │   └── models.py     # Datenbank-Models
│       ├── routers/
│       │   ├── auth.py       # Login / User-Verwaltung
│       │   ├── servers.py    # Server-Info
│       │   ├── zones.py      # Zonen-Verwaltung
│       │   ├── records.py    # Record-Verwaltung
│       │   ├── dnssec.py     # DNSSEC
│       │   ├── search.py     # Suche + Audit-Log
│       │   └── settings.py   # Server-Konfiguration
│       ├── schemas/
│       │   └── dns.py        # Pydantic-Schemas
│       └── services/
│           └── pdns_client.py # PowerDNS API Client
│
└── frontend/             # React Frontend
    ├── package.json
    ├── vite.config.js
    ├── tailwind.config.js
    └── src/
        ├── App.jsx
        ├── api.js            # API-Client
        ├── components/
        │   └── Layout.jsx    # Sidebar + Navigation
        └── pages/
            ├── LoginPage.jsx
            ├── DashboardPage.jsx
            ├── ZonesPage.jsx
            ├── ZoneDetailPage.jsx
            ├── SearchPage.jsx
            ├── AuditLogPage.jsx
            ├── UsersPage.jsx
            └── SettingsPage.jsx
```

---

## 🔄 Updates

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
git checkout v2.1.0  # Oder gewünschte Version
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

Entwickelt von der Community für die Community.
Besonderer Dank an alle Contributors!

---

*Made with ❤️ for the self-hosting community*
