# E2E-Check-Module und `ctx`-API

Ein Check-Modul ist eine Datei `scripts/e2e/checks/<ws>.py` (Dateiname = Workstream-Kuerzel in
Kleinbuchstaben, z. B. `f6.py`, `f9f11.py`, `f14.py`). Sie gehoert ihrem Feature-Workstream; andere
Workstreams aendern sie nicht. Discovery: alle `*.py` ohne `_`-Praefix, `base` zuerst, dann
alphabetisch. Module duerfen sich nicht auf die Reihenfolge verlassen.

```python
"""E2E-Checks fuer F6 (Webhook-Outbox)."""


def check_fresh(ctx):            # nach Neuinstallation (run-e2e.sh)
    zone = ctx.unique_zone("f6")
    ctx.cleanup(lambda: ctx.api("DELETE", f"zones/ns1/{zone}"), "Zone loeschen")
    with ctx.step("Zone anlegen"):
        ctx.api("POST", "zones", json={"name": zone, "kind": "Native", "nameservers": ["ns1.e2e.test."]}, expect=200)
    ...


def check_upgrade(ctx):          # nach Upgrade einer 2.4.1-Datenbank (upgrade-241-to-30.sh)
    ...


def check_upgrade_restart(ctx):  # optional: nach dem zweiten Start derselben Upgrade-Instanz
    ...
```

Jede Funktion ist optional; fehlt sie fuer den Modus, gilt das Modul dort als SKIP. Ein Check ist
**fehlgeschlagen**, wenn er `CheckFailed` wirft (ueber `ctx.check`/`ctx.eq`/`ctx.fail`/`expect=`)
oder abstuerzt (ERROR). `ctx.skip("Grund")` meldet SKIP (z. B. Funktion in dieser Umgebung nicht
pruefbar) – nicht verwenden, um fehlende Features zu verstecken. Checks laufen nacheinander gegen
**dieselbe** Instanz: eindeutige Namen (`ctx.unique_zone`) und Aufraeumen (`ctx.cleanup`) sind Pflicht.
Nur Standardbibliothek und Pakete aus dem Backend-Image (z. B. `pymysql`, `httpx`, `dnspython`
falls im Lock) verwenden.

## Ablaufumgebung

Der Runner laeuft im compose-Dienst `tools` (Image `pdnsmgr-e2e:local`, Benutzer = Host-Benutzer)
im Netz `pdnsmgr-e2e-net`. Erreichbar: Backend `http://backend:8000`, PowerDNS `http://pdns1:8081`
und `http://pdns2:8081` (DNS auf Port 53 der Hosts `pdns1`/`pdns2`), Webhook-Empfaenger
`http://receiver:8080`, MariaDB `db:3306`. `scripts/e2e` liegt read-only unter `/e2e`, der
Arbeitsordner des Laufs beschreibbar unter `/state`.

## `ctx`-Referenz

### Allgemein

| Attribut / Methode | Bedeutung |
|---|---|
| `ctx.mode` | `"fresh"`, `"upgrade"` oder `"upgrade-restart"` |
| `ctx.base_url` | `http://backend:8000` |
| `ctx.state` | dict, ueber Module hinweg geteilt; im Upgrade-Pfad von `upgrade` nach `upgrade-restart` uebernommen (JSON-faehige Werte) |
| `ctx.seed` | nur Upgrade: Inhalt von `seed.json` (siehe unten), sonst `{}` |
| `ctx.log(msg)` | Ausgabe mit Modulpraefix |
| `with ctx.step("Titel"):` | benannter Teilschritt; Fehlermeldungen tragen den Titel |
| `ctx.check(cond, msg)` / `ctx.eq(ist, soll, msg)` / `ctx.fail(msg)` | Zusicherungen (`CheckFailed`) |
| `ctx.skip(msg)` | Modul als SKIP beenden |
| `ctx.wait_until(fn, timeout=30, interval=0.5, msg="")` | wiederholt `fn()` bis wahr, gibt den Wert zurueck |
| `ctx.unique(prefix)` / `ctx.unique_zone(prefix)` | `prefix-1a2b3c4d` / `prefix-1a2b3c4d.e2e.test.` |
| `ctx.cleanup(fn, label="")` | Aufraeumfunktion, laeuft nach dem Modul (auch bei Fehler), LIFO |
| `ctx.backend_log()` | Container-Log des Backends (Schnappschuss vor dem Runner-Start dieses Modus) |

