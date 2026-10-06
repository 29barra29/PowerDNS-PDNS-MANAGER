import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Loader2, ShieldOff } from 'lucide-react'
import api from '../../api'
import ModalErrorBanner from '../ModalErrorBanner'
import DnssecDialog, { DnssecDialogFooter, SerialBumpOption } from './DnssecDialog'
import {
    bumpSerialValue, currentSepTags, isPrimaryKind, visiblePeers, withForce,
} from '../../zoneDetail/dnssecModel.js'

// "DNSSEC deaktivieren" (F4 §2.9): Reihenfolge (erst DS beim Registrar entfernen, TTL abwarten), Pflicht-Checkbox.
// Die Pruefung der Elternzone (Teil B) liefert WS-F4-C; bis dahin steht hier der manuelle Weg (dig).
// 409 parent_ds_present (ab F4-C) laeuft ueber withForce. Teilfehler -> Fehler im Dialog + Status neu laden.
// Props: { server, zoneId, zoneName, status, onClose, onDone(res), onReload() }
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
        <DnssecDialog title={t('dnssec.disableTitle', { zone: zoneName })} icon={ShieldOff} onClose={onClose}
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
            <div className="mt-4 p-3 rounded-lg border border-border bg-bg-secondary/40 text-xs text-text-muted">
                <p className="font-medium text-text-secondary mb-1">{t('dnssec.parentDsTitle')}</p>
                <p className="break-words">{t('dnssec.parentDsManual', { zone })}</p>
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
        </DnssecDialog>
    )
}
