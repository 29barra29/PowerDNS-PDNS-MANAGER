# DNS Manager – API für Skripte (Panel-Token & Session)

Die Weboberfläche spricht standardmäßig dieselbe **REST-API** unter dem Pfad-Präfix `/api/v1` an. Wenn du **keinen Browser** einsetzen willst (Terraform, Skripte, Monitoring, CI), legst du dir einen **Panel-API-Token** an und schickst ihn als `Authorization: Bearer …`.

---

## Authentifizierung

Drei Wege, je nach Anwendungsfall:

### 1. Browser-Session (Cookie)

Nach dem Login setzt der Server ein HttpOnly-Cookie (`dnsmgr_token`). Das brauchen **nur** SPA/Browser; Skripte können das nicht sinnvoll nutzen.

### 2. Panel-API-Token (empfohlen für Automation)

Anlegen unter **Einstellungen → API & Sicherheit → Panel-Token**.

- Header: `Authorization: Bearer <vollständiger-Token-String>`
- Token beginnt mit `dnsmgr_usr_` und wird **nur einmal** bei der Erstellung angezeigt (danach nur noch ein Kurz-Präfix in der Liste).
- **Gleiche Rechte** wie der erstellende Benutzer: alle Zonen/Records, die seine Rolle + Zonen-ACL erlauben (analog zum eingeloggten Panel).
- Letzte Verwendung (Zeit + IP) ist im Panel sichtbar; Revoke-Button entzieht den Token sofort.

### 3. ACME-Token (nur Zertifikate / DNS-01)

Anlegen unter **Einstellungen → ACME / Auto-TLS**.

- Prefix `dnsmgr_acme_`.
- Darf **ausschließlich** `_acme-challenge.*`-TXT-Records in den freigegebenen Zonen anlegen/entfernen. Alles andere ist HTTP 403.
- Endpoints: `POST /api/v1/acme/present`, `POST /api/v1/acme/cleanup`.
- Fertiger certbot-Hook im Repo: `scripts/certbot-dns-dnsmanager.sh`.

---

## Basiskonfiguration

- **Base-URL:** `https://<dein-host>` (z. B. `https://dns.example.com`; bei Selfhost ohne Reverse-Proxy oft `http://localhost:5380`).
- **API-Prefix:** `/api/v1`
- **Health (ohne Präfix):** `GET /health` → `{"status":"healthy","database":"connected"}` oder HTTP 503.
- **Wer bin ich:** `GET /api/v1/auth/me` – liefert den aktuellen Benutzer, wenn Token/Cookie gültig.
- **Interaktive API-Doku:** `https://<host>/docs` (nur wenn `DOCS_ENABLED=true` gesetzt ist; Default **aus** aus Sicherheitsgründen).

### Fehlerformat

Alle Fehler kommen als JSON mit `detail` (FastAPI-Default):

```json
{ "detail": "Token has been revoked" }
```

Wichtige Status-Codes:

| Code | Bedeutung                                                                |
|------|---------------------------------------------------------------------------|
| 401  | Kein Token / Token ungültig oder abgelaufen.                              |
| 403  | Token gültig, aber keine Berechtigung (Zonen-ACL, Read-Only, Server read-only). |
| 404  | Zone/Record/Server existiert nicht.                                       |
| 409  | Konflikt (z. B. Zone existiert bereits).                                  |
| 422  | Validierungsfehler (Pydantic-Body falsch).                                |
| 429  | Rate-Limit für Logins (gilt nicht für Token-Auth).                        |
| 5xx  | Backend-Fehler. Body ist sanitised – Details stehen im Server-Log.        |

---

## Beispiele (curl)

```bash
export DNSMGR='https://dns.example.com'   # anpassen
export TOKEN='dnsmgr_usr_…'                 # vollständiger Token aus dem Panel
H_AUTH="Authorization: Bearer $TOKEN"

# Wer bin ich?
curl -sS -H "$H_AUTH" "$DNSMGR/api/v1/auth/me"

# Server auflisten (inkl. allow_writes-Flag)
curl -sS -H "$H_AUTH" "$DNSMGR/api/v1/servers"

# Zonen auf einem Server auflisten
curl -sS -H "$H_AUTH" "$DNSMGR/api/v1/zones/<server-name>"

# Records einer Zone (zone_id ist der Domainname mit Trailing-Dot, z. B. example.com.)
curl -sS -H "$H_AUTH" "$DNSMGR/api/v1/records/<server-name>/example.com."

# Record anlegen / aktualisieren
curl -sS -H "$H_AUTH" -H "Content-Type: application/json" \
  -X POST "$DNSMGR/api/v1/records/<server-name>/example.com." \
  -d '{
    "name": "www.example.com.",
    "type": "A",
    "content": "203.0.113.10",
    "ttl": 300
  }'

# Record löschen
curl -sS -H "$H_AUTH" -H "Content-Type: application/json" \
  -X DELETE "$DNSMGR/api/v1/records/<server-name>/example.com./delete" \
  -d '{ "name": "www.example.com.", "type": "A" }'

# DNSSEC für eine Zone aktivieren
curl -sS -H "$H_AUTH" -X POST "$DNSMGR/api/v1/dnssec/<server-name>/example.com./enable"
```

### Multi-Server-Verhalten

Wenn mehrere PowerDNS-Server `Speichern: Ja` (`allow_writes=true`) haben **und** dieselbe Zone führen, schreibt der DNS Manager **automatisch auf alle**. Das gilt für `POST`, `PUT`, `DELETE` und `bulk` von Records sowie für DNSSEC-Operationen. Das Response-JSON enthält dann `details` mit Pro-Server-Status (`ok` / `skipped` / `error`):

