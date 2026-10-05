import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, Send } from 'lucide-react'
import api from '../../api'

// Kopf-Aktion "NOTIFY senden" (F2 §2.2): sichtbar fuer alle, aktiv nur mit Schreibrecht auf die Zone und fuer
// Zonen vom Typ Master/Slave/Producer. "Speichern: Nein" sperrt NOTIFY nicht (F3 E4). Keine Rueckfrage (E5).
// Fehlen die Zonen-Metadaten (Detail-Load fehlgeschlagen), bleibt der Button aktiv – das Backend meldet den Fehler.
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const action = { id: 'notify', order: 30 }

const NOTIFY_KINDS = ['master', 'slave', 'producer']

// eslint-disable-next-line react-refresh/only-export-components -- reine Hilfsfunktion (auch fuer Tests)
export function notifyState({ userCanEdit, zoneMeta }) {
    const kind = String(zoneMeta?.kind || '')
    const kindOk = !zoneMeta || NOTIFY_KINDS.includes(kind.toLowerCase())
    if (!userCanEdit) return { enabled: false, reason: 'permission', kind }
    if (!kindOk) return { enabled: false, reason: 'kind', kind }
    return { enabled: true, reason: null, kind }
}

export default function NotifyAction({ ctx }) {
    const { t } = useTranslation()
    const [notifying, setNotifying] = useState(false)
    const state = notifyState(ctx)

    let title = t('zoneDetail.notifyTitle', { server: ctx.server })
    if (state.reason === 'permission') title = t('zoneDetail.notifyNoPermission')
    if (state.reason === 'kind') title = t('zoneDetail.notifyUnsupportedKind', { kind: state.kind })

    async function handleNotify() {
        if (notifying || !state.enabled) return
        setNotifying(true)
        try {
            await api.notifyZone(ctx.server, ctx.zoneId)
            let msg = t('zoneDetail.notifySuccess', { zone: ctx.zoneName, server: ctx.server })
            const others = (ctx.otherWritableServers || []).map((s) => s.name)
            if (others.length > 0) msg = `${msg} ${t('zoneDetail.notifyOtherServersHint', { servers: others.join(', ') })}`
            ctx.setSuccess(msg)
        } catch (err) {
            if (err?.name === 'AbortError') return
            ctx.setError(err.message)
        } finally {
            setNotifying(false)
        }
    }

    // Titel am umschliessenden Element: deaktivierte Buttons zeigen in manchen Browsern keinen Tooltip
    return (
        <span title={title} className="self-start sm:self-auto order-2 flex">
            <button
                type="button"
                onClick={handleNotify}
                disabled={!state.enabled || notifying}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 border border-border bg-bg-secondary hover:bg-bg-hover text-text-primary rounded-lg font-medium text-sm transition-all disabled:opacity-40 disabled:cursor-not-allowed"
            >
                {notifying
                    ? <Loader2 className="w-4 h-4 shrink-0 animate-spin" aria-hidden="true" />
                    : <Send className="w-4 h-4 shrink-0" aria-hidden="true" />}
                {t('zoneDetail.notifyButton')}
            </button>
        </span>
    )
}
