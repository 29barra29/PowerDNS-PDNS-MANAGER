# API-Module (`src/api/*.js`)

Ab 3.0 wächst `src/api.js` nicht mehr. Jeder Workstream bzw. jedes Feature liefert seine Endpunkt-Methoden als
eigenes Modul in diesem Ordner. `api.js` lädt alle Module per `import.meta.glob('./api/*.js', { eager: true })`
(sortiert nach Dateiname) und mischt den Default-Export mit `Object.assign` in den Prototyp von `APIClient`.
Aufrufer merken davon nichts: `import api from '../api'` und `api.methodName(...)` wie bisher.

## Vertrag

```js
// src/api/f7-fe.js
// Nur nötig, wenn absichtlich eine Kernmethode aus api.js ersetzt wird (Plan B.14, Regel 10):
export const overrides = ['getAuditLog']

export default {
    // `this` ist der API-Client
    getAuditLog({ limit = 50, offset = 0, signal } = {}) {
        return this.request('GET', `/audit-log?limit=${limit}&offset=${offset}`, null, { signal })
    },
    getZoneHistory(server, zone, opts = {}) {
        return this.request('GET', `/zones/${encodeURIComponent(server)}/${encodeURIComponent(zone)}/history`, null, opts)
    },
}
```

Regeln:
- Default-Export ist ein **Objekt-Literal** (`export default { … }` oder `const x = { … }; export default x`).
  Kein Spread, keine berechneten Namen, keine Getter/Setter. `frontend/tests/api-modules.test.mjs` liest die Module
  statisch und bricht sonst mit `parse` ab.
- Methodennamen sind **global eindeutig**: über alle Module und gegenüber den Kernmethoden in `api.js`.
- Eine Kernmethode ersetzen darf nur, wer sie in `export const overrides = [...]` nennt **und** laut Plan B.14
  dafür freigegeben ist. Die Freigaben stehen in `ALLOWED_OVERRIDES` in `frontend/tests/api-modules.test.mjs`
  (Schlüssel = Dateiname ohne `.js`, klein geschrieben, z. B. `f7-fe`). Alles andere ist ein Testfehler; der
  Dev-Server warnt zusätzlich in der Konsole.
- Dateiname: Workstream-ID klein (`f7-fe.js`, `f2f3.js`) oder ein sprechender Name. Für Module mit
  Überschreibungen muss der Dateiname zum Schlüssel in `ALLOWED_OVERRIDES` passen.
- **Kein Top-Level-Zugriff auf `../api`**: `api.js` importiert die Module, ein Import zurück wäre zyklisch. Helfer
  gibt es über `this` (siehe unten). Benannte Funktions-Exporte (`extractErrorMessage`, Event-Namen) dürfen
  importiert und *innerhalb* von Methoden benutzt werden.
- Pfadsegmente mit Nutzereingaben (Server-, Zonennamen) mit `encodeURIComponent` kodieren, Query-Strings über
  `lib/buildQuery.js`.
- Body-lose POSTs senden `{}` (manche Setups stolpern über JSON-Content-Type ohne Body).
- Fehlertexte, die das Modul selbst erzeugt, über i18n (`import i18n from '../i18n'`, eigener Key-Präfix).

## Helfer des Clients (`this.…`)

| Methode | Zweck |
|---|---|
| `request(method, path, data = null, { signal, authRedirect = true })` | JSON-Aufruf. `204` → `null`, JSON → Objekt, sonst Text. |
| `requestRaw(method, path, { body, headers, signal, authRedirect, credentials, fallback })` | Rohaufruf, liefert die `Response` nur bei 2xx (z. B. für Blob-Downloads, FormData-Uploads). |
| `_publicJson(path, body, fallback)` | Öffentlicher JSON-POST ohne 401-Sprung (Login-Varianten). `fallback` = i18n-Key oder Text. |
| `getUser()`, `setUser(u)`, `clearUser()`, `isLoggedIn()` | Benutzer-Cache (nur im Speicher). |

Fehler: geworfen wird ein `Error` mit
- `message` – lesbarer Text (`extractErrorMessage`: `detail`, `detail.message`, Pydantic-Liste, PowerDNS-Wrapper),
- `status` – HTTP-Status (`0` bei Netzwerkfehler, dann zusätzlich `network: true`),
- `payload` – Rohantwort,
- `code` – maschinenlesbarer Code, falls vorhanden (`detail.code`, `code` oder ein reiner Code-String als `detail`).

Abbruch: `{ signal }` durchreichen; ein `AbortError` wird **unverändert** geworfen (kein „Server nicht erreichbar“).
Aufrufer prüfen `err.name === 'AbortError'` und zeigen dann nichts an.

## Zentrales Verhalten von `api.js`

- **401**: Benutzer-Cache leeren und – außer auf den Seiten in `AUTH_REDIRECT_EXEMPT_PATHS` (`/login`, `/setup`,
  `/register`, `/forgot-password`, `/reset-password`) – hart auf `/login` wechseln. Neue öffentliche SPA-Routen
  müssen dort eingetragen werden. Einzelne Aufrufe können mit `authRedirect: false` den Sprung unterdrücken
  (der Fehler hat dann `status === 401`).
- **403 + Header `X-Password-Change-Required`** (F3): `must_change_password` im Cache setzen, Window-Event
  `PASSWORD_CHANGE_EVENT` (`'pdns:password-change-required'`). `App.jsx` zeigt daraufhin `ForcePasswordChange`.
- **403 mit Code `stepup_required` oder `reauth_required`** (S8; Code aus `detail.code`, `code`, `detail` als
  Code-String oder Header `X-Step-Up-Required`): Window-Event `STEP_UP_EVENT` (`'pdns:step-up-required'`) als
  `CustomEvent` mit `detail = { code, method, path, message }`. Der Dialog dazu liegt im Slot
  `src/components/dialogs/*.dialog.jsx` (F10-APP-FE: `10-stepup.dialog.jsx`). Der Fehler wird trotzdem geworfen
  (`err.code`), der Aufrufer entscheidet über die Wiederholung.
- Netzwerkfehler → `apiErrors.serverUnreachable`, 401 → `apiErrors.sessionExpired`; Fallback-Texte ohne
  Backend-Meldung kommen aus `apiErrors.*`.

## Benannte Exporte von `api.js`

`extractErrorMessage(payload, statusText, status, fallback?)`, `extractErrorCode(payload)`,
`PASSWORD_CHANGE_EVENT`, `STEP_UP_EVENT`, `STEP_UP_CODES`, `AUTH_REDIRECT_EXEMPT_PATHS`; Default-Export `api`.
