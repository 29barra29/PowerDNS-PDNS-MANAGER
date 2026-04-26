# Stand: Validierung, Basis-URL & Mehrsprachigkeit

> Dieses Dokument war ursprünglich ein Planungspapier. Alle drei Themen sind inzwischen umgesetzt – die Datei beschreibt jetzt **wie** und **wo**, damit Wartung und Erweiterung einfach bleiben.

## 1. Validierung der Profilfelder

**Ziel:** International tauglich, ohne Länder-Sonderregeln. Pragmatisch, nicht streng.

| Feld     | Regel                                                                 |
|----------|-----------------------------------------------------------------------|
| Telefon  | Erlaubt: Ziffern, Leerzeichen, `+`, `-`, `()`, `/`. Mindestens **eine Ziffer**. Max. 25 Zeichen. |
| PLZ      | Frei, max. 20 Zeichen, alphanumerisch + Leerzeichen (UK „SW1A 1AA“ klappt, DE „12345“ auch). |
| Ort      | Freitext, max. 100 Zeichen. Keine Buchstaben-only-Regel (Städte mit Zahlen/Bindestrichen). |
| Land     | Freitext, max. 100 Zeichen.                                           |

**Wo im Code:**

- Backend: Pydantic-Validatoren in `backend/app/schemas/` (Profil-Schemas).
- Frontend: `pattern` / `maxLength` an den Inputs + Live-Hinweis aus `src/locales/<lang>.json` unter `profile.*`.
- Fehlerschlüssel sind übersetzt in **allen** Sprachen (en/de/sr/hr/bs/hu).

---

## 2. Öffentliche Basis-URL für E-Mails

**Problem damals:** `request.base_url` lieferte oft `http://10.x.x.x:5380` und damit unbrauchbare Reset-Links.

**Lösung:** Admin pflegt eine **Öffentliche App-URL** in `Einstellungen → System / Allgemein`. Wert wird in `system_settings` als Schlüssel `public_base_url` (bzw. `app_base_url`) abgelegt.

**Verwendung:**

- Passwort-zurücksetzen-Mail, Welcome-Mail, alle anderen E-Mail-Links bauen Links als `{public_base_url}/...`.
- Ist der Wert leer, fällt das Backend auf `request.base_url` zurück (alter Pfad).

**Wo im Code:**

- Setting: `backend/app/routers/settings.py`, gespeichert pro Schlüssel in der `system_settings`-Tabelle.
- Verwendung: `backend/app/services/email_templates.py` und Aufrufer in `backend/app/routers/auth.py`.
- UI: `Einstellungen → System` (Admin-Bereich).

**Hinweis in der UI:** „Wird z. B. für Links in E-Mails (Passwort zurücksetzen) genutzt. Sollte die öffentlich erreichbare URL sein – ohne abschließenden `/` (z. B. `https://dns.example.com`).“

---

## 3. Mehrsprachigkeit (i18n)

**Status:** Komplett eingebaut. `react-i18next` + `i18next`. Aktuell **6 Sprachen**: en, de, sr, hr, bs, hu.

### Wie es funktioniert

- **Source of Truth:** `frontend/src/locales/en.json`.
- **Andere Sprachen:** liegen daneben (`de.json`, `sr.json`, `hr.json`, `bs.json`, `hu.json`).
- **Keys** sind dieselben in allen Dateien. Ist ein Key fehlend, fällt die UI per `fallbackLng: 'en'` auf den englischen Wert zurück. Nichts bricht ab.
- **Code:** Komponenten greifen Texte über `t('bereich.aktion')` ab. Direkte deutsche/englische Strings im JSX sind ein Lint-Smell.
- **User-Sprache:** liegt in der DB am User-Account (`users.preferred_language`) und überstimmt den Browser-Default. Der Sprachschalter in der Sidebar (Dropdown mit Flagge) speichert sofort.

### Übersetzungen pflegen

Das Sync-Skript hält alle Sprachen auf identische Key-Liste:

```bash
node scripts/sync-locales.mjs
```

- ergänzt fehlende Keys in nicht-`en`-Dateien mit dem englischen Wert,
- entfernt Keys, die nicht mehr in `en.json` existieren,
- listet pro Datei auf, was hinzugefügt/entfernt wurde,
- lässt bestehende Übersetzungen unverändert.

Stand v2.3.7: 770 Keys identisch in allen 6 Sprachen.

### Neue Sprache beisteuern

1. `frontend/src/locales/en.json` als Vorlage kopieren, z. B. zu `it.json`.
2. Werte übersetzen, Keys nicht anfassen.
3. In `frontend/src/i18n.js` einen Eintrag in `LANGUAGES` (Code, Label, Flagge) und im `resources`-Block ergänzen.
4. PR aufmachen.

### Backend-Sprache

E-Mail-Templates folgen `users.preferred_language`, dann `DEFAULT_LANGUAGE` aus der `.env`, dann Englisch. Templates liegen zentral in `backend/app/services/email_templates.py` – neue Sprache → da eintragen.

---

## Reihenfolge der Umsetzung (historisch)

| Schritt | Status |
|---------|--------|
| Basis-URL als System-Setting + E-Mail-Links | umgesetzt (v2.3.x)             |
| Profil-Validierung (Telefon/PLZ/Ort/Land)   | umgesetzt                      |
| i18n-Infrastruktur (react-i18next)          | umgesetzt                      |
| User-Sprache (`preferred_language`)         | umgesetzt                      |
| Englisch + 5 weitere Sprachen               | umgesetzt (en/de/sr/hr/bs/hu)  |
| Backend-Mails übersetzt                     | umgesetzt                      |

Wer hier weitermachen will, sollte als Nächstes überlegen:

- Dropdown mit ISO-Ländern für „Land“ (statt Freitext) – einheitlich + automatisch übersetzbar.
- Optionale länderabhängige Telefon-/PLZ-Validierung mit `libphonenumber-js` (Frontend) und `phonenumbers` (Python). Aktuell bewusst nicht eingebaut, weil das Helper-Bibliotheken zieht und beim Falsch-Land mehr Frust als Nutzen bringt.
