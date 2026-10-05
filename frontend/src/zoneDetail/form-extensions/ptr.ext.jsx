import i18n from '../../i18n'
import PtrSyncOption from '../../components/PtrSyncOption'
import { getDefault, getManagePtr, isPtrType, setManagePtr } from '../../lib/ptrPreference.js'
import { applyPtrMessages } from '../../lib/ptrResults.js'

// Formular-Erweiterung "PTR mitpflegen" im Record-Dialog (F11 §2.5, Vertrag B.14 / form-extensions/README.md).
// Zustand: { manage: bool | null } - null = keine gemerkte Auswahl, dann gilt der Admin-Default aus
// GET /ptr/config (auch wenn er erst nach dem Oeffnen des Dialogs geladen wird).
// Senden: immer explizit manage_ptr true|false (nur bei A/AAAA, when()). Antwort: details.ptr -> Banner.

const effective = (state) => (typeof state?.manage === 'boolean' ? state.manage : getDefault())

// onResult laeuft ausserhalb des Renderns: Texte ueber die i18next-Instanz (aktuelle Sprache).
const ptrT = (key, values) => i18n.t(key, values)

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const ext = {
    id: 'ptr',
    when: (type) => isPtrType(type),
    position: 'afterValues',
    initialState: ({ zone }) => ({ manage: getManagePtr(zone) }),
    collect: (state) => ({ manage_ptr: effective(state) }),
    onResult: (res, { ctx }) => {
        applyPtrMessages(ptrT, res?.details, { setSuccess: ctx?.setSuccess, setWarning: ctx?.setWarning })
    },
}

export default function PtrExtension({ form, zone, server, isEdit, canEdit, extState, setExtState }) {
    if (!canEdit) return null
    const checked = effective(extState)
    return (
        <PtrSyncOption
            checked={checked}
            onChange={(v) => {
                setManagePtr(zone, v)
                setExtState((prev) => ({ ...(prev || {}), manage: !!v }))
            }}
            values={form?.contents || []}
            target={form?.fqdn || ''}
            server={server}
            isEdit={isEdit}
        />
    )
}
