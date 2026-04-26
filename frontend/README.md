# DNS Manager – Frontend

Single-Page-Web-UI des [DNS Managers](../README.md). React 19 + Vite 8 (Rolldown) + Tailwind CSS 4 + i18next. Spricht ausschließlich die Backend-API unter `/api/v1` an und wird im Backend-Image als Static ausgeliefert (`backend/Dockerfile` kopiert das Build-Result in `/app/static/`).

Im Produktivbetrieb baut das Backend-Dockerfile dieses Frontend automatisch mit – du musst hier nichts manuell ausführen, außer du arbeitest am Frontend.

## Lokale Entwicklung

Voraussetzung: Node 22+ und ein laufendes Backend (Default: `http://localhost:5380`).

```bash
cd frontend
npm install
npm run dev
```

Vite startet auf `http://localhost:5173` mit HMR. API-Aufrufe gehen per Proxy ans Backend (siehe `vite.config.js`).

Weitere Skripte:

```bash
npm run build      # Production-Bundle nach dist/ – wird vom Backend-Image aufgegriffen
npm run preview    # gebautes Bundle lokal anschauen
npm run lint       # ESLint (CI bricht hart bei Fehlern ab)
```

## Aufbau

```
frontend/
├── src/
│   ├── api.js              # zentraler fetch-Wrapper (Cookie + Bearer)
│   ├── App.jsx             # Routing, Auth-Guards, AppErrorBoundary
│   ├── i18n.js             # Sprachen + Fallback auf Englisch
│   ├── locales/            # en/de/sr/hr/bs/hu – identische Key-Liste
│   ├── pages/              # Top-Level-Routen (Dashboard, Zonen, Settings …)
│   ├── components/         # wiederverwendbare UI-Bausteine
│   └── zoneDetail/         # Models / Validierung für die Record-Seite
├── public/                 # Static Assets (Logo-Fallback, vite.svg)
├── index.html
├── vite.config.js
├── tailwind.config.js
└── package.json
```

## Übersetzungen

`src/locales/en.json` ist die Source of Truth. Wenn du neue UI-Strings einbaust:

```bash
node ../scripts/sync-locales.mjs
```

Das Skript ergänzt fehlende Keys in den anderen Sprachen mit dem englischen Wert und entfernt verwaiste Keys. Englisch ist `fallbackLng`, fehlende Übersetzungen in einer Sprache werden zur Laufzeit auf Englisch angezeigt – der UI bricht nichts ab, nur die Übersetzung fehlt halt.

## Backend-API

Die Frontend-Komponenten gehen ausschließlich über `src/api.js`. Das hängt automatisch das Auth-Cookie an, normalisiert Fehler (`error.status`, `error.payload`) und kümmert sich um JSON-Encoding. Bitte keine direkten `fetch`-Aufrufe in Komponenten – sonst gehen z. B. die zentrale Fehleranzeige und der `AppErrorBoundary` an dem Aufruf vorbei.

## Browser-Support

Aktuelle Versionen von Chrome, Firefox, Edge, Safari (Desktop und Mobile). Kein Support für IE.
