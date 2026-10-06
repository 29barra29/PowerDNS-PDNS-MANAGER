import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Loader2, Shield } from 'lucide-react'
import api from '../../api'
import ModalErrorBanner from '../ModalErrorBanner'
import DnssecDialog, { DnssecDialogFooter, SerialBumpOption } from './DnssecDialog'
import DnssecOptionsFields from './DnssecOptionsFields'
import {
    DEFAULT_DNSSEC_OPTIONS, NSEC3_MAX_ITERATIONS, algorithmOptions, bumpSerialValue, buildDnssecPayload,
    isPrimaryKind, validateDnssecOptions, visiblePeers,
} from '../../zoneDetail/dnssecModel.js'

// "DNSSEC aktivieren" (F4 §2.2): Optionen, Hinweis bei weiteren Servern mit dieser Zone, Fehler im Dialog.
// Props: { server, zoneId, zoneName, status, onClose, onDone(res) }
export default function DnssecEnableModal({ server, zoneId, zoneName, status, onClose, onDone }) {
    const { t } = useTranslation()
    const [opts, setOpts] = useState(() => ({ ...DEFAULT_DNSSEC_OPTIONS }))
    const [bump, setBump] = useState(true)
    const [busy, setBusy] = useState(false)
    const [modalError, setModalError] = useState('')
    const algorithms = algorithmOptions(status?.capabilities)
    const peers = visiblePeers(status?.peers)
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
            const body = buildDnssecPayload(opts, algorithms)
            const bumpSerial = bumpSerialValue(status?.zone_kind, bump)
            if (bumpSerial !== undefined) body.bump_serial = bumpSerial
            const res = await api.enableDNSSEC(server, zoneId, body)
            setBusy(false)
            onDone(res)
        } catch (err) {
            setBusy(false)
            setModalError(err.message || String(err))
        }
    }

    return (
        <DnssecDialog title={t('dnssec.enableTitle', { zone: zoneName })} icon={Shield} onClose={onClose} onSubmit={submit} busy={busy}>
            <ModalErrorBanner message={modalError} onClose={() => setModalError('')} />
            <p className="text-sm text-text-secondary mb-4 leading-relaxed">{t('dnssec.enableIntro')}</p>

            {peers.length > 0 && (
                <div className="mb-4 p-3 rounded-lg border border-warning/30 bg-warning/10 text-warning text-sm" role="status">
                    <p className="font-medium flex items-center gap-1.5">
                        <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
                        {t('dnssec.peerWarnTitle')}
                    </p>
                    <p className="mt-1 text-xs leading-relaxed">
                        {t('dnssec.peerWarnBody', { servers: peers.map((p) => p.server).join(', '), server })}
                    </p>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                        {peers.map((p) => (
                            <span key={p.server} className="text-xs px-2 py-0.5 rounded-full border border-warning/40">
                                {p.server}{p.allow_writes === false ? ` · ${t('dnssec.peerReadOnly')}` : ''}
                            </span>
                        ))}
                    </div>
                </div>
            )}

            <DnssecOptionsFields value={opts} onChange={setOpts} algorithms={algorithms} disabled={busy} />

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
                    {busy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <Shield className="w-4 h-4" aria-hidden="true" />}
                    {t('dnssec.enableSubmit')}
                </button>
            </DnssecDialogFooter>
        </DnssecDialog>
    )
}
