# 🌐 DNS Manager

Ein modernes, selbst gehostetes Admin-Panel für **PowerDNS Authoritative Server**.  
Ersetzt PowerDNS-Admin mit einer schlankeren, schnelleren und stabileren Lösung.

![License](https://img.shields.io/badge/license-MIT-blue.svg)
![Docker](https://img.shields.io/badge/docker-ready-brightgreen.svg)
![PowerDNS](https://img.shields.io/badge/PowerDNS-4.x-orange.svg)

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

---

## 🚀 Schnellstart

### Voraussetzungen

- [Docker](https://docs.docker.com/get-docker/) + [Docker Compose](https://docs.docker.com/compose/install/)
- Mindestens ein laufender PowerDNS Authoritative Server mit aktivierter API

### 1. Repository klonen

```bash
git clone https://github.com/DEIN-USERNAME/dns-manager.git
cd dns-manager
```

### 2. Umgebungsvariablen konfigurieren

```bash
cp .env.example .env
nano .env  # Passwörter anpassen!
```

### 3. Starten

```bash
docker compose up -d
```

### 4. Öffnen

Öffne **http://localhost:5380** im Browser.

**Standard-Login:**
- Benutzername: `admin`
- Passwort: `admin`

> ⚠️ **Wichtig:** Ändere das Admin-Passwort nach dem ersten Login!

### 5. PowerDNS-Server einrichten

Du hast **zwei Möglichkeiten**:

**Option A – Über das Admin-Panel (empfohlen):**
1. Einloggen als Admin
2. Gehe zu **Einstellungen** → **DNS-Server**
3. Klicke **Server hinzufügen**
4. Trage Name, URL und API-Key ein
5. Nutze **Verbindung testen** zum prüfen
6. Speichern!

**Option B – Über Umgebungsvariable:**
```env
PDNS_SERVERS=ns1|http://192.168.1.10:8081|dein-api-key
```

Mehrere Server:
```env
PDNS_SERVERS=ns1|http://10.0.0.1:8081|key1,ns2|http://10.0.0.2:8081|key2
```

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

- Ändere das Standard-Admin-Passwort nach dem ersten Login
- Verwende starke Passwörter in der `.env` Datei
- Beschränke den Zugriff auf Port 5380 (z.B. per Firewall oder Reverse Proxy)
- Nutze HTTPS über einen Reverse Proxy (z.B. nginx, Traefik, Caddy)
- Die `.env` Datei wird **niemals** in Git hochgeladen

---

## 📄 Lizenz

MIT License – Frei nutzbar, auch kommerziell.

---

## 🤝 Mitwirken

Pull Requests sind willkommen! Bei größeren Änderungen bitte zuerst ein Issue erstellen.

---

*Made with ❤️ for the self-hosting community*
