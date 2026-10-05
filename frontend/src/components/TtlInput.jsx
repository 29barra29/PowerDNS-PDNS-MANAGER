import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { TTL_MAX, TTL_MIN, TTL_PRESETS, formatTtl, isValidTtl, parseTtl } from '../lib/ttl'

// TTL-Eingabe: Zahlenfeld + Vorgaben-Auswahl + lesbare Anzeige bzw. Fehler (F8 §6.3.5).
// `value` ist ein String (oder eine Zahl); `onChange` bekommt immer den String des Felds.
export default function TtlInput({ id, value, onChange, disabled = false, min = TTL_MIN, max = TTL_MAX }) {
    const { t } = useTranslation()
    const autoId = useId()
    const inputId = id || `ttl-${autoId}`
    const hintId = `${inputId}-hint`
    const valid = isValidTtl(value, min, max)
    const presets = TTL_PRESETS.filter((s) => s >= min && s <= max)

    return (
        <div className="min-w-0">
            <div className="grid grid-cols-[1fr_auto] gap-2">
                <input
                    id={inputId}
                    type="number"
                    inputMode="numeric"
                    min={min}
                    max={max}
                    step={1}
                    value={value ?? ''}
                    disabled={disabled}
                    onChange={(e) => onChange(e.target.value)}
                    aria-invalid={!valid}
                    aria-describedby={hintId}
                    className="w-full h-10 px-3 text-sm rounded-lg border border-border bg-bg-primary text-text-primary disabled:opacity-50"
                />
                <select
                    value=""
                    disabled={disabled}
                    onChange={(e) => { if (e.target.value) onChange(e.target.value) }}
                    aria-label={t('ttlInput.presetPlaceholder')}
                    className="h-10 px-2 text-sm rounded-lg border border-border bg-bg-primary text-text-primary disabled:opacity-50"
                >
                    <option value="">{t('ttlInput.presetPlaceholder')}</option>
                    {presets.map((s) => (
                        <option key={s} value={String(s)}>{formatTtl(s, t)}</option>
                    ))}
                </select>
            </div>
            {valid ? (
                <p id={hintId} className="mt-1 text-xs text-text-muted">= {formatTtl(parseTtl(value), t)}</p>
            ) : (
                <p id={hintId} className="mt-1 text-xs text-danger">{t('ttlInput.outOfRange', { min, max })}</p>
            )}
        </div>
    )
}
