import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircle2, KeyRound, Loader2, ShieldOff, X } from 'lucide-react'
import api from '../../api'
import ModalErrorBanner from '../ModalErrorBanner'
import OneTimeSecretModal from '../OneTimeSecretModal'
import { useDialogFocus } from '../../lib/useDialogFocus'
import { visibleSecuritySections } from './UserSecuritySections'

/*
 * Dialog "Passwort & Sicherheit" eines Benutzers (F3 §2.4/§6.4, Bauplan B.14 und [S9]).
 *
 * Abschnitte kommen aus dem Slot `sections/NN-*.section.jsx` (F3: 10 Passwort, 20 Zufallspasswort, 30 Reset-Link,
 * 40 zweiter Faktor; F10: 50, F14: 60). Darunter steht fest der Block "Zugaenge" (Zaehler + "Alle Zugaenge
 * widerrufen").
 *
 * Vertrag `ctx` fuer Abschnitte (stabil):
 *   user                aktuelle lokale Kopie des Benutzers (aus listUsers, nach Aktionen aktualisiert)
 *   userName            Anzeigename (display_name || username)
 *   setUser(u)          lokale Kopie ersetzen (z. B. nach 2FA-Reset aus `res.user`)
 *   resetMailAvailable  SMTP + oeffentliche Basis-URL eingerichtet (aus listUsers)
 *   busy                '' oder Schluessel der laufenden Aktion; run(key, fn) setzt ihn, leert Meldungen,
 *                       faengt Fehler (-> Fehlerbanner im Dialog) und liefert das Ergebnis von fn (oder undefined)
 *   setError(msg) / setInfo(msg)   Meldungen im Dialog
 *   showOneTime({ password, name }) Einmalanzeige eines Passworts (schliesst nur ueber "gesichert")
 *   changed(message?)   Liste neu laden (+ optional Seitenbanner), Dialog bleibt offen
 *   finish(message?)    Liste neu laden (+ Seitenbanner) und Dialog schliessen
 *   refreshSummary()    Zaehler der Zugaenge neu laden
 */
