# Erweiterungen des Record-Dialogs (`zoneDetail/form-extensions/`)

Slot für zusätzliche Felder und zusätzliche Request-Daten im Dialog „Record hinzufügen / bearbeiten“
(`RecordFormModal.jsx`, Plan B.14 [F5]). Nach Welle 0b ändert **kein** Workstream `RecordFormModal.jsx` für
Erweiterungen – neue Funktionen kommen als Datei `form-extensions/<name>.ext.jsx` (z. B. `ptr.ext.jsx` F9F11-FE,
`lua.ext.jsx` F15). Die Ablauflogik steht in `zoneDetail/formExtensions.js` und ist durch
`frontend/tests/zoneDetailSlots.test.mjs` abgesichert.

## Vertrag

```js
export const ext = {
    id,                                   // eindeutig (Default: Dateiname ohne Endung)
    when(type) -> boolean,                // Pflicht: für welchen Record-Typ die Erweiterung aktiv ist
    position: 'afterValues' | 'beforeTtl' | 'footer',   // Default 'afterValues'
    initialState({ record, mode, type, zone }) -> state,  // Pflicht
    collect(state, form) -> partialBody | null,           // Pflicht
    validate?(state, form) -> errorKey | { key, values } | null,
    onResult?(res, { state, form, ctx }) -> void,
}
export default function MyExtension({ type, form, setForm, record, zone, server, isEdit, canEdit, extState, setExtState }) { … }
```

### Ablauf

1. **Öffnen** (Anlegen, Bearbeiten, Klonen) und **jede Schnellvorlage**: `initialState({ record, mode, type, zone })`
   für **alle** Erweiterungen (auch inaktive – so findet ein späterer Typwechsel einen Zustand vor).
   `mode` ist `'add' | 'edit' | 'clone' | 'template'`, `record` der Ausgangs-Record (bei `add`/`template` `null`),
   `zone = { id, name, key, meta }` (`meta` = Zonen-Metadaten, kann `null` sein).
2. **Rendern**: aktive Erweiterungen (`when(type)` wahr) mit Default-Komponente erscheinen an ihrer `position`:
   - `afterValues` – unter den Wertfeldern und „+ Weiterer Wert“, vor dem Typ-Hinweis (Platz für die PTR-Option),
   - `beforeTtl` – in der dritten Spalte oberhalb der TTL-Auswahl,
   - `footer` – über der Fußzeile mit Abbrechen/Speichern.
3. **Absenden**: zuerst die Prüfungen des Dialogs (Apex, Pflichtfelder, Validatoren, Duplikate), dann `validate`
   aller aktiven Erweiterungen – der erste Fehler bricht ab und erscheint als Modal-Fehler (`errorKey` wird mit
   `t(key, values)` übersetzt). Danach `collect` aller aktiven Erweiterungen; die Teil-Bodies werden in den
   Request-Body von `POST`/`PUT /records/...` gemergt. Die Kernfelder (`name`, `type`, `ttl`, `records`,
   `old_content`, `new_content`, `disabled`) setzt nur der Dialog – eine Erweiterung kann sie nicht überschreiben.
   Wirft `collect`, erscheint die Meldung als Modal-Fehler und nichts wird gesendet.
4. **Antwort** (nur bei Erfolg): Dialog schließt, Fan-out-Auswertung (rot/gelb) und Erfolgsmeldung sind gesetzt,
   dann `onResult(res, { state, form, ctx })` für alle aktiven Erweiterungen, danach `ctx.loadZone()`.
   Zum Ergänzen der Banner die Updater-Form nutzen, z. B. `ctx.setSuccess(prev => prev + ' ' + text)` oder
   `ctx.setWarning(prev => prev ? prev + '\n' + w : w)`. Ein Fehler in `onResult` stoppt die anderen nicht.

### Props der Komponente

| Prop | Inhalt |
|---|---|
| `type` | aktueller Record-Typ |
| `form` | `{ mode, isEdit, type, name, fqdn, ttl, fieldsList, contents, oldContent }` – `contents` = gebaute Werte je Wert-Set (unvollständige Sets `''`) |
| `setForm(patch \| form => patch)` | ändert `type`, `name`, `ttl` oder `fieldsList` (andere Felder werden ignoriert) |
| `record` | Ausgangs-Record (`null` bei Anlegen/Vorlage) |
| `zone`, `server` | `{ id, name, key, meta }` bzw. Servername aus der URL |
| `isEdit`, `canEdit` | Bearbeiten-Modus; Schreibrecht (Zone + Server) |
| `extState`, `setExtState(value \| prev => value)` | eigener Zustand dieser Erweiterung |

`form` bei `validate`/`collect`/`onResult` ist dieselbe Struktur; beim Absenden sind `fqdn` und `contents` die
tatsächlich gesendeten Werte.

## Beispiel

Die Testdatei `frontend/tests/zoneDetailSlots.test.mjs` prüft genau diesen Ablauf (ohne JSX).

```jsx
// form-extensions/note.ext.jsx – fügt bei TXT-Records ein Feld "note" in den Request ein
import { useTranslation } from 'react-i18next'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const ext = {
    id: 'note',
    when: (type) => type === 'TXT',
    position: 'afterValues',
    initialState: ({ mode }) => ({ enabled: mode !== 'edit', note: '' }),
    validate: (state) => (state.enabled && state.note.length > 200 ? 'example.noteTooLong' : null),
    collect: (state) => (state.enabled ? { note: state.note.trim() } : null),
    onResult: (res, { ctx }) => {
        if (res?.details?.note === 'stored') ctx.setSuccess((prev) => `${prev} (Notiz gespeichert)`)
    },
}

export default function NoteExtension({ extState, setExtState, canEdit }) {
    const { t } = useTranslation()
    if (!extState) return null
    return (
        <label className="flex items-center gap-2 text-xs text-text-secondary">
            <input
                type="checkbox"
                checked={extState.enabled}
                disabled={!canEdit}
                onChange={(e) => setExtState((s) => ({ ...s, enabled: e.target.checked }))}
            />
            {t('example.noteLabel')}
        </label>
    )
}
```

## Hinweise

- `when`, `initialState` und `collect` sind Pflicht; fehlen sie, wird die Erweiterung mit Konsolenmeldung ignoriert.
  Eine Default-Komponente ist optional (Erweiterung nur mit `collect`/`onResult`).
- Löschen läuft nicht über den Dialog: Zusatzfelder für `DELETE` (z. B. `manage_ptr`) gibt der Aufrufer an
  `ctx.handleDelete(record, extra)` mit (`useZoneData.js`).
- Texte über i18n im eigenen Fragment (`locales/fragments/<ws>.<lang>.json`).
