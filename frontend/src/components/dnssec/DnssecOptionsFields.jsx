import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react'
import {
    DNSSEC_ALGORITHMS, NSEC3_MAX_ITERATIONS, NSEC3_WARN_ITERATIONS, RSA_BITS, RSA_BITS_DEFAULT,
    findAlgorithm, validateDnssecOptions,
} from '../../zoneDetail/dnssecModel.js'

// Kontrollierte DNSSEC-Optionen (F4 §2.2): Schluesselmodell, Algorithmus (+ Bits bei RSA), NSEC/NSEC3 mit
// einklappbaren NSEC3-Details. Genutzt im Aktivieren-Dialog, im NSEC-Dialog (`denialOnly`) und beim Zonenanlegen.
// Props: { value, onChange(next), algorithms?, denialOnly?, disabled? } – value im Format DEFAULT_DNSSEC_OPTIONS.
export default function DnssecOptionsFields({ value, onChange, algorithms = DNSSEC_ALGORITHMS, denialOnly = false, disabled = false }) {
    const { t } = useTranslation()
    const id = useId()
    const opts = value || {}
    const [advancedOpen, setAdvancedOpen] = useState(
        () => Number(opts.nsec3_iterations) > 0 || !!opts.nsec3_optout || !!opts.nsec3narrow
            || (opts.nsec3_salt !== '' && opts.nsec3_salt !== '-' && opts.nsec3_salt != null),
    )
    const errors = validateDnssecOptions(opts)
    const algo = findAlgorithm(opts.algorithm, algorithms)
    const rsa = !!algo?.rsa
    const iterations = Number(opts.nsec3_iterations)
    const set = (patch) => onChange({ ...opts, ...patch })

    function setAlgorithm(name) {
        const next = findAlgorithm(name, algorithms)
        set({ algorithm: name, bits: next?.rsa ? (RSA_BITS.includes(Number(opts.bits)) ? Number(opts.bits) : RSA_BITS_DEFAULT) : null })
    }

    const radio = 'w-4 h-4 mt-0.5 shrink-0'
    return (
        <div className="space-y-4 text-sm">
            {!denialOnly && (
                <fieldset className="space-y-2" disabled={disabled}>
                    <legend className="text-sm font-medium text-text-secondary mb-1">{t('dnssec.fieldKeyModel')}</legend>
                    <label className="flex items-start gap-2 cursor-pointer">
                        <input type="radio" name={`${id}-model`} className={radio} checked={opts.key_model !== 'ksk_zsk'}
                            onChange={() => set({ key_model: 'csk' })} />
                        <span>
                            <span className="text-text-primary">{t('dnssec.keyModelCsk')}</span>{' '}
                            <span className="text-xs px-1.5 py-0.5 rounded bg-success/15 text-success">{t('dnssec.recommended')}</span>
                            <span className="block text-xs text-text-muted mt-0.5">{t('dnssec.keyModelCskHint')}</span>
                        </span>
                    </label>
                    <label className="flex items-start gap-2 cursor-pointer">
                        <input type="radio" name={`${id}-model`} className={radio} checked={opts.key_model === 'ksk_zsk'}
                            onChange={() => set({ key_model: 'ksk_zsk' })} />
                        <span>
                            <span className="text-text-primary">{t('dnssec.keyModelKskZsk')}</span>
                            <span className="block text-xs text-text-muted mt-0.5">{t('dnssec.keyModelKskZskHint')}</span>
                        </span>
                    </label>
                </fieldset>
            )}

            {!denialOnly && (
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                    <div className={rsa ? 'sm:col-span-2' : 'sm:col-span-3'}>
                        <label htmlFor={`${id}-algo`} className="block text-sm font-medium text-text-secondary mb-1">
                            {t('dnssec.fieldAlgorithm')}
                        </label>
                        <select
                            id={`${id}-algo`}
                            value={algo?.name || opts.algorithm || ''}
                            onChange={(e) => setAlgorithm(e.target.value)}
                            disabled={disabled}
                            className="w-full px-3 py-2 text-sm"
                        >
                            {algorithms.map((a) => (
                                <option key={a.name} value={a.name}>
                                    {`${a.label} (${a.number})${a.recommended ? ` – ${t('dnssec.recommended')}` : ''}`}
                                </option>
                            ))}
                        </select>
                    </div>
                    {rsa && (
                        <div>
                            <label htmlFor={`${id}-bits`} className="block text-sm font-medium text-text-secondary mb-1">
                                {t('dnssec.fieldBits')}
                            </label>
                            <select
                                id={`${id}-bits`}
                                value={RSA_BITS.includes(Number(opts.bits)) ? Number(opts.bits) : RSA_BITS_DEFAULT}
                                onChange={(e) => set({ bits: Number(e.target.value) })}
                                disabled={disabled}
                                className="w-full px-3 py-2 text-sm"
                            >
                                {RSA_BITS.map((b) => <option key={b} value={b}>{b}</option>)}
                            </select>
                        </div>
                    )}
                    {algo?.buildDependent && (
                        <p className="sm:col-span-3 text-xs text-text-muted">{t('dnssec.algoHintEd')}</p>
                    )}
                    {rsa && <p className="sm:col-span-3 text-xs text-warning">{t('dnssec.algoHintRsa')}</p>}
                </div>
            )}

            <fieldset className="space-y-2" disabled={disabled}>
                <legend className="text-sm font-medium text-text-secondary mb-1">{t('dnssec.fieldDenial')}</legend>
                <label className="flex items-start gap-2 cursor-pointer">
                    <input type="radio" name={`${id}-denial`} className={radio} checked={opts.nsec_mode !== 'nsec'}
                        onChange={() => set({ nsec_mode: 'nsec3' })} />
                    <span>
                        <span className="text-text-primary">NSEC3</span>{' '}
                        <span className="text-xs px-1.5 py-0.5 rounded bg-success/15 text-success">{t('dnssec.recommended')}</span>
                        <span className="block text-xs text-text-muted mt-0.5">{t('dnssec.denialNsec3Hint')}</span>
                    </span>
                </label>
                <label className="flex items-start gap-2 cursor-pointer">
                    <input type="radio" name={`${id}-denial`} className={radio} checked={opts.nsec_mode === 'nsec'}
                        onChange={() => set({ nsec_mode: 'nsec' })} />
                    <span>
                        <span className="text-text-primary">NSEC</span>
                        <span className="block text-xs text-text-muted mt-0.5">{t('dnssec.denialNsecHint')}</span>
                    </span>
                </label>
            </fieldset>

            {opts.nsec_mode !== 'nsec' && (
                <div className="rounded-lg border border-border bg-bg-secondary/30">
                    <button
                        type="button"
                        onClick={() => setAdvancedOpen((v) => !v)}
                        aria-expanded={advancedOpen}
                        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-text-secondary hover:text-text-primary"
                    >
                        {advancedOpen ? <ChevronDown className="w-4 h-4" aria-hidden="true" /> : <ChevronRight className="w-4 h-4" aria-hidden="true" />}
                        {t('dnssec.advancedNsec3')}
                    </button>
                    {advancedOpen && (
                        <div className="px-3 pb-3 space-y-3">
                            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                                <div>
                                    <label htmlFor={`${id}-it`} className="block text-xs font-medium text-text-secondary mb-1">
                                        {t('dnssec.fieldIterations')}
                                    </label>
                                    <input
                                        id={`${id}-it`}
                                        type="number"
                                        min={0}
                                        max={NSEC3_MAX_ITERATIONS}
                                        step={1}
                                        inputMode="numeric"
                                        value={opts.nsec3_iterations ?? 0}
                                        onChange={(e) => set({ nsec3_iterations: e.target.value })}
                                        disabled={disabled}
                                        aria-invalid={!!errors.nsec3_iterations}
                                        className={`w-full px-3 py-2 text-sm ${errors.nsec3_iterations ? 'border-danger/60' : ''}`}
                                    />
                                    <p className="text-xs text-text-muted mt-1">{t('dnssec.iterationsHint', { max: NSEC3_MAX_ITERATIONS })}</p>
                                </div>
                                <div>
                                    <label htmlFor={`${id}-salt`} className="block text-xs font-medium text-text-secondary mb-1">
                                        {t('dnssec.fieldSalt')}
                                    </label>
                                    <input
                                        id={`${id}-salt`}
                                        type="text"
                                        value={opts.nsec3_salt ?? ''}
                                        onChange={(e) => set({ nsec3_salt: e.target.value.trim() })}
                                        disabled={disabled}
                                        autoComplete="off"
                                        spellCheck={false}
                                        placeholder="-"
                                        aria-invalid={!!errors.nsec3_salt}
                                        className={`w-full px-3 py-2 text-sm font-mono ${errors.nsec3_salt ? 'border-danger/60' : ''}`}
                                    />
                                    <p className="text-xs text-text-muted mt-1">{t('dnssec.saltHint')}</p>
                                </div>
                            </div>
                            {errors.nsec3_iterations && (
                                <p className="text-xs text-danger">{t(errors.nsec3_iterations, { max: NSEC3_MAX_ITERATIONS })}</p>
                            )}
                            {errors.nsec3_salt && <p className="text-xs text-danger">{t(errors.nsec3_salt)}</p>}
                            {!errors.nsec3_iterations && iterations > NSEC3_WARN_ITERATIONS && (
                                <p className="text-xs text-danger flex items-start gap-1.5">
                                    <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" aria-hidden="true" />
                                    {t('dnssec.iterationsDanger')}
                                </p>
                            )}
                            {!errors.nsec3_iterations && iterations > 0 && iterations <= NSEC3_WARN_ITERATIONS && (
                                <p className="text-xs text-warning flex items-start gap-1.5">
                                    <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" aria-hidden="true" />
                                    {t('dnssec.iterationsWarn')}
                                </p>
                            )}
                            <label className="flex items-start gap-2 cursor-pointer">
                                <input type="checkbox" className="w-4 h-4 rounded mt-0.5" checked={!!opts.nsec3_optout}
                                    disabled={disabled} onChange={(e) => set({ nsec3_optout: e.target.checked })} />
                                <span>
                                    <span className="text-text-primary">{t('dnssec.fieldOptOut')}</span>
                                    <span className="block text-xs text-text-muted mt-0.5">{t('dnssec.optOutHint')}</span>
                                </span>
                            </label>
                            <label className="flex items-start gap-2 cursor-pointer">
                                <input type="checkbox" className="w-4 h-4 rounded mt-0.5" checked={!!opts.nsec3narrow}
                                    disabled={disabled} onChange={(e) => set({ nsec3narrow: e.target.checked })} />
                                <span>
                                    <span className="text-text-primary">{t('dnssec.fieldNarrow')}</span>
                                    <span className="block text-xs text-text-muted mt-0.5">{t('dnssec.narrowHint')}</span>
                                </span>
                            </label>
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}
