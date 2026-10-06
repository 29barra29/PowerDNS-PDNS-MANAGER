# Security Policy / Sicherheitsrichtlinie

---

## English

### Supported Versions

Security fixes are provided for the current minor release line only. Please update to the latest version before reporting.

| Version | Supported          |
| ------- | ------------------ |
| v2.4.x  | :white_check_mark: |
| v2.3.x  | :x:                |
| v2.2.x  | :x:                |
| v2.1.x  | :x:                |
| v2.0.x  | :x:                |
| < 2.0   | :x:                |

### Reporting a Vulnerability

Please do **not** report security vulnerabilities through public GitHub issues.

**How to report:** Go to the **Security** tab of this repository → click **Report a vulnerability**. Your report will be sent to the maintainers privately. We will respond as soon as possible, usually within 48 hours.


Please include the following information in your report:
* The version of PDNS Manager you are using
* Steps to reproduce the vulnerability
* What you expected to happen
* What actually happened

We take security issues very seriously and will work with you to understand and resolve the issue as quickly as possible.

### Secrets at rest (since 3.0)

* Reversibly stored secrets are encrypted in the database (Fernet, prefix `enc:v1:`): PowerDNS API keys, webhook
  secrets **and webhook URLs**, TOTP secrets, the SMTP password, the captcha secret, OIDC/LDAP secrets and the
  Prometheus scrape token. A database dump without the key does not reveal them.
* The key comes from `SECRET_ENCRYPTION_KEY` in the `.env`, from `SECRET_ENCRYPTION_KEY_FILE`, or from
  `/app/data/.secret_key` in the `backend_data` volume (created on first start). **Back up the key separately from
  database dumps** – with the key, a dump reveals the secrets; without it, they cannot be recovered. `./update.sh`
  stores a copy outside the stack directory (`${PDNSMGR_KEY_BACKUP_DIR:-$HOME/.pdnsmgr-keys}`, mode 0600).
* Passwords are stored as bcrypt hashes; panel, ACME and DynDNS tokens only as SHA-256 hashes (shown once on creation).
* Database dumps taken **before** the upgrade to 3.0 still contain the secrets in plain text – store them securely or
  delete them.
* Status and recovery: *Settings → Security* in the panel and `python -m app.cli.secrets` in the backend container;
  details in [INSTALL.md](INSTALL.md#geheimnisse--schlüssel) (German).

### Hardening checklist

Run the panel behind a TLS reverse proxy with `AUTH_COOKIE_SECURE=true`, bind port 5380 to `127.0.0.1`, set
`TRUST_PROXY_HEADERS=true` behind the proxy, restrict `/metrics` to the Prometheus server, limit API tokens to the
required zones, read-only access and an expiry date, and keep the encryption key and the `.env` in a separate backup.

---

## Deutsch

### Unterstützte Versionen

Diese Tabelle zeigt, welche Versionen des Projekts mit Sicherheitsupdates unterstützt werden.

| Version | Unterstützt         |
| ------- | ------------------- |
| v2.4.x  | :white_check_mark:  |
| v2.3.x  | :x:                 |
| v2.2.x  | :x:                 |
| v2.1.x  | :x:                 |
| v2.0.x  | :x:                 |
| < 2.0   | :x:                 |

### Sicherheitslücke melden

Bitte melden Sie Sicherheitslücken **nicht** in öffentlichen GitHub-Issues.

**So melden Sie:** Gehen Sie zum **Security**-Tab dieses Repositorys → klicken Sie auf **Report a vulnerability** (Sicherheitslücke melden). Ihre Meldung geht vertraulich an die Maintainer. Wir melden uns in der Regel innerhalb von 48 Stunden.


Bitte geben Sie in Ihrer Meldung an:
* Welche Version des PDNS Manager Sie verwenden
* Schritte zur Reproduktion der Lücke
* Was Sie erwartet haben
* Was tatsächlich passiert ist

Wir nehmen Sicherheitsfragen sehr ernst und arbeiten mit Ihnen daran, das Problem schnell zu verstehen und zu beheben.

### Gespeicherte Geheimnisse (ab 3.0)

* Umkehrbar gespeicherte Geheimnisse liegen verschlüsselt in der Datenbank (Fernet, Präfix `enc:v1:`):
  PowerDNS-API-Keys, Webhook-Secrets **und Webhook-URLs**, 2FA-Geheimnisse, SMTP-Passwort, Captcha-Secret,
  OIDC-/LDAP-Secrets und der Prometheus-Scrape-Token. Ein Datenbank-Dump ohne den Schlüssel verrät sie nicht.
* Der Schlüssel kommt aus `SECRET_ENCRYPTION_KEY` in der `.env`, aus `SECRET_ENCRYPTION_KEY_FILE` oder aus
  `/app/data/.secret_key` im Volume `backend_data` (beim ersten Start erzeugt). **Den Schlüssel getrennt vom
  Datenbank-Dump sichern** – mit dem Schlüssel verrät ein Dump die Geheimnisse, ohne ihn sind sie nicht
  wiederherstellbar. `./update.sh` legt eine Kopie außerhalb des Stack-Ordners ab
  (`${PDNSMGR_KEY_BACKUP_DIR:-$HOME/.pdnsmgr-keys}`, Rechte 0600).
* Passwörter liegen als bcrypt-Hash vor, Panel-, ACME- und DynDNS-Tokens nur als SHA-256-Hash (Klartext nur einmal beim
  Anlegen).
* Datenbank-Dumps von **vor** dem Update auf 3.0 enthalten die Geheimnisse noch im Klartext – sicher verwahren oder
  löschen.
* Status und Wiederherstellung: **Einstellungen → Sicherheit** im Panel und `python -m app.cli.secrets` im
  Backend-Container; Details in [INSTALL.md](INSTALL.md#geheimnisse--schlüssel).

### Härtung

Panel hinter einem Reverse-Proxy mit TLS und `AUTH_COOKIE_SECURE=true` betreiben, Port 5380 an `127.0.0.1` binden,
hinter dem Proxy `TRUST_PROXY_HEADERS=true` setzen, `/metrics` nur für den Prometheus-Server freigeben, API-Tokens auf
die nötigen Zonen, Leserecht und eine Laufzeit beschränken, Schlüssel und `.env` in einem getrennten Backup aufbewahren.
Die vollständige Checkliste steht in [INSTALL.md](INSTALL.md#sicherheits-checkliste).
