# PDNS Manager – Frontend

Single-Page-Web-UI des [PDNS Managers](../README.md). React 19 + Vite 8 (Rolldown) + Tailwind CSS 4 + i18next. Spricht ausschließlich die Backend-API unter `/api/v1` an und wird im Backend-Image als Static ausgeliefert (`backend/Dockerfile` baut das Frontend in einer eigenen Stufe und kopiert `dist/` nach `/app/app/static_new/`).

Im Produktivbetrieb baut das Backend-Dockerfile dieses Frontend automatisch mit – du musst hier nichts manuell ausführen, außer du arbeitest am Frontend.

## Lokale Entwicklung

Voraussetzung: Node 22+ und ein laufendes Backend auf `http://localhost:5380` (siehe [README – Lokale Entwicklung](../README.md#lokale-entwicklung-ohne-docker)).

```bash
cd frontend
npm install
npm run dev
```

Vite startet auf `http://localhost:3000` mit HMR. Aufrufe unter `/api` gehen per Proxy an `http://localhost:5380` (siehe `vite.config.js`).

| Skript | Zweck |
|---|---|
| `npm run build` | Production-Bundle nach `dist/` |
| `npm run preview` | gebautes Bundle lokal ansehen |
| `npm run lint` | ESLint (die CI bricht bei Fehlern ab) |
| `npm test` | Node-Testrunner über `tests/**/*.test.mjs` (reine Module, Slot-Verträge, Locale-Prüfung) |
| `npm run check:locales` | Prüfung der Sprachdateien (`-- --strict` wie in der CI) |
| `npm run merge:locales` | Locale-Fragmente in die Sprachdateien übernehmen und löschen |

## Aufbau

```
frontend/
├── src/
│   ├── main.jsx, App.jsx    # Einstieg, Routing (Seiten per Lazy-Load), Auth-Guards, Dialog-Host
│   ├── api.js               # API-Client (Cookie, Fehler-Normalisierung, 401/403-Behandlung)
│   ├── api/                 # API-Module je Funktion, werden in den Client gemischt (README dort)
│   ├── i18n.js              # Sprachliste, Nachladen der Sprachen, Fallback Englisch
│   ├── locales/             # en/de/sr/hr/bs/hu (gleiche Keys) + fragments/ für neue Texte
│   ├── pages/               # Top-Level-Seiten (Dashboard, Zonen, Zonenansicht, Einstellungen, Audit-Log …)
│   ├── zoneDetail/          # Zonenansicht: Context, Datenhook, Modelle (Records, Bulk, DNSSEC, Propagation) und Slots
│   ├── components/          # UI-Bausteine, nach Funktion gruppiert (bulk, dnssec, dyndns, lua, panelTokens,
│   │                        # settings, sso, users, userSecurity, webhooks, zoneHistory, banners, dialogs …)
│   ├── constants/           # Record-Typen, Audit-Aktionen (Slot auditActions/), Geheimnis-Felder
│   ├── lib/                 # reine Hilfsmodule ohne React (TTL, DNS-Namen, Datum, LUA, PTR, Slots, Dialog-Fokus …)
│   ├── context/, hooks/     # Versionsprüfung (nur Admins)
│   └── utils/
├── scripts/                 # check-locales.mjs, merge-locale-fragments.mjs, locale-allowlist(.d)
├── tests/                   # node --test; tests/integration/ für Abläufe über mehrere Module
├── index.html, vite.config.js, eslint.config.js, package.json
```

Einige Module sind bewusst ohne React geschrieben und direkt testbar, z. B. `src/lib/luaRecord.js` (LUA-Prüfung, ein Test hält sie synchron zum Backend), `src/zoneDetail/bulkModel.js`, `src/zoneDetail/dnssecModel.js`, `src/zoneDetail/propagationModel.js`, `src/lib/ttl.js`, `src/lib/dnsName.js`.

## Slots

Neue Funktionen docken über **Slot-Verzeichnisse** an, statt die großen Seiten zu ändern. Jede Slot-Datei exportiert Metadaten und eine Default-Komponente; eingesammelt wird per `import.meta.glob`, sortiert nach der Nummer im Dateinamen bzw. `order`. Ein Renderfehler in einem Slot blendet nur diesen Slot aus.

| Verzeichnis | Datei | Export | Wofür |
|---|---|---|---|
| `src/api/` | `<name>.js` | Objekt mit Methoden (Default-Export) | API-Methoden; Vertrag in [src/api/README.md](src/api/README.md) |
| `src/components/settings/tabs/` | `<id>.tab.jsx` | `tab = { id, order, labelKey, icon, adminOnly, hidden? }` | Reiter der Einstellungen, verlinkbar als `/settings?tab=<id>` |
| `src/components/settings/integrations-cards/` | `NN-<name>.card.jsx` | `card = { id, order }` | Karten im Reiter „API & Sicherheit“ (2FA, Passkeys, Tokens, Webhooks, DynDNS, SSO-Verknüpfung) |
| `src/components/settings/dns-cards/` | `NN-<name>.card.jsx` | `card = { id, order }` | Karten im Reiter „DNS-Optionen“ (PTR, LUA); [README](src/components/settings/dns-cards/README.md) |
| `src/zoneDetail/tabs/` | `NN-<id>.tab.jsx` | `tab = { id, order, labelKey, icon, when? }` | Tabs der Zonenansicht (Records, Verlauf, Propagation), `?tab=<id>` |
| `src/zoneDetail/header-actions/` | `NN-<name>.action.jsx` | `action = { id, order, when? }` | Knöpfe im Kopf der Zonenansicht (DS, Export, NOTIFY, Text-Editor, Record hinzufügen) |
| `src/zoneDetail/row-actions/` | `NN-<name>.action.jsx` | `action = { id, order, when? }` | Icons je Record-Zeile; [README](src/zoneDetail/row-actions/README.md) |
| `src/zoneDetail/form-extensions/` | `<name>.ext.jsx` | `ext = { id, when, position, initialState, collect, … }` | Zusatzfelder im Record-Dialog (PTR, LUA); [README](src/zoneDetail/form-extensions/README.md) |
| `src/zoneDetail/value-renderers/` | `<name>.renderer.jsx` | `renderer = { id, order?, when }` | eigene Darstellung der Wert-Zelle (z. B. LUA) |
| `src/components/banners/` | `NN-<name>.banner.jsx` | `banner = { id, adminOnly }` | Hinweise über jeder Seite (Geheimnisse, DynDNS hinter Proxy) |
| `src/components/dialogs/` | `NN-<name>.dialog.jsx` | `dialog = { id }` | dauerhaft gemountete Dialoge (z. B. Bestätigung kritischer Änderungen) |
| `src/components/users/badges/` | `NN-<name>.badge.jsx` | `badge = { id, order, when? }` | Badges in der Benutzerliste |
| `src/components/userSecurity/sections/` | `NN-<name>.section.jsx` | `section = { id, order?, titleKey?, when? }` | Abschnitte im Dialog „Passwort & Sicherheit“ |
| `src/constants/auditActions/` | `<name>.actions.js` | Liste `{ action, group, history? }` | Beschriftung und Gruppierung von Audit-Aktionen |

Der Metadaten-Export (`tab`, `card`, `action` …) braucht wegen `react-refresh/only-export-components` eine Zeile `// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten` davor. Die Verträge sind durch Tests abgesichert (z. B. `tests/settings-slots.test.mjs`, `tests/zoneDetailSlots.test.mjs`, `tests/api-modules.test.mjs`).

## Regeln

- **API nur über den Client:** Komponenten rufen `api.methodName(...)` auf (`import api from '../api'`), keine direkten `fetch`-Aufrufe. Der Client hängt das Cookie an, normalisiert Fehler (`err.status`, `err.message`, `err.code`, `err.payload`), leitet bei 401 zur Anmeldung, meldet einen erzwungenen Passwortwechsel (`X-Password-Change-Required`) und eine nötige Bestätigung (`stepup_required`/`reauth_required`) per Window-Event. Neue Methoden gehören in ein Modul unter `src/api/`; Methodennamen sind global eindeutig.
- **Dialoge:** `role="dialog"`, `aria-modal="true"` und `useDialogFocus` aus `src/lib/useDialogFocus.js` (Fokus beim Öffnen, Tab bleibt im Dialog, ESC schließt, Fokus-Rückgabe; bei übereinanderliegenden Dialogen reagiert nur der oberste). Tokens und Secrets zeigt `OneTimeSecretModal` genau einmal an.
- **Texte nur über i18n** (`t('bereich.key')`), Datum und Uhrzeit über `src/lib/datetime.js` bzw. `useDateFormat` im Format der gewählten Sprache.

## Übersetzungen

`src/locales/en.json` ist die Referenz; alle sechs Dateien haben dieselben Keys. Neue oder geänderte Texte kommen als **Fragment** je Sprache nach `src/locales/fragments/<name>.<sprache>.json` – Format, Konfliktregeln und Ablauf in der [Fragment-README](src/locales/fragments/README.md):

```bash
node scripts/merge-locale-fragments.mjs --check     # Konflikte prüfen, schreibt nichts
node scripts/check-locales.mjs --with-fragments     # Regeln auf dem zusammengeführten Stand
npm run merge:locales                               # Fragmente übernehmen und löschen (vor dem Release)
npm run check:locales -- --strict                   # wie in der CI
```

Die Prüfung meldet fehlende Keys und Pluralformen (`Intl.PluralRules`, z. B. `_few` für Serbisch, Bosnisch, Kroatisch), abweichende Platzhalter, leere Werte, im Code verwendete, aber fehlende Keys und Werte, die unverändert aus dem Englischen stammen. Begründete Ausnahmen (Fachbegriffe, dynamisch gebildete Keys) stehen in `scripts/locale-allowlist.d/<name>.json` ([README](scripts/locale-allowlist.d/README.md)). Englisch ist `fallbackLng`: Fehlt zur Laufzeit ein Key, erscheint der englische Text.

Sprachwahl: Vor der Anmeldung gilt die im Browser gespeicherte Wahl, sonst die Browsersprache, sonst Englisch; die Standardsprache des Servers (`DEFAULT_LANGUAGE`) nur, solange im Browser keine Wahl gespeichert ist. Nach der Anmeldung hat die Sprache aus dem Profil Vorrang und wird im Browser gemerkt.

## Tests

- `npm test` – Unit- und Vertragstests mit dem Node-Testrunner (ohne Browser, ohne Backend), u. a. API-Module, Slots, Locale-Prüfung, Bulk-, DNSSEC- und Propagations-Modelle, Dialog-Fokus.
- `tests/integration/` – Abläufe über mehrere Module (z. B. Bulk mit PTR, Verlauf, DynDNS/PTR-Vertrag).
- **UI-Smoke-Tests** im echten Browser (Playwright/Chromium im Container gegen die isolierte E2E-Umgebung): `scripts/e2e/run-e2e.sh --ui` aus dem Repo-Wurzelverzeichnis, Details in [scripts/e2e/ui/README.md](../scripts/e2e/ui/README.md). Neue Dialoge oder Abläufe bekommen dort eine Spec.

## Browser-Support

Aktuelle Versionen von Chrome, Firefox, Edge, Safari (Desktop und Mobile). Kein Support für IE.
