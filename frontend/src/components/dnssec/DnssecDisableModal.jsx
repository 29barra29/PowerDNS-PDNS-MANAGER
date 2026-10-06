import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, ShieldOff, X } from 'lucide-react'
import api from '../../api'
import { useDialogFocus } from '../../lib/useDialogFocus.js'
import ModalErrorBanner from '../ModalErrorBanner'
import { DnssecDialogFooter, SerialBumpOption } from './DnssecDialog'
import DnssecParentDsCheck from './DnssecParentDsCheck'
import {
    bumpSerialValue, currentSepTags, isPrimaryKind, visiblePeers, withForce,
} from '../../zoneDetail/dnssecModel.js'

// "DNSSEC deaktivieren" (F4 §2.9): Reihenfolge (erst DS beim Registrar entfernen, TTL abwarten), Pflicht-Checkbox.
// Teil B (WS-F4-C): Knopf "Elternzone pruefen" (GET …/parent-ds); ohne Freigabe der DNS-Pruefungen nur der manuelle
// Weg (dig). Das Backend sperrt mit 409 parent_ds_present, solange ein Resolver noch einen DS der Zone liefert –
// die Rueckfrage (force) laeuft ueber withForce. Teilfehler -> Fehler im Dialog + Status neu laden.
// Fokus/ESC/Tab-Falle ueber lib/useDialogFocus (eigener Rahmen statt DnssecDialog); Strg/Cmd+Enter sendet ab.
// Props: { server, zoneId, zoneName, status, onClose, onDone(res), onReload() }

function DisableDialogFrame({ title, onClose, onSubmit, busy, children }) {
    const { t } = useTranslation()
    const titleId = useId()
    const dialogRef = useDialogFocus({ onClose, canClose: !busy })
    function onKeyDown(e) {
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && onSubmit && !busy) {
            e.preventDefault()
            onSubmit()
        }
    }
    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
            onClick={() => { if (!busy) onClose?.() }}
        >
            <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                tabIndex={-1}
                className="glass-card p-5 sm:p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto shadow-2xl outline-none"
                onClick={(e) => e.stopPropagation()}
                onKeyDown={onKeyDown}
            >
                <div className="flex items-start justify-between gap-3 mb-4">
                    <h2 id={titleId} className="text-lg font-bold text-text-primary flex items-center gap-2 leading-snug break-words min-w-0">
                        <ShieldOff className="w-5 h-5 text-accent-light shrink-0" aria-hidden="true" />
                        <span className="min-w-0">{title}</span>
                    </h2>
                    <button
                        type="button"
                        onClick={() => { if (!busy) onClose?.() }}
                        disabled={busy}
                        className="p-1.5 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-primary shrink-0 disabled:opacity-50"
                        aria-label={t('common.close')}
                        title={t('common.close')}
                    >
                        <X className="w-5 h-5" />
                    </button>
                </div>
                {children}
            </div>
        </div>
    )
}
export default function DnssecDisableModal({ server, zoneId, zoneName, status, onClose, onDone, onReload }) {
    const { t } = useTranslation()
    const [confirmed, setConfirmed] = useState(false)
    const [bump, setBump] = useState(true)
    const [busy, setBusy] = useState(false)
    const [modalError, setModalError] = useState('')
    const tags = currentSepTags(status?.keys)
    const sepTags = tags.length ? tags : (status?.keys || []).filter((k) => k.role === 'sep' && k.key_tag != null).map((k) => k.key_tag)
    const divergent = visiblePeers(status?.peers).filter((p) => p.state === 'different_keys').map((p) => p.server)
    const primary = isPrimaryKind(status?.zone_kind)
    const zone = String(status?.zone || zoneId || '').replace(/\.$/, '') || zoneName

    async function submit() {
        if (busy || !confirmed) return
        setBusy(true)
        setModalError('')
        try {
            const bumpSerial = bumpSerialValue(status?.zone_kind, bump)
            const res = await withForce((force) => {
                const body = force ? { force: true } : {}
                if (bumpSerial !== undefined) body.bump_serial = bumpSerial
                return api.disableDNSSEC(server, zoneId, body)
            }, t)
            setBusy(false)
            if (res === null) return // Rueckfrage abgebrochen
            onDone(res)
        } catch (err) {
            setBusy(false)
            setModalError(err.message || String(err))
            onReload?.() // Teilfehler: Schluessel koennen schon geloescht sein
        }
    }

    return (
        <DisableDialogFrame title={t('dnssec.disableTitle', { zone: zoneName })} onClose={onClose}
            onSubmit={confirmed ? submit : undefined} busy={busy}>
            <ModalErrorBanner message={modalError} onClose={() => setModalError('')} />
            <ol className="list-decimal pl-5 space-y-2 text-sm text-text-secondary leading-relaxed">
                <li>{t('dnssec.disableStep1', { tags: sepTags.length ? sepTags.join(', ') : '—' })}</li>
                <li>{t('dnssec.disableStep2')}</li>
                <li>{t('dnssec.disableStep3')}</li>
            </ol>
            <p className="mt-3 text-sm text-text-secondary">{t('dnssec.disableWhat', { server })}</p>
            {divergent.length > 0 && (
                <p className="mt-2 text-sm text-warning">{t('dnssec.disablePeersNote', { servers: divergent.join(', ') })}</p>
            )}
            <div className="mt-4 p-3 rounded-lg border border-border bg-bg-secondary/40 space-y-2">
                <p className="text-xs font-medium text-text-secondary">{t('dnssec.parentDsTitle')}</p>
                <DnssecParentDsCheck server={server} zoneId={zoneId} zone={zone} mode="disable"
                    capability={!!status?.capabilities?.parent_ds_check} disabled={busy} />
            </div>
            {primary && (
                <div className="mt-4">
                    <SerialBumpOption visible checked={bump} onChange={setBump} disabled={busy} />
                </div>
            )}
            <label className="mt-4 flex items-start gap-2 text-sm text-text-primary cursor-pointer">
                <input type="checkbox" className="w-4 h-4 rounded mt-0.5" checked={confirmed} disabled={busy}
                    onChange={(e) => setConfirmed(e.target.checked)} />
                <span>{t('dnssec.disableConfirmCheckbox')}</span>
            </label>
            <DnssecDialogFooter onCancel={onClose} busy={busy}>
                <button
                    type="button"
                    onClick={submit}
                    disabled={busy || !confirmed}
                    className="px-4 py-2 rounded-lg text-sm font-medium border border-danger/50 bg-danger/20 text-danger hover:bg-danger/30 disabled:opacity-50 flex items-center gap-2"
                >
                    {busy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <ShieldOff className="w-4 h-4" aria-hidden="true" />}
                    {t('dnssec.disableSubmit')}
                </button>
            </DnssecDialogFooter>
        </DisableDialogFrame>
    )
}