### HTTP gegen PDNS Manager

| Attribut / Methode | Bedeutung |
|---|---|
| `ctx.api(method, path, json=…, form=…, body=…, headers=…, token=…, expect=…, timeout=60)` | Aufruf mit `ctx.admin_token` als Bearer (Default). `token=None` = anonym, `token="…"` = anderer Token. `path` ohne fuehrenden `/` ist relativ zu `/api/v1/` (`"zones/ns1"`), mit `/` absolut (`"/health"`, `"/nic/update"`). `expect` = int oder Tupel erlaubter Status-Codes (sonst `CheckFailed` mit Antworttext); ohne `expect` keine Pruefung. |
| Rueckgabe `Response` | `.status`, `.headers` (Kleinbuchstaben), `.body` (bytes), `.text`, `.json()`, `.ok` |
| `ctx.admin_token` | Panel-Token des Admins (fresh: vom Runner angelegt; upgrade: Alt-Token aus 2.4.1) |
| `ctx.user_token`, `ctx.user_id`, `ctx.user_name`, `ctx.user_password` | Nicht-Admin: fresh `e2e-user` ohne Zonenrechte; upgrade `alice` (TOTP, alpha=manage, beta=read) |
| `ctx.user_totp_secret` | upgrade: TOTP-Geheimnis von `alice`; fresh `None` |
| `ctx.admin_username`, `ctx.admin_password` | Admin-Zugang |
| `ctx.admin_session`, `ctx.user_session` | eingeloggte Cookie-Sessions (`HttpSession`) fuer Endpunkte, die eine Browser-Session verlangen (Token-/Webhook-Verwaltung, Settings, Benutzerverwaltung); Methoden `.api/.request(method, path, json_body=…)`, `.get/.post/.put/.delete(path, json=…, expect=…)` |
| `ctx.login(user, pw, totp_secret=None, expect_ok=True)` | Login inkl. TOTP-Zweitschritt → `(Response, HttpSession)` |
| `ctx.session(user, pw, totp_secret=None)` | nur die eingeloggte `HttpSession` |
| `ctx.http(token=None)` | neuer Client mit eigenen Cookies |
| `ctx.totp(secret)` | aktueller TOTP-Code; wartet automatisch auf ein neues 30-s-Fenster, wenn der Code in diesem Lauf schon benutzt wurde (Replay-Schutz des Backends) |
| `ctx.set_user_zones(user_id, {zone: "read"\|"manage"})` | setzt die Zonenrechte (ersetzt alle bisherigen) |

### PowerDNS direkt (`ctx.pdns`)

| Methode | Bedeutung |
|---|---|
| `ctx.pdns.servers` | `["ns1", "ns2"]` (Panel-Servernamen = Schluessel) |
| `ctx.pdns.url(srv)`, `ctx.pdns.api_key(srv)` | URL/API-Key im E2E-Netz (ns1: `e2ekey`, ns2: `e2ekey2`) |
| `ctx.pdns.zone(srv, zone)` | Zonen-JSON oder `None` |
| `ctx.pdns.zones(srv)` | Liste der Zonennamen |
| `ctx.pdns.rrset(srv, zone, name, type)` | RRset-JSON oder `None` |
| `ctx.pdns.contents(srv, zone, name, type)` | sortierte Werte des RRsets (`[]` wenn leer) |
| `ctx.pdns.export(srv, zone)` | Rohantwort des PowerDNS-Exports |
| `ctx.pdns.create_zone(srv, zone, nameservers, rrsets=None)` / `ctx.pdns.delete_zone(srv, zone)` | Zonen an PDNS Manager vorbei anlegen/loeschen (z. B. Peer-Drift erzeugen) |
| `ctx.pdns.request(srv, method, path, json_body=None, expect=None)` | beliebiger API-Aufruf relativ zu `/api/v1/servers/localhost` |

