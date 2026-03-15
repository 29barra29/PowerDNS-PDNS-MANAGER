# 🚀 DNS Manager - Installation Guide

## Schnellstart (3 Minuten)

### Option 1: Interaktives Setup (Empfohlen)

```bash
# 1. Repository klonen
git clone https://github.com/29barra29/dns-manager.git
cd dns-manager

# 2. Setup-Assistent starten
chmod +x setup.sh
./setup.sh

# 3. Container starten
docker compose up -d

# 4. Browser öffnen
# http://localhost:5380
```

### Option 2: One-Liner Installation

```bash
curl -sSL https://raw.githubusercontent.com/29barra29/dns-manager/main/install.sh | bash
```

---

## 📋 Voraussetzungen

- Docker & Docker Compose
- Linux/MacOS/Windows mit WSL2
- Mindestens 1GB RAM
- Port 5380 frei

## 🔧 Manuelle Installation

### 1. Repository klonen

```bash
git clone https://github.com/29barra29/dns-manager.git
cd dns-manager
```

### 2. Umgebung konfigurieren

```bash
cp .env.example .env
nano .env
```

**Wichtige Variablen:**

```env
# Für automatisches Setup beim ersten Start
ENABLE_REGISTRATION=true

# ODER: Festes Admin-Passwort
ENABLE_REGISTRATION=false
INITIAL_ADMIN_PASSWORD=dein-sicheres-passwort

# Sicherheit (wird automatisch generiert wenn leer)
JWT_SECRET_KEY=
```

### 3. Container starten

```bash
docker compose up -d
```

### 4. Erster Login

- **Mit Registrierung:** Öffne http://localhost:5380 und registriere dich
- **Ohne Registrierung:** Login mit admin / [dein-passwort]

---

## 🔒 Sicherheits-Checkliste

### Minimal (Pflicht)
- [ ] Admin-Passwort geändert
- [ ] `.env` Datei gesichert
- [ ] Firewall konfiguriert

### Empfohlen
- [ ] HTTPS mit Reverse Proxy (siehe unten)
- [ ] Fail2Ban installiert
- [ ] Regelmäßige Backups

---

## 🌐 HTTPS mit Reverse Proxy

### Nginx

```nginx
server {
    listen 443 ssl http2;
    server_name dns.example.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://localhost:5380;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### Traefik

```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.dns-manager.rule=Host(`dns.example.com`)"
  - "traefik.http.routers.dns-manager.tls=true"
  - "traefik.http.routers.dns-manager.tls.certresolver=letsencrypt"
```

### Cloudflare Tunnel

```bash
# Tunnel erstellen
cloudflared tunnel create dns-manager

# Route konfigurieren
cloudflared tunnel route dns --tunnel-id [TUNNEL_ID] dns.example.com

# Tunnel starten
cloudflared tunnel run --url http://localhost:5380 dns-manager
```

---

## 🔌 PowerDNS Integration

### PowerDNS vorbereiten

```ini
# /etc/powerdns/pdns.conf
api=yes
api-key=dein-sicherer-api-key
webserver=yes
webserver-address=0.0.0.0
webserver-port=8081
```

### Im DNS Manager hinzufügen

1. Login als Admin
2. Einstellungen → DNS-Server
3. "Server hinzufügen"
4. Eingeben:
   - Name: `ns1`
   - URL: `http://pdns-server:8081`
   - API-Key: `dein-api-key`
5. "Verbindung testen"
6. Speichern

---

## 🐳 Docker Compose Anpassungen

### Mit eigenem Netzwerk

```yaml
services:
  dns-manager:
    extends:
      file: docker-compose.yml
      service: backend
    networks:
      - my-network

networks:
  my-network:
    external: true
```

### Mit Umgebungsvariablen

```bash
# Start mit custom .env
docker compose --env-file production.env up -d
```

---

## 📦 Updates

### Automatisches Update

```bash
./update.sh
```

### Manuelles Update

```bash
git pull origin main
docker compose build --no-cache
docker compose up -d
```

---

## 🔄 Backup & Restore

### Backup erstellen

```bash
# Datenbank sichern
docker exec dns-manager-db mysqldump -u root -p dns_manager > backup.sql

# Volumes sichern
docker run --rm -v dns-manager_mariadb_data:/data -v $(pwd):/backup alpine tar czf /backup/volumes-backup.tar.gz /data
```

### Restore

```bash
# Datenbank wiederherstellen
docker exec -i dns-manager-db mysql -u root -p dns_manager < backup.sql

# Volumes wiederherstellen
docker run --rm -v dns-manager_mariadb_data:/data -v $(pwd):/backup alpine tar xzf /backup/volumes-backup.tar.gz -C /
```

---

## 🐛 Troubleshooting

### Container startet nicht

```bash
# Logs prüfen
docker compose logs -f

# Ports prüfen
netstat -tulpn | grep 5380
```

### Datenbank-Verbindung fehlgeschlagen

```bash
# Datenbank-Status
docker compose exec mariadb mysqladmin -u root -p status

# Neustart
docker compose restart mariadb
```

### Admin-Passwort vergessen

```bash
# Passwort zurücksetzen
docker compose exec backend python -c "
from app.core.database import async_session
from app.models.models import User
from app.core.auth import hash_password
import asyncio

async def reset():
    async with async_session() as db:
        admin = await db.get(User, 1)
        admin.hashed_password = hash_password('neues-passwort')
        await db.commit()
        print('Password reset!')

asyncio.run(reset())
"
```

---

## 📚 Weitere Ressourcen

- [GitHub Repository](https://github.com/29barra29/dns-manager)
- [Wiki / Dokumentation](https://github.com/29barra29/dns-manager/wiki)
- [Issue Tracker](https://github.com/29barra29/dns-manager/issues)
- [Discord Community](https://discord.gg/dns-manager)

---

## 💡 Tipps für Production

1. **Niemals** Default-Credentials verwenden
2. **Immer** HTTPS aktivieren
3. **Regelmäßig** Updates installieren
4. **Monitoring** einrichten (Uptime Kuma, etc.)
5. **Backups** automatisieren

---

*Happy DNS Managing! 🚀*