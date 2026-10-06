import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { KeyRound, Loader2 } from 'lucide-react'
import api from '../../api'
import ModalErrorBanner from '../ModalErrorBanner'
import DnssecDialog, { DnssecDialogFooter, SerialBumpOption } from './DnssecDialog'
import DnssecOptionsFields from './DnssecOptionsFields'
import {
    NSEC3_MAX_ITERATIONS, buildNsecPayload, bumpSerialValue, formatNsec, isPrimaryKind, optionsFromStatus,
    validateDnssecOptions,
} from '../../zoneDetail/dnssecModel.js'

// "NSEC/NSEC3 aendern" (F4 §2.8): vorbelegt mit dem aktuellen Stand; Fehler (409 Algorithmus ohne NSEC3,
// Rectify-Fehler) im Dialog. Props: { server, zoneId, zoneName, status, onClose, onDone(res) }
export default function DnssecNsecModal({ server, zoneId, zoneName, status, onClose, onDone }) {
    const { t } = useTranslation()
    const [opts, setOpts] = useState(() => optionsFromStatus(status))
    const [bump, setBump] = useState(true)
    const [busy, setBusy] = useState(false)
    const [modalError, setModalError] = useState('')
    const primary = isPrimaryKind(status?.zone_kind)

    async function submit() {
        if (busy) return
        const errors = validateDnssecOptions(opts)
        const firstError = errors.nsec3_iterations || errors.nsec3_salt
        if (firstError) {
            setModalError(t(firstError, { max: NSEC3_MAX_ITERATIONS }))
            return
        }
        setBusy(true)
        setModalError('')
        try {
            const body = buildNsecPayload(opts)
            const bumpSerial = bumpSerialValue(status?.zone_kind, bump)
            if (bumpSerial !== undefined) body.bump_serial = bumpSerial
            const res = await api.updateNsec3(server, zoneId, body)
            setBusy(false)
            onDone(res)
        } catch (err) {
            setBusy(false)
            setModalError(err.message || String(err))
        }
    }

    return (
        <DnssecDialog title={t('dnssec.nsecTitle', { zone: zoneName })} icon={KeyRound} onClose={onClose} onSubmit={submit} busy={busy}>
            <ModalErrorBanner message={modalError} onClose={() => setModalError('')} />
            <p className="text-sm text-text-primary mb-1">{t('dnssec.nsecCurrent', { value: formatNsec(status?.nsec, t) })}</p>
            <p className="text-sm text-text-secondary mb-4 leading-relaxed">{t('dnssec.nsecIntro')}</p>
            <DnssecOptionsFields value={opts} onChange={setOpts} denialOnly disabled={busy} />
            {primary && (
                <div className="mt-4">
                    <SerialBumpOption visible checked={bump} onChange={setBump} disabled={busy} />
                </div>
            )}
            <DnssecDialogFooter onCancel={onClose} busy={busy}>
                <button
                    type="button"
                    onClick={submit}
                    disabled={busy}
                    className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                >
                    {busy && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                    {t('dnssec.nsecSubmit')}
                </button>
            </DnssecDialogFooter>
        </DnssecDialog>
    )
}