### Webhook-Empfaenger (`ctx.receiver`)

| Methode | Bedeutung |
|---|---|
| `ctx.receiver.url(name="default", **params)` | Ziel-URL fuer Webhook-Konfigurationen, z. B. `url("f6", fail=2)` |
| `ctx.receiver.deliveries(name=None)` | Zustellungen (alle oder nur `/hook/<name>`), aelteste zuerst; Felder `path`, `query`, `headers`, `body_text`, `body_b64`, `json`, `attempt`, `key`, `response_status`, `received_at` |
| `ctx.receiver.wait_for(name=None, count=1, timeout=30, predicate=None)` | wartet auf Zustellungen, sonst `CheckFailed` |
| `ctx.receiver.clear()` | Speicher leeren (betrifft alle Module – nur mit eigenen `name`-Pfaden arbeiten) |
| `ctx.receiver.raw_body(d)`, `ctx.receiver.hmac_sha256(secret, raw)` | Hilfen fuer Signaturpruefungen |

### Datenbank

| Methode | Bedeutung |
|---|---|
| `ctx.db(sql, params=None)` | SQL direkt in der Panel-Datenbank (`pymysql`, Platzhalter `%s`, autocommit) → Liste von Dicts |
| `ctx.db_value(sql, params=None)` | erster Wert der ersten Zeile |

## Seed-Daten im Upgrade-Pfad (`ctx.seed`)

Erzeugt von `seed_241.py` mit dem 2.4.1-Code:

| Schluessel | Inhalt |
|---|---|
| `source_version` | Version des Altstands (`2.4.1`) |
| `zones` | `{"alpha": "alpha.e2e-seed.test.", "beta": …, "gamma": …}` – auf ns1 und ns2 angelegt, je `www` A 192.0.2.1 und TXT am Apex |
| `admin` | `{"id", "username": "admin", "password"}` |
| `users.alice` | `{"id", "username", "password", "totp_secret", "zones": {alpha: manage, beta: read}}` (TOTP aktiv) |
| `users.bob` | Benutzer ohne Zonenrechte |
| `tokens` / `token_ids` | Panel-Tokens `admin`, `alice` (aktiv), `admin_inactive` (`is_active=0`) |
| `acme_token` | `{"id", "plaintext", "allowed_zones": [alpha]}` |
| `webhooks` | `[{"id", "user", "name", "url", "path", "secret", "events"}]` – Klartext-Secret wie 2.4.1 (`seed-admin` → `/hook/seed-admin`, `seed-alice` → `/hook/seed-alice`) |
| `servers` | `{"ns1": {"url", "api_key"}, "ns2": …}` – in `server_configs` im Klartext |
| `settings` | `{"smtp_password", "captcha_secret_key"}` in `system_settings` (Captcha-Provider `none`) |
| `audit_ids` | IDs der Audit-Zeilen im v1-Format (Zone-CREATE, Record CREATE/UPDATE/DELETE, Fehler-Eintrag, LOGIN, PANEL_TOKEN_CREATE), Zeitstempel 3 Tage alt |

Reicht der Seed fuer einen Feature-Check nicht aus, beantragt der Workstream die Erweiterung in
seiner Integrationsnotiz (Besitzer: W0-TESTINFRA bzw. Integrator) – Check-Module legen zusaetzliche
Altdaten nicht selbst an, da sie nach dem Start des neuen Stands laufen.
