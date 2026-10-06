# Stand: Validierung, Basis-URL & Mehrsprachigkeit

> Dieses Dokument war ursprünglich ein Planungspapier. Alle drei Themen sind umgesetzt – die Datei beschreibt jetzt **wie** und **wo**, damit Wartung und Erweiterung einfach bleiben (Stand 3.0).

## 1. Validierung der Profilfelder

**Ziel:** International tauglich, ohne Länder-Sonderregeln. Pragmatisch, nicht streng.

| Feld     | Regel                                                                 |
|----------|-----------------------------------------------------------------------|
| Telefon  | Freitext mit mindestens **einer Ziffer**, max. 25 Zeichen. |
| PLZ      | Freitext, max. 20 Zeichen (UK „SW1A 1AA“ klappt, DE „12345“ auch). |
| Ort      | Freitext, max. 100 Zeichen. Keine Buchstaben-only-Regel (Städte mit Zahlen/Bindestrichen). |
| Land     | Freitext, max. 100 Zeichen.                                           |

Leere Eingaben leeren das Feld (ein nicht mitgeschicktes Feld bleibt unverändert).

**Wo im Code:**

- Backend: Pydantic-Schema `ProfileUpdate` in `backend/app/routers/auth.py` (`PUT /api/v1/auth/me`).
- Frontend: `pattern` / `maxLength` an den Inputs im Profil-Reiter (`frontend/src/components/settings/tabs/profile.tab.jsx`), Texte über i18n.

---

## 2. Öffentliche Basis-URL für E-Mails und Links

**Problem damals:** `request.base_url` lieferte oft `http://10.x.x.x:5380` und damit unbrauchbare Reset-Links.

**Lösung:** Admins pflegen die **öffentliche Basis-URL** unter **Einstellungen → Profil → Öffentliche Basis-URL** (nur für Admins sichtbar). Der Wert liegt in der Tabelle `system_settings` unter dem Schlüssel `app_base_url`; `PUT /api/v1/settings/app-info` mit `app_base_url: ""` leert ihn.

**Verwendung:**

- Passwort-Reset-Mails (auch der vom Admin verschickte Reset-Link) und die Welcome-Mail.
- Redirect-URI für OIDC (`<basis-url>/api/v1/auth/oidc/callback`), Beispiele der DynDNS-Anleitung und der Prometheus-Konfiguration im Panel.
- Passwort-Reset-Links: Ist der Wert leer, gilt der erste Eintrag aus `WEBAUTHN_ORIGIN` (`.env`). Aus dem `Host`-Header der Anfrage werden Reset-Links seit 2.4.1 **nicht** mehr gebaut – ohne Basis-URL (und ohne `WEBAUTHN_ORIGIN`) verschickt das Panel keine Reset-Mails und warnt beim Start im Log.
- Welcome-Mail (`{login_url}`): ohne Basis-URL die Adresse, unter der die Registrierung aufgerufen wurde.

**Wo im Code:**

- Setting: `backend/app/routers/settings.py` (`PUT /settings/app-info`, `GET /settings/admin-info`).
- Auflösung: `resolve_public_base_url` in `backend/app/services/password_reset_mail.py`; Mail-Texte in `backend/app/services/email_templates.py`.
- UI: Profil-Reiter der Einstellungen (Admin-Bereich).

**Hinweis in der UI:** Die Adresse sollte die öffentlich erreichbare URL sein – ohne abschließenden `/` (z. B. `https://dns.example.com`).

---

## 3. Mehrsprachigkeit (i18n)

**Status:** Komplett eingebaut. `react-i18next` + `i18next`. Aktuell **6 Sprachen**: en, de, sr, hr, bs, hu.

### Wie es funktioniert

