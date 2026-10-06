import { useEffect, useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircle, Loader2, X } from 'lucide-react'
import api from '../../api'
import ModalErrorBanner from '../ModalErrorBanner'
import { useDateFormat } from '../../lib/useDateFormat'
import { useDialogFocus } from '../../lib/useDialogFocus'
import { isRetentionShortening, RETENTION_MAX_DAYS, validateRetention } from './historyModel.js'
import ModalPortal from '../common/ModalPortal'

// Aufbewahrung des Audit-Logs (F7 §2.5 Nr. 8, §3.9/§3.10; nur Admin-Session).
// Eigener Fehlerzustand im Modal; Erfolg erscheint im Modal, geschlossen wird ueber "Schliessen".
// Props: { onClose, onSaved?(settings) }
export default function AuditRetentionModal({ onClose, onSaved }) {
    const { t } = useTranslation()
    const { fmtDateTime } = useDateFormat()
    const titleId = useId()
    const inputId = useId()
    const [settings, setSettings] = useState(null)
    const [loading, setLoading] = useState(true)
    const [value, setValue] = useState('')
    const [saving, setSaving] = useState(false)
    const [modalError, setModalError] = useState('')
    const [saved, setSaved] = useState(false)

    useEffect(() => {
        const ctrl = new AbortController()
        api.getAuditSettings({ signal: ctrl.signal })
            .then((s) => {
                setSettings(s)
                setValue(String(s?.retention_days ?? 0))
            })
            .catch((err) => {
                if (err?.name !== 'AbortError') setModalError(err.message || String(err))
            })
            .finally(() => { if (!ctrl.signal.aborted) setLoading(false) })
        return () => ctrl.abort()
    }, [])

    // Fokus beim Oeffnen, Tab-Falle, ESC (nicht waehrend des Speicherns), Fokus-Rueckgabe (L11)
    const dialogRef = useDialogFocus({ onClose, canClose: !saving })

    const check = validateRetention(value)
    const current = settings?.retention_days ?? 0
    const unchanged = check.ok && check.days === current

    async function handleSave(e) {
        e.preventDefault()
        if (!check.ok || saving) return
        if (isRetentionShortening(current, check.days) && !window.confirm(t('audit.retentionConfirm', { days: check.days }))) {
            return
        }
        setSaving(true)
        setModalError('')
        setSaved(false)
        try {
            const res = await api.updateAuditSettings({ retention_days: check.days })
            const next = res?.settings || { ...settings, retention_days: check.days }
            setSettings(next)
            setValue(String(next.retention_days ?? check.days))
            setSaved(true)
            onSaved?.(next)
        } catch (err) {
            setModalError(err.message || String(err))
        } finally {
            setSaving(false)
        }
    }

    const valueText = (days) => (days > 0 ? t('audit.retentionDaysValue', { count: days }) : t('audit.retentionUnlimited'))

    return (
        <ModalPortal>
            <div
                className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
                onClick={() => { if (!saving) onClose() }}
            >
                <div
                    ref={dialogRef}
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby={titleId}
                    className="glass-card p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto"
                    onClick={(e) => e.stopPropagation()}
                >
                    <div className="flex items-center justify-between mb-5">
                        <h2 id={titleId} className="text-lg font-bold text-text-primary">{t('audit.retentionTitle')}</h2>
                        <button
                            type="button"
                            onClick={onClose}
                            disabled={saving}
                            className="p-1 rounded-lg hover:bg-bg-hover text-text-muted disabled:opacity-50"
                            aria-label={t('common.close')}
                        >
                            <X className="w-5 h-5" />
                        </button>
                    </div>

                    <ModalErrorBanner message={modalError} onClose={() => setModalError('')} />

                    {saved && (
                        <div role="status" className="mb-4 p-3 rounded-xl bg-success/10 border border-success/30 text-success text-sm flex items-center gap-2">
                            <CheckCircle className="w-4 h-4 shrink-0" aria-hidden="true" /> {t('audit.retentionSaved')}
                        </div>
                    )}

                    {loading ? (
                        <div className="flex items-center justify-center py-10">
                            <Loader2 className="w-6 h-6 text-accent animate-spin" aria-label={t('common.loading')} />
                        </div>
                    ) : (
                        <form onSubmit={handleSave} className="space-y-4">
                            {settings && (
                                <div className="text-sm text-text-secondary space-y-1">
                                    <p>{t('audit.retentionCurrent', { value: valueText(current) })}</p>
                                    <p>
                                        {settings.oldest_entry_at
                                            ? t('audit.retentionStats', { total: settings.total_entries ?? 0, date: fmtDateTime(settings.oldest_entry_at) })
                                            : t('audit.retentionStatsEmpty', { total: settings.total_entries ?? 0 })}
                                    </p>
                                    <p>
                                        {settings.last_purge_at
                                            ? t('audit.retentionLastPurge', { date: fmtDateTime(settings.last_purge_at), deleted: settings.last_purge_deleted ?? 0 })
                                            : t('audit.retentionNeverPurged')}
                                    </p>
                                </div>
                            )}

                            {settings && settings.worker_enabled === false && (
                                <div className="p-3 rounded-lg bg-warning/10 border border-warning/30 text-warning text-xs">
                                    {t('audit.retentionWorkerOff')}
                                </div>
                            )}

                            <div>
                                <label htmlFor={inputId} className="block text-sm font-medium text-text-secondary mb-1">
                                    {t('audit.retentionDays')}
                                </label>
                                <input
                                    id={inputId}
                                    type="number"
                                    min={0}
                                    max={RETENTION_MAX_DAYS}
                                    step={1}
                                    inputMode="numeric"
                                    autoFocus
                                    value={value}
                                    onChange={(e) => { setValue(e.target.value); setSaved(false) }}
                                    aria-invalid={!check.ok}
                                    className="w-full px-3 py-2 text-sm"
                                />
                                {!check.ok && (
                                    <p className="mt-1 text-xs text-danger">{t('audit.retentionInvalid')}</p>
                                )}
                                <p className="mt-2 text-xs text-text-muted">{t('audit.retentionHelp')}</p>
                            </div>

                            <div className="flex justify-end gap-2 pt-2">
                                <button
                                    type="button"
                                    onClick={onClose}
                                    disabled={saving}
                                    className="px-4 py-2 rounded-lg text-sm border border-border text-text-secondary hover:bg-bg-hover disabled:opacity-50"
                                >
                                    {t('common.close')}
                                </button>
                                <button
                                    type="submit"
                                    disabled={!check.ok || saving || unchanged || !settings}
                                    className="px-5 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                                >
                                    {saving && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
                                    {t('common.save')}
                                </button>
                            </div>
                        </form>
                    )}
                </div>
            </div>
        </ModalPortal>
    )
}