export default function UserSecurityModal({ user, resetMailAvailable, onClose, onChanged }) {
    const { t } = useTranslation()
    const [local, setLocal] = useState(user)
    const [busy, setBusy] = useState('')
    const [modalError, setModalError] = useState('')
    const [modalInfo, setModalInfo] = useState('')
    const [oneTime, setOneTime] = useState(null)
    const [summary, setSummary] = useState(null)
    const [summaryError, setSummaryError] = useState('')
    const userName = local.display_name || local.username

    const loadSummary = useCallback((signal) => api.getUserAccessSummary(local.id, { signal })
        .then((res) => {
            setSummary(res)
            setSummaryError('')
        })
        .catch((err) => {
            if (err?.name !== 'AbortError') setSummaryError(err.message)
        }), [local.id])

    useEffect(() => {
        const ctrl = new AbortController()
        api.getUserAccessSummary(local.id, { signal: ctrl.signal })
            .then((res) => {
                setSummary(res)
                setSummaryError('')
            })
            .catch((err) => {
                if (err?.name !== 'AbortError') setSummaryError(err.message)
            })
        return () => ctrl.abort()
    }, [local.id])

    const canClose = !busy && !oneTime

    // Fokus, Tab-Falle, ESC (nicht waehrend einer Aktion/Einmal-Anzeige) und Fokus-Rueckgabe: lib/useDialogFocus.
    // Die Einmal-Anzeige darueber nutzt denselben Hook; nur der oberste Dialog reagiert auf ESC/Tab.
    const dialogRef = useDialogFocus({ onClose, canClose })

    const run = useCallback(async (key, fn) => {
        setBusy(key)
        setModalError('')
        setModalInfo('')
        try {
            return await fn()
        } catch (err) {
            if (err?.name !== 'AbortError') setModalError(err.message)
            return undefined
        } finally {
            setBusy('')
        }
    }, [])

    const ctx = useMemo(() => ({
        user: local,
        userName,
        setUser: (u) => setLocal((prev) => ({ ...prev, ...u })),
        resetMailAvailable: !!resetMailAvailable,
        busy,
        run,
        setError: setModalError,
        setInfo: setModalInfo,
        showOneTime: (value) => setOneTime(value),
        changed: (message) => onChanged?.(message),
        finish: (message) => {
            onChanged?.(message)
            onClose()
        },
        refreshSummary: () => loadSummary(),
    }), [local, userName, resetMailAvailable, busy, run, onChanged, onClose, loadSummary])

    const sections = visibleSecuritySections(local, ctx)

    function closeOneTime() {
        setOneTime(null)
        onChanged?.()
        onClose()
    }

    return (
        <>
            <div
                className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
                onClick={() => { if (canClose) onClose() }}
            >
                <div
                    ref={dialogRef}
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby="user-security-title"
                    className="glass-card p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto"
                    onClick={(e) => e.stopPropagation()}
                >
                    <div className="flex items-start justify-between gap-3 mb-4">
                        <div className="flex items-start gap-3 min-w-0">
                            <KeyRound className="w-5 h-5 text-warning shrink-0 mt-1" aria-hidden="true" />
                            <div className="min-w-0">
                                <h2 id="user-security-title" className="text-lg font-bold text-text-primary">{t('users.securityTitle')}</h2>
                                <p className="text-sm text-text-muted break-all">{t('users.securityFor', { name: userName })}</p>
                            </div>
                        </div>
                        <button
                            type="button"
                            onClick={onClose}
                            disabled={!canClose}
                            className="p-1 rounded-lg hover:bg-bg-hover text-text-muted disabled:opacity-40"
                            aria-label={t('common.close')}
                        >
                            <X className="w-5 h-5" />
                        </button>
                    </div>

                    <ModalErrorBanner message={modalError} onClose={() => setModalError('')} />
                    {modalInfo && (
                        <div role="status" className="mb-4 p-3 rounded-xl bg-success/10 border border-success/30 text-success text-sm flex items-start gap-2">
                            <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                            <p className="flex-1 break-words">{modalInfo}</p>
                            <button type="button" onClick={() => setModalInfo('')} className="text-xs hover:underline" aria-label={t('common.close')}>×</button>
                        </div>
                    )}

                    <div className="space-y-4">
                        {sections.map((entry) => {
                            const Section = entry.Component
                            return (
                                <section key={entry.id} className="rounded-xl border border-border/60 p-4 space-y-3">
                                    {entry.titleKey && <h3 className="text-sm font-semibold text-text-primary">{t(entry.titleKey)}</h3>}
                                    <Section user={local} ctx={ctx} />
                                </section>
                            )
                        })}
                        <AccessBlock ctx={ctx} summary={summary} summaryError={summaryError} />
                    </div>

                    <div className="flex justify-end pt-4 mt-4 border-t border-border">
                        <button
                            type="button"
                            onClick={onClose}
                            disabled={!canClose}
                            className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-50"
                        >
                            {t('common.close')}
                        </button>
                    </div>
                </div>
            </div>

            {oneTime && (
                <OneTimeSecretModal
                    title={t('users.oneTimePasswordTitle', { name: oneTime.name })}
                    body={t('users.oneTimePasswordWarning')}
                    secret={oneTime.password}
                    doneLabel={t('users.oneTimeDone')}
                    onDone={closeOneTime}
                />
            )}
        </>
    )
}

const REVOKE_DEFAULTS = { panel_tokens: true, dyndns_tokens: true, webhooks: true, reset_2fa: false, remove_passkeys: false }