- **Referenz:** `frontend/src/locales/en.json`. Die anderen Sprachen liegen daneben (`de.json`, `sr.json`, `hr.json`, `bs.json`, `hu.json`) und haben dieselben Keys.
- **Laden:** Englisch ist im Start-Bundle; die übrigen Sprachen werden erst bei Bedarf nachgeladen (`frontend/src/i18n.js`). Fehlt ein Key, zeigt die UI per `fallbackLng: 'en'` den englischen Wert. Nichts bricht ab.
- **Code:** Komponenten holen Texte über `t('bereich.aktion')`. Direkte deutsche/englische Strings im JSX gehören nicht in den Code.
- **Welche Sprache gilt:**
  1. Vor der Anmeldung: die im Browser gespeicherte Wahl (`localStorage`, Schlüssel `lang`).
  2. Ist im Browser **keine** Wahl gespeichert, gilt die Standardsprache des Servers (`DEFAULT_LANGUAGE`, über `GET /api/v1/settings/app-info`; ohne Angabe `de`). Sie wird nicht gemerkt. Die Browsersprache (bzw. Englisch, wenn sie nicht unterstützt wird) gilt nur beim Start, bis die App-Info geladen ist, oder wenn sie nicht geladen werden kann.
  3. Nach der Anmeldung hat die Sprache aus dem Profil (`users.preferred_language`) Vorrang und wird im Browser gemerkt.
  4. Eine Sprachwahl in den Einstellungen oder in der mobilen Kopfleiste wird im Profil gespeichert. Schlägt das Speichern fehl, bleibt die vorherige Sprache.

### Übersetzungen pflegen

Neue oder geänderte Texte kommen als **Fragment** je Sprache nach `frontend/src/locales/fragments/<name>.<sprache>.json` (Format und Regeln: `frontend/src/locales/fragments/README.md`) und werden vor dem Release mit `npm run merge:locales` in die Sprachdateien übernommen. Die Prüfung läuft in der CI:

```bash
cd frontend
npm run check:locales -- --strict
```

Sie meldet fehlende Keys, fehlende Pluralformen (laut `Intl.PluralRules`, z. B. `_few` für sr/hr/bs), abweichende Platzhalter (`{{var}}`), leere Werte, im Code verwendete, aber fehlende Keys und Werte, die unverändert aus dem Englischen stammen (Ausnahmen mit Begründung in `frontend/scripts/locale-allowlist.d/`).

`scripts/sync-locales.mjs` (im Repo-Wurzelverzeichnis) gleicht alle Dateien gegen `en.json` ab: fehlende Keys mit dem englischen Wert ergänzen, verwaiste Keys entfernen, Pluralformen der Zielsprache erhalten. Es ist ein Werkzeug für Pflege-Commits; mit `--strict` meldet die Prüfung so ergänzte englische Werte als nicht übersetzt.

### Neue Sprache beisteuern

1. `frontend/src/locales/en.json` als Vorlage kopieren, z. B. zu `it.json`, und alle Werte übersetzen (Keys und Platzhalter unverändert, Pluralformen der Sprache ergänzen).
2. In `frontend/src/i18n.js` einen Eintrag in `LANGUAGES` (Code, Label, Flagge) ergänzen – die Datei wird automatisch nachgeladen.
3. Den Code in `PROFILE_LANGUAGES` in `backend/app/routers/auth.py` ergänzen, damit er im Profil gespeichert werden kann.
4. `npm run check:locales -- --strict` ausführen, PR aufmachen.

### Backend-Sprache

E-Mail-Templates gibt es auf Deutsch und Englisch (`SUPPORTED_LANGS` in `backend/app/services/email_templates.py`). Die Sprache folgt `users.preferred_language`, dann `DEFAULT_LANGUAGE` aus der `.env`, dann Englisch; andere Sprachen erhalten englische E-Mails. Neue Mail-Sprache → Texte dort ergänzen und in `SUPPORTED_LANGS` aufnehmen.

---

## Reihenfolge der Umsetzung (historisch)

| Schritt | Status |
|---------|--------|
| Basis-URL als System-Setting + E-Mail-Links | umgesetzt (v2.3.x), ohne Host-Header-Fallback seit v2.4.1 |
| Profil-Validierung (Telefon/PLZ/Ort/Land)   | umgesetzt                      |
| i18n-Infrastruktur (react-i18next)          | umgesetzt                      |
| User-Sprache (`preferred_language`)         | umgesetzt                      |
| Englisch + 5 weitere Sprachen               | umgesetzt (en/de/sr/hr/bs/hu), vollständig seit 3.0 |
| Backend-Mails übersetzt                     | umgesetzt (de/en)              |

Wer hier weitermachen will, sollte als Nächstes überlegen:

- Dropdown mit ISO-Ländern für „Land“ (statt Freitext) – einheitlich + automatisch übersetzbar.
- Optionale länderabhängige Telefon-/PLZ-Validierung mit `libphonenumber-js` (Frontend) und `phonenumbers` (Python). Aktuell bewusst nicht eingebaut, weil das Helper-Bibliotheken zieht und beim Falsch-Land mehr Frust als Nutzen bringt.