```json
{
  "message": "Record updated",
  "details": {
    "targets": ["pdns-eu", "pdns-us"],
    "results": {
      "pdns-eu": { "status": "ok" },
      "pdns-us": { "status": "ok" }
    }
  }
}
```

Versuchst du, gegen einen Server mit `allow_writes=false` zu schreiben, kommt **HTTP 403** mit klarer Fehlermeldung.

---

## Endpunkt-Übersicht (Auszug)

Authentifizierung läuft bei allen Endpunkten unten gleich (Bearer-Token).

| Bereich    | Endpoint                                                               | Methoden          |
|------------|------------------------------------------------------------------------|-------------------|
| System     | `/health`, `/api/v1/auth/me`                                           | GET               |
| Server     | `/api/v1/servers`, `/api/v1/servers/{name}`                            | GET               |
| Zonen      | `/api/v1/zones/{server}`, `/api/v1/zones/{server}/{zone}/detail`       | GET               |
| Zonen      | `/api/v1/zones`                                                        | POST (anlegen)    |
| Zonen      | `/api/v1/zones/{server}/{zone}`                                        | PUT, DELETE       |
| Zonen      | `/api/v1/zones/{server}/{zone}/notify`                                 | POST              |
| Zonen      | `/api/v1/zones/{server}/{zone}/export`                                 | GET (BIND-Zone)   |
| Zonen      | `/api/v1/zones/import/preview`, `/api/v1/zones/import`                 | POST              |
| Records    | `/api/v1/records/{server}/{zone}`                                      | GET, POST, PUT    |
| Records    | `/api/v1/records/{server}/{zone}/delete`                               | DELETE            |
| Records    | `/api/v1/records/{server}/{zone}/bulk`                                 | POST              |
| DNSSEC     | `/api/v1/dnssec/{server}/{zone}/enable`, `/disable`, `/keys`           | POST / GET        |
| Suche      | `/api/v1/search?q=…`                                                   | GET               |
| Audit-Log  | `/api/v1/audit-log`, `/api/v1/audit-log/export` (CSV)                  | GET (admin)       |
| ACME       | `/api/v1/acme/present`, `/api/v1/acme/cleanup`                         | POST              |

Komplette Liste mit Body-Schemas im Browser unter `/docs` (mit `DOCS_ENABLED=true`) oder direkt in [`backend/app/routers/`](../backend/app/routers).

---

## Webhooks (eingehende Empfangs-Seite)

DNS Manager kann nach Änderungen an Zonen, Records, DNSSEC, ACME oder Audit-Events ein **POST**-Request an deinen Endpunkt schicken (konfigurierbar in **Einstellungen → API & Sicherheit → Webhooks**).

- **Methode:** POST
- **Body:** JSON, z. B.
  ```json
  {
    "version": 1,
    "event": "record.create",
    "occurred_at": "2025-04-26T11:08:42Z",
    "actor_user_id": 1,
    "data": { "...": "..." }
  }
  ```
- **Header:** `X-DNS-Manager-Signature: sha256=<hex>`
- **Signatur:** HMAC-SHA256 über den **rohen Request-Body** mit dem **Shared Secret**, das beim Anlegen einmalig im Panel angezeigt wurde.

### SSRF-Schutz

Standardmäßig dürfen Webhook-URLs **nicht** auf `localhost`, RFC1918-Netze, link-local oder multicast zeigen – sonst werden sie schon beim Speichern abgelehnt. Wer das in einem internen Netz braucht, setzt `WEBHOOK_ALLOW_PRIVATE_URLS=true` in der `.env`.

### Signatur prüfen (Python)

```python
import hmac, hashlib

SECRET = b"dein-shared-secret"  # aus dem Panel

def verify(raw_body: bytes, signature_header: str) -> bool:
    if not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(SECRET, raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))
```

### Signatur prüfen (Node)

```js
import crypto from "node:crypto";

const SECRET = "dein-shared-secret";

export function verify(rawBody, signatureHeader) {
  if (!signatureHeader?.startsWith("sha256=")) return false;
  const expected = crypto
    .createHmac("sha256", SECRET)
    .update(rawBody)
    .digest("hex");
  return crypto.timingSafeEqual(
    Buffer.from(expected, "hex"),
    Buffer.from(signatureHeader.slice(7), "hex"),
  );
}
```

**Wichtig:** Beim Verifizieren immer den **rohen** Request-Body verwenden, nicht das geparste JSON. Zwischen-Reformatierung (z. B. durch ein Body-Parsing-Middleware) ändert die Signatur.

---

## Praxis-Tipps

- **Token absichern:** Token nicht in Git, sondern in `~/.config/dnsmgr/token` o. ä. Beispiel-Skripte im Repo lesen `DNSMGR_URL` und `DNSMGR_TOKEN` aus Env oder `/etc/dnsmgr.env`.
- **Idempotenz:** Records-`POST` ist als „upsert auf gleichem Name+Type“ gedacht – mehrfaches Anlegen ergibt also kein Duplikat. Trotzdem in deiner Pipeline auf `409` reagieren, falls eine Zone-Anlage mit demselben Namen kollidiert.
- **Multi-Server beobachten:** im Response `details.results` prüfen. `status: error` einzelner Peers zeigt z. B. dass ein PowerDNS-Backend gerade nicht erreichbar ist – die anderen sind aber sauber durchgegangen.
- **Rate-Limit:** Token-Auth ist nicht rate-limited. Login-Endpoints sind es (HTTP 429), das betrifft Skripte aber nur, wenn sie mit Username/Passwort gegen `/api/v1/auth/login` arbeiten – mach das nicht, nimm einen Panel-Token.
