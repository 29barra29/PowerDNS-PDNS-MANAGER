import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, KeyRound, Loader2 } from 'lucide-react'
import api from '../../api'
import ModalErrorBanner from '../ModalErrorBanner'
import DnssecDialog, { DnssecDialogFooter, SerialBumpOption } from './DnssecDialog'
import {
    MAX_KEYS_DEFAULT, RSA_BITS, RSA_BITS_DEFAULT, algorithmOptions, bumpSerialValue, findAlgorithm, isPrimaryKind,
} from '../../zoneDetail/dnssecModel.js'

// "Schluessel hinzufuegen" (F4 §2.7) fuer Sonderfaelle, z. B. Algorithmuswechsel. Waehlt der Nutzer einen
// Algorithmus, den kein aktiver Schluessel hat: Warnung addKeyAlgoWarn und Voreinstellung aktiv=an,
// veroeffentlicht=aus (nur wenn PowerDNS "published" kennt).
// Props: { server, zoneId, zoneName, status, onClose, onDone(res) }
function activeAlgorithmNumbers(keys) {
    return new Set((keys || []).filter((k) => k.active && k.algorithm_number != null).map((k) => k.algorithm_number))
}

export default function DnssecAddKeyModal({ server, zoneId, zoneName, status, onClose, onDone }) {
    const { t } = useTranslation()
    const algorithms = algorithmOptions(status?.capabilities)
    const keys = status?.keys || []
    const maxKeys = status?.capabilities?.max_keys ?? MAX_KEYS_DEFAULT
    const supportsPublished = status?.capabilities?.supports_published
    const activeAlgos = activeAlgorithmNumbers(keys)
    const initialAlgo = findAlgorithm(keys.find((k) => k.active)?.algorithm, algorithms)?.name || algorithms[0]?.name || 'ECDSAP256SHA256'
    const [form, setForm] = useState(() => ({
        keytype: keys.some((k) => k.role === 'zsk') ? 'zsk' : 'csk',
        algorithm: initialAlgo,
        bits: RSA_BITS_DEFAULT,
        active: false,
        published: true,
    }))
    const [bump, setBump] = useState(true)
    const [busy, setBusy] = useState(false)
    const [modalError, setModalError] = useState('')
    const algo = findAlgorithm(form.algorithm, algorithms)
    const newAlgorithm = !!algo && activeAlgos.size > 0 && !activeAlgos.has(algo.number)
    const limitReached = keys.length >= maxKeys
    const primary = isPrimaryKind(status?.zone_kind)

    function setAlgorithm(name) {
        const next = findAlgorithm(name, algorithms)
        const isNew = !!next && activeAlgos.size > 0 && !activeAlgos.has(next.number)
        setForm((prev) => ({
            ...prev,
            algorithm: name,
            // Algorithmuswechsel: aktiv + unveroeffentlicht anlegen (Doku-Anleitung); zurueck -> Standard
            active: isNew ? true : false,
            published: isNew && supportsPublished !== false ? false : true,
        }))
    }

    async function submit() {
        if (busy || limitReached) return
        setBusy(true)
        setModalError('')
        try {
            const body = { keytype: form.keytype, algorithm: form.algorithm, active: !!form.active }
            if (algo?.rsa) body.bits = RSA_BITS.includes(Number(form.bits)) ? Number(form.bits) : RSA_BITS_DEFAULT
            body.published = supportsPublished === false ? true : !!form.published
            const bumpSerial = bumpSerialValue(status?.zone_kind, bump)
            if (bumpSerial !== undefined) body.bump_serial = bumpSerial
            const res = await api.createDnssecKey(server, zoneId, body)
            setBusy(false)
            onDone(res)
        } catch (err) {
            setBusy(false)
            setModalError(err.message || String(err))
        }
    }

    const radio = 'w-4 h-4 mt-0.5 shrink-0'
    return (
        <DnssecDialog title={t('dnssec.addKeyTitle', { zone: zoneName })} icon={KeyRound} onClose={onClose} onSubmit={submit} busy={busy}>
            <ModalErrorBanner message={modalError} onClose={() => setModalError('')} />
            <p className="text-sm text-text-secondary mb-4 leading-relaxed">{t('dnssec.addKeyIntro')}</p>
            {limitReached && (
                <div className="mb-4 p-3 rounded-lg border border-warning/30 bg-warning/10 text-warning text-sm" role="status">
                    {t('dnssec.maxKeysReached', { max: maxKeys })}
                </div>
            )}
            <div className="space-y-4 text-sm">
                <fieldset className="space-y-2" disabled={busy}>
                    <legend className="text-sm font-medium text-text-secondary mb-1">{t('dnssec.fieldKeyType')}</legend>
                    {[['csk', 'dnssec.keyTypeCsk'], ['ksk', 'dnssec.keyTypeKsk'], ['zsk', 'dnssec.keyTypeZsk']].map(([value, key]) => (
                        <label key={value} className="flex items-start gap-2 cursor-pointer">
                            <input type="radio" name="dnssec-add-keytype" className={radio} checked={form.keytype === value}
                                onChange={() => setForm((prev) => ({ ...prev, keytype: value }))} />
                            <span className="text-text-primary">{t(key)}</span>
                        </label>
                    ))}
                </fieldset>
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <div className={algo?.rsa ? 'sm:col-span-2' : 'sm:col-span-3'}>
                        <label htmlFor="dnssec-add-algo" className="block text-sm font-medium text-text-secondary mb-1">{t('dnssec.fieldAlgorithm')}</label>
                        <select id="dnssec-add-algo" value={form.algorithm} disabled={busy}
                            onChange={(e) => setAlgorithm(e.target.value)} className="w-full px-3 py-2 text-sm">
                            {algorithms.map((a) => (
                                <option key={a.name} value={a.name}>
                                    {`${a.label} (${a.number})${a.recommended ? ` – ${t('dnssec.recommended')}` : ''}`}
                                </option>
                            ))}
                        </select>
                    </div>
                    {algo?.rsa && (
                        <div>
                            <label htmlFor="dnssec-add-bits" className="block text-sm font-medium text-text-secondary mb-1">{t('dnssec.fieldBits')}</label>
                            <select id="dnssec-add-bits" value={form.bits} disabled={busy}
                                onChange={(e) => setForm((prev) => ({ ...prev, bits: Number(e.target.value) }))} className="w-full px-3 py-2 text-sm">
                                {RSA_BITS.map((b) => <option key={b} value={b}>{b}</option>)}
                            </select>
                        </div>
                    )}
                    {algo?.buildDependent && <p className="sm:col-span-3 text-xs text-text-muted">{t('dnssec.algoHintEd')}</p>}
                </div>
                {newAlgorithm && (
                    <p className="p-3 rounded-lg border border-warning/30 bg-warning/10 text-warning text-xs flex items-start gap-1.5">
                        <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
                        {t('dnssec.addKeyAlgoWarn')}
                    </p>
                )}
                <label className="flex items-start gap-2 cursor-pointer">
                    <input type="checkbox" className="w-4 h-4 rounded mt-0.5" checked={!!form.active} disabled={busy}
                        onChange={(e) => setForm((prev) => ({ ...prev, active: e.target.checked }))} />
                    <span className="text-text-primary">{t('dnssec.fieldActive')}</span>
                </label>
                <label className="flex items-start gap-2 cursor-pointer" title={supportsPublished === false ? t('dnssec.publishNeeds43') : undefined}>
                    <input type="checkbox" className="w-4 h-4 rounded mt-0.5"
                        checked={supportsPublished === false ? true : !!form.published}
                        disabled={busy || supportsPublished === false}
                        onChange={(e) => setForm((prev) => ({ ...prev, published: e.target.checked }))} />
                    <span>
                        <span className="text-text-primary">{t('dnssec.fieldPublished')}</span>
                        {supportsPublished === false && (
                            <span className="block text-xs text-text-muted mt-0.5">{t('dnssec.publishNeeds43')}</span>
                        )}
                    </span>
                </label>
                {primary && <SerialBumpOption visible checked={bump} onChange={setBump} disabled={busy} />}
            </div>
            <DnssecDialogFooter onCancel={onClose} busy={busy}>
                <button
                    type="button"
                    onClick={submit}
                    disabled={busy || limitReached}
                    className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                >
                    {busy && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                    {t('dnssec.addKeySubmit')}
                </button>
            </DnssecDialogFooter>
        </DnssecDialog>
    )
}