// Block "Zugaenge" ([S9], E-F3-3): Zaehler aus access-summary und "Alle Zugaenge widerrufen".
function AccessBlock({ ctx, summary, summaryError }) {
    const { t } = useTranslation()
    const [opts, setOpts] = useState(REVOKE_DEFAULTS)
    const { user, userName, busy, run } = ctx
    const nothingSelected = !Object.values(opts).some(Boolean)

    const rows = [
        ['panel_tokens', 'users.accessSummaryPanelTokens'],
        ['dyndns_tokens', 'users.accessSummaryDyndnsTokens'],
        ['webhooks', 'users.accessSummaryWebhooks'],
    ]
    const toggles = [
        ['panel_tokens', 'users.revokeAccessPanelTokens'],
        ['dyndns_tokens', 'users.revokeAccessDyndns'],
        ['webhooks', 'users.revokeAccessWebhooks'],
        ['reset_2fa', 'users.revokeAccessReset2fa'],
        ['remove_passkeys', 'users.revokeAccessRemovePasskeys'],
    ]

    async function handleRevoke() {
        if (nothingSelected || busy) return
        if (!window.confirm(t('users.revokeAccessConfirm', { name: userName }))) return
        const res = await run('revoke', () => api.revokeUserAccess(user.id, opts))
        if (!res) return
        const r = res.revoked || {}
        ctx.setInfo(t('users.revokeAccessDone', {
            tokens: r.panel_tokens ?? 0,
            dyndns: r.dyndns_tokens ?? 0,
            webhooks: r.webhooks ?? 0,
            deliveries: r.cancelled_deliveries ?? 0,
        }))
        if (res.user) ctx.setUser(res.user)
        ctx.refreshSummary()
        setOpts(REVOKE_DEFAULTS)
        ctx.changed()
    }

    return (
        <section className="rounded-xl border border-danger/30 p-4 space-y-3">
            <h3 className="text-sm font-semibold text-text-primary">{t('users.accessSummaryTitle')}</h3>
            {summaryError && <p className="text-xs text-danger">{summaryError}</p>}
            {!summary && !summaryError && (
                <p className="text-xs text-text-muted flex items-center gap-2">
                    <Loader2 className="w-3 h-3 animate-spin" aria-hidden="true" /> {t('pageSpinner.loading')}
                </p>
            )}
            {summary && (
                <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                    {rows.map(([key, label]) => (
                        <div key={key} className="contents">
                            <dt className="text-text-muted">{t(label)}</dt>
                            <dd className="text-text-primary font-medium text-right sm:text-left">{summary[key] ?? 0}</dd>
                        </div>
                    ))}
                    {(summary.pending_deliveries ?? 0) > 0 && (
                        <div className="contents">
                            <dt className="text-text-muted">{t('users.accessSummaryPendingDeliveries')}</dt>
                            <dd className="text-text-primary font-medium text-right sm:text-left">{summary.pending_deliveries}</dd>
                        </div>
                    )}
                </dl>
            )}
            <p className="text-xs text-text-muted">{t('users.revokeAccessHint')}</p>
            <div className="space-y-1.5">
                {toggles.map(([key, label]) => (
                    <label key={key} className="flex items-center gap-2 text-sm text-text-secondary">
                        <input
                            type="checkbox"
                            checked={!!opts[key]}
                            disabled={!!busy}
                            onChange={(e) => setOpts((prev) => ({ ...prev, [key]: e.target.checked }))}
                        />
                        {t(label)}
                    </label>
                ))}
            </div>
            <button
                type="button"
                onClick={handleRevoke}
                disabled={!!busy || nothingSelected}
                title={nothingSelected ? t('users.revokeAccessNothing') : undefined}
                className="inline-flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium bg-danger/15 text-danger border border-danger/40 hover:bg-danger/25 disabled:opacity-40 disabled:cursor-not-allowed"
            >
                {busy === 'revoke'
                    ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                    : <ShieldOff className="w-4 h-4" aria-hidden="true" />}
                {t('users.revokeAccessButton')}
            </button>
        </section>
    )
}
