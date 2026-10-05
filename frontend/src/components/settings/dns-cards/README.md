# DNS-Optionen-Karten (`components/settings/dns-cards/`)

Der Einstellungs-Tab **DNS-Optionen** (`tabs/dns.tab.jsx`, ID `dns`, nur Admins) rendert alle Dateien
`NN-<name>.card.jsx` in diesem Verzeichnis, sortiert nach `card.order`. Ohne Karte ist der Tab ausgeblendet.

## Vertrag

```jsx
import { useSettings } from '../settingsContext'

export const card = { id: 'ptr', order: 10 }   // id eindeutig, order = Position (NN aus dem Dateinamen)

export default function PtrCard() {
    const { notify, isAdmin } = useSettings()      // Kontext der Einstellungsseite, siehe settingsContext.js
    // Daten laden, eigene Fehler-/Erfolgsanzeige in der Karte (oder notify.error/notify.success fuer das Seiten-Banner)
    return <div className="glass-card p-6 space-y-4">…</div>
}
```

- Der Export `card` braucht wegen `react-refresh/only-export-components` die Zeile
  `// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)` davor.
- Der Tab bleibt nach dem ersten Oeffnen gemountet; wer beim erneuten Oeffnen neu laden will, liest
  `activeTab === 'dns'` aus `useSettings()`.
- Texte nur ueber i18n (Fragment des eigenen Workstreams), Admin-Endpunkte ueber `frontend/src/api/<ws>.js`.
- Der Vertragstest `frontend/tests/settings-slots.test.mjs` prueft Dateinamen, `card`-Export und Default-Export.

## Vorgesehene Karten

| Datei | Workstream | Inhalt |
|---|---|---|
| `10-ptr.card.jsx` | F9F11-FE | Reverse-DNS (PTR-Default) |
| `20-lua.card.jsx` | F15 | LUA-Records (Policy) |

Der Tab-Titel ist `settings.dnsTab` („DNS-Optionen“ / „DNS options“), angelegt von W0-INT-FE1b.
