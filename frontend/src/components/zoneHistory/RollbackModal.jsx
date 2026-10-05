import { useCallback, useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Info, Loader2, RotateCcw, X } from 'lucide-react'
import api from '../../api'
import ModalErrorBanner from '../ModalErrorBanner'
import RrsetChangeDiff from '../audit/RrsetChangeDiff'
import {
    canConfirmRollback, conflictCount, displayName, isRollbackConflict, planItemAsChange, rollbackTargets,
} from '../audit/historyModel.js'

// Rollback-Dialog (F7 §2.4, §6.1). Laedt die Vorschau (GET .../rollback-preview), zeigt Plan, Konflikte,
// Zielserver und Ausnahmen; bei Konflikten Pflicht-Checkbox -> force. 409 beim Bestaetigen laedt die Vorschau
// neu (zwischenzeitlich geaendert), andere Fehler bleiben im Modal.
// Props: { server, zoneId, zoneKey, entry, onClose, onDone(res), t? }
export default function RollbackModal({ server, zoneId, zoneKey, entry, onClose, onDone, t: tProp }) {
    const { t: tHook } = useTranslation()
    const t = tProp || tHook
    const titleId = useId()
    const [preview, setPreview] = useState(null)
    const [loading, setLoading] = useState(true)
    const [loadError, setLoadError] = useState('')
    const [submitError, setSubmitError] = useState('')
    const [conflictReloaded, setConflictReloaded] = useState(false)
    const [force, setForce] = useState(false)
    const [busy, setBusy] = useState(false)
    const seq = useRef(0)

    const loadPreview = useCallback(async () => {
        const my = ++seq.current
        setLoading(true)
        setLoadError('')
        try {
            const p = await api.getRollbackPreview(server, zoneId, entry.id)
            if (my !== seq.current) return
            setPreview(p)
            setForce(false)
        } catch (err) {
            if (my !== seq.current) return
            setPreview(null)
            setLoadError(err.message || String(err))
        } finally {
            if (my === seq.current) setLoading(false)
        }
    }, [server, zoneId, entry.id])

    useEffect(() => {
        const counter = seq
        // eslint-disable-next-line react-hooks/set-state-in-effect -- Vorschau beim Oeffnen laden (F7 §2.4 Nr. 1)
        loadPreview()
        return () => { counter.current++ }
    }, [loadPreview])

    useEffect(() => {
        function onKey(e) {
            if (e.key === 'Escape' && !busy) onClose()
        }
        window.addEventListener('keydown', onKey)
        return () => window.removeEventListener('keydown', onKey)
    }, [busy, onClose])

    async function handleConfirm() {
        if (!canConfirmRollback(preview, { force, busy })) return
        setBusy(true)
        setSubmitError('')
        setConflictReloaded(false)
        try {
            const res = await api.rollbackZoneChange(server, zoneId, entry.id, { force: Boolean(preview.has_conflicts && force) })
            setBusy(false)
            onDone(res)
        } catch (err) {
            setBusy(false)
            if (isRollbackConflict(err)) {
                // zwischen Vorschau und Klick geaendert: Vorschau neu laden, Checkbox zuruecksetzen
                setConflictReloaded(true)
                await loadPreview()
                return
            }
            setSubmitError(err.message || String(err))
        }
    }

    const conflicts = conflictCount(preview)
    const targets = rollbackTargets(preview)
    const plan = Array.isArray(preview?.plan) ? preview.plan : []
    const skipped = Array.isArray(preview?.skipped) ? preview.skipped : []
    const confirmEnabled = canConfirmRollback(preview, { force, busy })
    const blocked = preview && !preview.rollbackable
    const zone = zoneKey || preview?.zone || ''

    return (
        <div
            className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
            onClick={() => { if (!busy) onClose() }}
        >
            <div
                role="dialog"
                aria-modal="true"
                aria-labelledby={titleId}
                className="glass-card p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-center justify-between mb-5 gap-3">
                    <h2 id={titleId} className="text-lg font-bold text-text-primary flex items-center gap-2">
                        <RotateCcw className="w-5 h-5 text-accent-light" aria-hidden="true" />
                        {t('history.rollbackTitle', { id: entry.id })}
                    </h2>
                    <button
                        type="button"
                        onClick={onClose}
                        disabled={busy}
                        className="p-1 rounded-lg hover:bg-bg-hover text-text-muted disabled:opacity-50"
                        aria-label={t('common.close')}
                    >
                        <X className="w-5 h-5" />
                    </button>
                </div>

                <ModalErrorBanner message={submitError} onClose={() => setSubmitError('')} />

                {loading && (
                    <div className="flex items-center gap-3 py-8 justify-center text-text-muted text-sm" role="status">
                        <Loader2 className="w-5 h-5 animate-spin text-accent" aria-hidden="true" />
                        {t('history.rollbackLoading')}
                    </div>
                )}

                {!loading && loadError && (
                    <>
                        <ModalErrorBanner message={loadError} />
                        <div className="flex justify-end gap-2">
                            <button type="button" onClick={loadPreview} className="px-4 py-2 rounded-lg text-sm border border-border text-text-secondary hover:bg-bg-hover">
                                {t('common.retry')}
                            </button>
                            <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg text-sm border border-border text-text-secondary hover:bg-bg-hover">
                                {t('common.close')}
                            </button>
                        </div>
                    </>
                )}

                {!loading && !loadError && blocked && (
                    <>
                        <div className="p-4 rounded-xl bg-bg-secondary/60 border border-border text-text-secondary text-sm flex items-start gap-3">
                            <Info className="w-5 h-5 shrink-0 mt-0.5" aria-hidden="true" />
                            <p>{t(`history.blocked.${preview.blocked_reason}`, { defaultValue: preview.blocked_message || preview.blocked_reason || '' })}</p>
                        </div>
                        <div className="flex justify-end mt-4">
                            <button type="button" onClick={onClose} className="px-4 py-2 rounded-lg text-sm border border-border text-text-secondary hover:bg-bg-hover">
                                {t('common.close')}
                            </button>
                        </div>
                    </>
                )}

                {!loading && !loadError && preview && !blocked && (
                    <div className="space-y-4">
                        {conflictReloaded && (
                            <div role="status" className="p-3 rounded-lg bg-warning/10 border border-warning/30 text-warning text-sm">
                                {t('history.rollbackConflictReloaded')}
                            </div>
                        )}

                        <p className="text-sm text-text-secondary">{t('history.rollbackIntro', { server })}</p>

                        {preview.source_server && preview.source_server !== server && (
                            <p className="text-xs text-text-muted">
                                {t('history.rollbackServerHint', { source: preview.source_server, server })}
                            </p>
                        )}

                        {preview.already_reverted_by != null && (
                            <div className="p-3 rounded-lg bg-accent/10 border border-accent/30 text-accent-light text-sm flex items-start gap-2">
                                <Info className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                                {t('history.rollbackAlreadyReverted', { id: preview.already_reverted_by })}
                            </div>
                        )}

                        {targets.length > 0 && (
                            <div>
                                <div className="text-xs font-medium text-text-muted mb-1">{t('history.rollbackTargets')}</div>
                                <ul className="flex flex-wrap gap-1.5 text-xs">
                                    {targets.map((tg) => (
                                        <li
                                            key={tg.server}
                                            className={`px-2 py-0.5 rounded-full border ${tg.write ? 'border-success/30 bg-success/10 text-success' : 'border-border text-text-muted'}`}
                                        >
                                            {tg.server}: {tg.write ? t('history.targetWrite') : tg.status}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        {preview.has_conflicts && (
                            <div className="p-4 rounded-xl bg-warning/10 border border-warning/30 text-warning space-y-3">
                                <div className="flex items-start gap-2">
                                    <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" aria-hidden="true" />
                                    <div className="text-sm">
                                        <p className="font-medium">{t('history.rollbackConflictTitle')}</p>
                                        <p className="mt-1">{t('history.rollbackConflictBody', { count: conflicts })}</p>
                                    </div>
                                </div>
                                <label className="flex items-start gap-2 text-sm cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={force}
                                        onChange={(e) => setForce(e.target.checked)}
                                        disabled={busy}
                                        className="w-4 h-4 mt-0.5 rounded"
                                    />
                                    <span>{t('history.rollbackForce')}</span>
                                </label>
                            </div>
                        )}

                        <div className="space-y-2">
                            {plan.map((item) => (
                                <RrsetChangeDiff
                                    key={`${item.name}|${item.type}`}
                                    change={planItemAsChange(item)}
                                    zoneKey={zone}
                                    t={t}
                                    labels={{ before: t('history.rollbackCurrent'), after: t('history.rollbackTarget') }}
                                    badge={(
                                        <>
                                            {item.conflict && (
                                                <span className="px-1.5 py-0.5 rounded border border-warning/40 bg-warning/10 text-warning">
                                                    {t('history.rollbackConflictBadge')}
                                                </span>
                                            )}
                                            {item.noop && (
                                                <span className="px-1.5 py-0.5 rounded border border-border bg-bg-hover text-text-muted">
                                                    {t('history.rollbackNoop')}
                                                </span>
                                            )}
                                        </>
                                    )}
                                />
                            ))}
                        </div>

                        {skipped.length > 0 && (
                            <div>
                                <div className="text-xs font-medium text-text-muted mb-1">{t('history.rollbackSkippedTitle')}</div>
                                <ul className="text-xs space-y-0.5">
                                    {skipped.map((s) => (
                                        <li key={`${s.name}|${s.type}`}>
                                            <span className="font-mono">{displayName(s.name, zone)} {s.type}</span>
                                            <span className="text-text-muted"> – {t(`history.skipReason.${s.reason}`, { defaultValue: s.reason })}</span>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}

                        <div className="flex justify-end gap-2 pt-2">
                            <button
                                type="button"
                                onClick={onClose}
                                disabled={busy}
                                className="px-4 py-2 rounded-lg text-sm border border-border text-text-secondary hover:bg-bg-hover disabled:opacity-50"
                            >
                                {t('common.cancel')}
                            </button>
                            <button
                                type="button"
                                onClick={handleConfirm}
                                disabled={!confirmEnabled}
                                className="px-5 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                            >
                                {busy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <RotateCcw className="w-4 h-4" aria-hidden="true" />}
                                {t('history.rollbackConfirm')}
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}
