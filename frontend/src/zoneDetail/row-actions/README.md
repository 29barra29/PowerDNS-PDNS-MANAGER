# Zeilen-Aktionen der Record-Tabelle (`zoneDetail/row-actions/`)

Slot für zusätzliche Icons in der Aktionsspalte der Record-Tabelle (Plan B.14, Regel 5). `RecordsTable.jsx`
rendert sie **hinter** Stift / Klonen / Papierkorb, sortiert nach dem Präfix `NN`. Kein Workstream ändert dafür
`RecordsTable.jsx` oder die Shell.

## Datei

`row-actions/NN-<name>.action.jsx` (z. B. `10-history.action.jsx`, F7-FE):

```jsx
import { useTranslation } from 'react-i18next'
import { History } from 'lucide-react'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const action = {
    id: 'history',                                  // eindeutig, Default = Dateiname ohne NN- und Endung
    order: 10,                                      // optional, Default = NN
    when: (record, ctx) => record.type !== 'SOA',   // optional; fehlt es, gilt die Aktion für jede Zeile
}

export default function HistoryRowAction({ record, ctx }) {
    const { t } = useTranslation()
    return (
        <button
            type="button"
            onClick={() => ctx.setTab('history', { hname: record.name, htype: record.type }, { replace: false })}
            className="p-1 rounded text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors"
            title={t('zoneDetail.recordHistoryTitle')}
        >
            <History className="w-3.5 h-3.5" />
        </button>
    )
}
```

## Vertrag

| Teil | Bedeutung |
|---|---|
| `action.id` | eindeutig; doppelte ids werden ignoriert (Konsole) |
| `action.order` | Sortierung, Default aus `NN` |
| `action.when(record, ctx)` | optional; wirft die Funktion, wird die Aktion ausgeblendet |
| Default-Komponente | Props `{ record, ctx }`; rendert genau ein Icon/Button (Klassen wie die Bestands-Icons: `p-1 rounded …`, Icon `w-3.5 h-3.5`, `title=`) |

- `record` = Zeile aus `GET /records/...` (`{ name, type, ttl, content, disabled }`, `name` als FQDN mit Punkt).
- `ctx` = Context der Zonenansicht (Beschreibung in `zoneDetail/zoneDetailContext.js`), u. a. `canEdit`,
  `setTab(id, params?, { replace })`, `openEdit`, `handleDelete(record, extra?)`, `setError/setSuccess/setWarning`,
  `loadZone({ silent })`.
- Schreibende Aktionen respektieren `ctx.canEdit` (Button `disabled`), die Prüfung macht ohnehin das Backend.
- Die Aktionsspalte wird mit der Anzahl der Zeilen-Aktionen breiter (`w-28` → `w-36` → `w-44`).
- Belegte Nummern: `10-history` (F7-FE).
