import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { AlertTriangle, Check, CheckCircle, Loader2, RefreshCw, Search, X } from 'lucide-react'
import api from '../../api'
import { useDialogFocus } from '../../lib/useDialogFocus.js'
import ModalErrorBanner from '../ModalErrorBanner'
import { CopyButton, FollowUpNotes, SerialBumpOption } from './DnssecDialog'
import DnssecParentDsCheck from './DnssecParentDsCheck'
import {
    PHASE_KEYS, ROLLOVER_STEPS, ROLLOVER_STEP_KEYS, algorithmLabel, bumpSerialValue, dnskeyCheckOutcome,
    effectiveRolloverChecks, followUpMessages, formatDuration, isPrimaryKind, keyTypeLabel, maxRecordTtl,
    recommendedDs, rolloverActionAllowed, rolloverKinds, rolloverNewKeyBody, rolloverStepIndex, sinceSeconds,
    soaTimings, withForce,
} from '../../zoneDetail/dnssecModel.js'
import ModalPortal from '../common/ModalPortal'

// Rollover-Assistent (F4 §2.5 KSK/CSK, §2.6 ZSK) – zustandslos: die Phase kommt aus status.rollover, Zeitstempel aus
// status.key_history, Wartezeiten aus den geladenen Records. Plan WS-F4-B/WS-F4-C [D10]:
//  - jede Aktion mit Schluessel-Statusaenderung sendet bump_serial (Schalter, Standard an bei Master/Producer) und
//    zeigt serial_bumped/notified; notify_error/serial_error blockieren nicht (gelber Hinweis);
//  - vor dem DS-Tausch beim Registrar und vor dem Loeschen des alten Schluessels steht der Schritt "DNSKEY auf allen
//    autoritativen NS pruefen" (GET …/dnskey-check): frei erst, wenn alle NS den neuen Schluessel liefern, sonst nur
//    mit bestaetigtem "Trotzdem fortfahren"; ist die Pruefung nicht freigegeben (capabilities.dnskey_check), zeigt
//    der Schritt "nicht geprueft" und verlangt die Checkbox;
//  - DS-Schritte: Elternzonen-Pruefung (GET …/parent-ds) als Information, Pflicht-Checkboxen bleiben.
// Fokus/ESC/Tab-Falle ueber lib/useDialogFocus (eigener Rahmen statt DnssecDialog).
// Props: { server, zoneId, zoneName, status, records, loading, onClose, onChanged(details) -> Promise, onFinished(msg) }

function RolloverDialogFrame({ title, onClose, busy, children }) {
    const { t } = useTranslation()
    const titleId = useId()
    const dialogRef = useDialogFocus({ onClose, canClose: !busy })
    return (
        <ModalPortal>
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
                    className="glass-card p-5 sm:p-6 w-full max-w-3xl max-h-[90vh] overflow-y-auto shadow-2xl outline-none"
                    onClick={(e) => e.stopPropagation()}
                >
                    <div className="flex items-start justify-between gap-3 mb-4">
                        <h2 id={titleId} className="text-lg font-bold text-text-primary flex items-center gap-2 leading-snug break-words min-w-0">
                            <RefreshCw className="w-5 h-5 text-accent-light shrink-0" aria-hidden="true" />
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
        </ModalPortal>
    )
}

function Stepper({ steps, current, t }) {
    return (
        <ol className="flex flex-wrap gap-1.5 mb-4" aria-label={t('dnssec.rolloverStepsLabel')}>
            {steps.map((step, i) => {
                const done = current > i
                const active = current === i
                return (
                    <li
                        key={`${step}-${i}`}
                        aria-current={active ? 'step' : undefined}
                        className={`flex items-center gap-1.5 text-xs px-2 py-1 rounded-lg border ${active
                            ? 'border-accent/50 bg-accent/15 text-accent-light font-medium'
                            : done ? 'border-success/30 bg-success/10 text-success' : 'border-border text-text-muted'}`}
                    >
                        {done ? <Check className="w-3.5 h-3.5" aria-hidden="true" /> : <span className="font-mono">{i + 1}</span>}
                        {t(ROLLOVER_STEP_KEYS[step])}
                    </li>
                )
            })}
        </ol>
    )
}

function Section({ title, children, tone = 'neutral' }) {
    const cls = tone === 'warning'
        ? 'border-warning/30 bg-warning/5'
        : 'border-border bg-bg-secondary/30'
    return (
        <div className={`rounded-lg border p-3 space-y-2 ${cls}`}>
            {title && <p className="text-sm font-medium text-text-primary">{title}</p>}
            {children}
        </div>
    )
}

function Confirm({ checked, onChange, disabled, children }) {
    return (
        <label className="flex items-start gap-2 text-sm text-text-primary cursor-pointer">
            <input type="checkbox" className="w-4 h-4 rounded mt-0.5" checked={!!checked} disabled={disabled}
                onChange={(e) => onChange(e.target.checked)} />
            <span>{children}</span>
        </label>
    )
}

export default function DnssecRolloverModal({ server, zoneId, zoneName, status, records, loading, onClose, onChanged, onFinished }) {
    const { t } = useTranslation()
    const kinds = rolloverKinds(status)
    const [kind, setKind] = useState(() => {
        const r = status?.rollover || {}
        const running = (track) => ['new_prepublished', 'both_active', 'old_retired'].includes(track?.phase)
        if (!running(r.sep) && running(r.zsk)) return 'zsk'
        return r.sep || !r.zsk ? 'sep' : 'zsk'
    })
    const [bump, setBump] = useState(true)
    const [busy, setBusy] = useState(false)
    const [modalError, setModalError] = useState('')
    const [followUp, setFollowUp] = useState({ info: [], warnings: [] })
    const [finishedTag, setFinishedTag] = useState(null)
    const [checkState, setCheckState] = useState({ key: '', values: {} })
    // Ergebnisse der DNSKEY-Pruefung je Schritt (dnskey/dnskeyDelete), nur gueltig fuer die aktuelle Phase
    const [dnskeyState, setDnskeyState] = useState({ key: '', results: {}, errors: {}, busy: '' })

    const track = status?.rollover?.[kind] || null
    const phaseKey = `${kind}|${track?.phase}|${track?.old_key_id}|${track?.new_key_id}`
    const checks = checkState.key === phaseKey ? checkState.values : {}
    const setCheck = (name, value) => setCheckState((prev) => ({
        key: phaseKey,
        values: { ...(prev.key === phaseKey ? prev.values : {}), [name]: value },
    }))

    const keys = status?.keys || []
    const byId = (id) => keys.find((k) => k.id === id) || null
    const oldKey = byId(track?.old_key_id)
    const newKey = byId(track?.new_key_id)
    const history = status?.key_history || {}
    const soa = soaTimings(records)
    const dnskeyTtl = soa?.minimum ?? soa?.ttl ?? null
    const maxTtl = maxRecordTtl(records)
    const primary = isPrimaryKind(status?.zone_kind)
    const bumpSerial = bumpSerialValue(status?.zone_kind, bump)
    const zoneFqdn = status?.zone || zoneId
    const steps = ROLLOVER_STEPS[kind]
    const dnskeyCapability = !!status?.capabilities?.dnskey_check
    const dnskeyCurrent = dnskeyState.key === phaseKey ? dnskeyState : { results: {}, errors: {}, busy: '' }
    const outcomeFor = (name) => dnskeyCheckOutcome(dnskeyCurrent.results[name] || null, {
        capability: dnskeyCapability, tag: newKey?.key_tag ?? null, requestError: dnskeyCurrent.errors[name] || '',
    })
    const outcomes = { dnskey: outcomeFor('dnskey'), dnskeyDelete: outcomeFor('dnskeyDelete') }
    const effective = effectiveRolloverChecks(checks, outcomes)
    const stepIndex = rolloverStepIndex(kind, track, effective)
    const locked = busy || loading
    const allowed = rolloverActionAllowed(kind, track, effective) && !locked
    const done = finishedTag !== null && track?.phase === 'idle'

    function close() {
        if (busy) return
        if (done) onFinished(t('dnssec.rolloverDone', { tag: finishedTag }))
        else onClose()
    }

    async function run(action) {
        if (locked) return
        setBusy(true)
        setModalError('')
        setFollowUp({ info: [], warnings: [] })
        const collected = { info: [], warnings: [] }
        const collect = (res) => {
            const f = followUpMessages(res?.details, t)
            collected.info.push(...f.info)
            collected.warnings.push(...f.warnings)
            return res
        }
        let lastDetails = null
        try {
            lastDetails = await action(collect)
        } catch (err) {
            setModalError(err.message || String(err))
        }
        setFollowUp(collected)
        try {
            await onChanged(lastDetails)
        } finally {
            setBusy(false)
        }
    }

    const withBump = (body) => (bumpSerial === undefined ? body : { ...body, bump_serial: bumpSerial })
    const updateKey = (keyId, data, collect) => withForce(
        (force) => api.updateDnssecKey(server, zoneId, keyId, withBump(force ? { ...data, force: true } : data)), t,
    ).then((res) => (res ? collect(res) : res))

    const startRollover = () => run(async (collect) => {
        const res = collect(await api.createDnssecKey(server, zoneId, withBump(rolloverNewKeyBody(oldKey))))
        return res?.details
    })
    const switchKeys = () => run(async (collect) => {
        const first = await updateKey(newKey.id, { active: true }, collect)
        if (first === null) return null
        const second = await updateKey(oldKey.id, { active: false }, collect)
        return second?.details || first?.details
    })
    const deactivateOld = () => run(async (collect) => (await updateKey(oldKey.id, { active: false }, collect))?.details)
    const deleteOld = () => run(async (collect) => {
        const tag = newKey?.key_tag ?? null
        const res = await withForce((force) => api.deleteDnssecKey(server, zoneId, oldKey.id, { force, bumpSerial }), t)
        if (res === null) return null
        collect(res)
        setFinishedTag(tag ?? '—')
        return res.details
    })

    const tag = (k) => (k?.key_tag ?? '—')
    function waitLine(ttl, sinceIso, textKey) {
        const since = sinceSeconds(sinceIso)
        return (
            <div className="text-xs text-text-secondary space-y-0.5">
                <p>{t(textKey, { ttl: ttl != null ? formatDuration(ttl, t) : '?' })}</p>
                <p className="text-text-muted">
                    {since != null ? t('dnssec.rolloverSince', { duration: formatDuration(since, t) }) : t('dnssec.rolloverSinceUnknown')}
                    {since != null && ttl != null && since >= ttl && (
                        <span className="ml-2 text-success">{t('dnssec.rolloverWaitDone')}</span>
                    )}
                </p>
            </div>
        )
    }

    async function runDnskeyCheck(name) {
        if (locked || dnskeyCurrent.busy) return
        const key = phaseKey
        const update = (fn) => setDnskeyState((prev) => fn(prev.key === key ? prev : { key, results: {}, errors: {}, busy: '' }))
        update((prev) => ({ ...prev, busy: name, errors: { ...prev.errors, [name]: '' } }))
        try {
            const res = await api.getDnskeyCheck(server, zoneId, { keyTags: newKey?.key_tag != null ? [newKey.key_tag] : [] })
            update((prev) => ({ ...prev, busy: '', results: { ...prev.results, [name]: res } }))
        } catch (err) {
            const message = t('dnssec.checkRequestFailed', { error: err.message || String(err) })
            update((prev) => ({ ...prev, busy: '', errors: { ...prev.errors, [name]: message } }))
        }
    }

    function confirmForce(name, value) {
        if (value && !globalThis.window?.confirm(t('dnssec.dnskeyCheckForceConfirm', { tag: tag(newKey) }))) return
        setCheck(`${name}Force`, value)
    }

    const dnskeyCheck = (name) => {
        const outcome = outcomes[name]
        const running = dnskeyCurrent.busy === name
        if (outcome.state === 'disabled') {
            return (
                <Section title={t('dnssec.rolloverStepDnskeyCheck')} tone="warning">
                    <p className="text-xs text-warning flex items-start gap-1.5 break-words">
                        <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" aria-hidden="true" />
                        <span className="min-w-0">{t('dnssec.dnskeyCheckDisabled', { tag: tag(newKey), zone: zoneFqdn })}</span>
                    </p>
                    <Confirm checked={checks[name]} onChange={(v) => setCheck(name, v)} disabled={locked}>
                        {t('dnssec.dnskeyCheckConfirm')}
                    </Confirm>
                </Section>
            )
        }
        const failed = outcome.state === 'failed' || outcome.state === 'error'
        return (
            <Section title={t('dnssec.rolloverStepDnskeyCheck')} tone={outcome.state === 'ok' ? 'neutral' : 'warning'}>
                <p className="text-xs text-text-secondary">{t('dnssec.dnskeyCheckIntro', { tag: tag(newKey) })}</p>
                <div>
                    <button
                        type="button"
                        onClick={() => runDnskeyCheck(name)}
                        disabled={locked || running}
                        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border text-xs font-medium text-text-primary hover:bg-bg-hover disabled:opacity-50"
                    >
                        {running ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <Search className="w-3.5 h-3.5" aria-hidden="true" />}
                        {outcome.state === 'unchecked' ? t('dnssec.dnskeyCheckButton') : t('dnssec.checkAgain')}
                    </button>
                </div>
                <div role="status" className="space-y-1">
                    {outcome.state === 'ok' && (
                        <p className="text-xs text-success flex items-start gap-1.5">
                            <CheckCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" aria-hidden="true" />
                            {t('dnssec.dnskeyCheckAllOk', { tag: tag(newKey) })}
                        </p>
                    )}
                    {outcome.state === 'failed' && (
                        <p className="text-xs text-warning flex items-start gap-1.5">
                            <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" aria-hidden="true" />
                            {outcome.rows.length ? t('dnssec.dnskeyCheckFailed') : t('dnssec.dnskeyCheckNone')}
                        </p>
                    )}
                    {outcome.state === 'error' && (
                        <p className="text-xs text-danger flex items-start gap-1.5" role="alert">
                            <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" aria-hidden="true" />
                            {outcome.error}
                        </p>
                    )}
                    {outcome.rows.length > 0 && (
                        <ul className="text-xs font-mono space-y-0.5 break-words">
                            {outcome.rows.map((r) => (
                                <li key={r.ns} className={r.ok ? 'text-text-secondary' : 'text-warning'}>
                                    {r.ok && t('dnssec.dnskeyCheckNsOk', { ns: r.ns, tags: r.serves.join(', ') || '—' })}
                                    {!r.ok && r.error && !r.serves.length && t('dnssec.dnskeyCheckNsError', { ns: r.ns, error: r.error })}
                                    {!r.ok && (!r.error || r.serves.length > 0) && t('dnssec.dnskeyCheckNsMissing', {
                                        ns: r.ns, tag: r.missing.join(', ') || tag(newKey), tags: r.serves.join(', ') || '—',
                                    })}
                                </li>
                            ))}
                        </ul>
                    )}
                    {outcome.truncated && <p className="text-xs text-warning">{t('dnssec.dnskeyCheckTruncated')}</p>}
                </div>
                {failed && (
                    <Confirm checked={checks[`${name}Force`]} onChange={(v) => confirmForce(name, v)} disabled={locked}>
                        <span className="text-warning">{t('dnssec.dnskeyCheckForce')}</span>
                    </Confirm>
                )}
            </Section>
        )
    }

    const primaryButton = (label, onClick, enabled, danger = false) => (
        <button
            type="button"
            onClick={onClick}
            disabled={!enabled}
            className={`px-4 py-2 rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2 ${danger
                ? 'border border-danger/50 bg-danger/20 text-danger hover:bg-danger/30'
                : 'bg-gradient-to-r from-accent to-purple-600 text-white'}`}
        >
            {busy && <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />}
            {label}
        </button>
    )

    let content = null
    if (kinds.manual) {
        content = <p className="text-sm text-warning">{t('dnssec.rolloverAlgorithmManual')}</p>
    } else if (done) {
        content = (
            <p className="p-3 rounded-lg border border-success/30 bg-success/10 text-success text-sm flex items-center gap-2" role="status">
                <CheckCircle className="w-4 h-4 shrink-0" aria-hidden="true" />
                {t('dnssec.rolloverDone', { tag: finishedTag })}
            </p>
        )
    } else if (!track) {
        content = <p className="text-sm text-text-secondary">{kind === 'zsk' ? t('dnssec.rolloverZskUnavailable') : t('dnssec.rolloverNoActive')}</p>
    } else if (track.phase === 'no_active') {
        content = <p className="text-sm text-text-secondary">{t('dnssec.rolloverNoActive')}</p>
    } else if (track.phase === 'complex') {
        content = <p className="text-sm text-text-secondary">{t('dnssec.rolloverComplex')}</p>
    } else if (track.phase === 'idle') {
        content = (
            <div className="space-y-3">
                <p className="text-sm text-text-secondary">
                    {t('dnssec.rolloverStartBody', {
                        type: keyTypeLabel(oldKey), algorithm: algorithmLabel(oldKey?.algorithm, oldKey?.algorithm_number), oldTag: tag(oldKey),
                    })}
                </p>
                <div className="flex justify-end">{primaryButton(t('dnssec.rolloverStartButton'), startRollover, allowed)}</div>
            </div>
        )
    } else if (track.phase === 'new_prepublished') {
        const ds = kind === 'sep' ? recommendedDs(newKey) : null
        content = (
            <div className="space-y-3">
                {ds && (
                    <Section title={t('dnssec.rolloverNewDsTitle')}>
                        <div className="flex items-start gap-2">
                            <code className="flex-1 text-xs font-mono break-all text-text-primary bg-bg-primary/50 px-2 py-1 rounded min-w-0">{ds.ds}</code>
                            <CopyButton value={ds.ds} label="DS" onResult={(ok) => { if (!ok) setModalError(t('secretModal.copyFailed')) }} />
                        </div>
                    </Section>
                )}
                <Section title={t('dnssec.rolloverStepWaitDnskey')}>
                    {waitLine(dnskeyTtl, history[String(newKey?.id)]?.created_at, 'dnssec.rolloverWaitDnskey')}
                    {kind === 'zsk' && (
                        <Confirm checked={checks.waited} onChange={(v) => setCheck('waited', v)} disabled={locked}>
                            {t('dnssec.rolloverConfirmWaited')}
                        </Confirm>
                    )}
                </Section>
                {dnskeyCheck('dnskey')}
                {kind === 'sep' && (
                    <Section title={t('dnssec.rolloverStepDsAdd')}>
                        <p className="text-xs text-text-secondary">{t('dnssec.rolloverDsAddBody', { tag: tag(newKey), oldTag: tag(oldKey) })}</p>
                        <p className="text-xs text-text-muted">{t('dnssec.rolloverWaitDs')}</p>
                        <DnssecParentDsCheck server={server} zoneId={zoneId} zone={zoneFqdn} mode="add"
                            capability={!!status?.capabilities?.parent_ds_check} keyId={newKey?.id ?? null}
                            tag={newKey?.key_tag ?? null} disabled={locked} />
                        <Confirm checked={checks.ds} onChange={(v) => setCheck('ds', v)} disabled={locked}>
                            {t('dnssec.rolloverConfirmDsAdded')}
                        </Confirm>
                    </Section>
                )}
                <div className="flex justify-end">{primaryButton(t('dnssec.rolloverSwitchButton'), switchKeys, allowed && !!newKey && !!oldKey)}</div>
            </div>
        )
    } else if (track.phase === 'both_active') {
        content = (
            <div className="space-y-3">
                <p className="text-sm text-text-secondary">{t('dnssec.rolloverDeactivateBody')}</p>
                {dnskeyCheck('dnskey')}
                {kind === 'sep' && (
                    <Confirm checked={checks.ds} onChange={(v) => setCheck('ds', v)} disabled={locked}>
                        {t('dnssec.rolloverConfirmDsAdded')}
                    </Confirm>
                )}
                <div className="flex justify-end">{primaryButton(t('dnssec.rolloverDeactivateButton'), deactivateOld, allowed && !!oldKey)}</div>
            </div>
        )
    } else if (track.phase === 'old_retired') {
        content = (
            <div className="space-y-3">
                <p className="text-sm text-text-secondary">{t('dnssec.rolloverRetireBody', { oldTag: tag(oldKey) })}</p>
                <Section title={t('dnssec.rolloverStepWaitMaxTtl')}>
                    {waitLine(maxTtl, history[String(oldKey?.id)]?.deactivated_at, 'dnssec.rolloverWaitMaxTtl')}
                    {kind === 'zsk' && (
                        <Confirm checked={checks.waited} onChange={(v) => setCheck('waited', v)} disabled={locked}>
                            {t('dnssec.rolloverConfirmWaited')}
                        </Confirm>
                    )}
                </Section>
                {kind === 'sep' && (
                    <Section title={t('dnssec.rolloverStepDsRemove')}>
                        <p className="text-xs text-text-secondary">{t('dnssec.rolloverDsRemoveBody', { oldTag: tag(oldKey) })}</p>
                        <DnssecParentDsCheck server={server} zoneId={zoneId} zone={zoneFqdn} mode="remove"
                            capability={!!status?.capabilities?.parent_ds_check} keyId={oldKey?.id ?? null}
                            tag={oldKey?.key_tag ?? null} disabled={locked} />
                        <Confirm checked={checks.dsRemoved} onChange={(v) => setCheck('dsRemoved', v)} disabled={locked}>
                            {t('dnssec.rolloverConfirmDsRemoved')}
                        </Confirm>
                    </Section>
                )}
                {dnskeyCheck('dnskeyDelete')}
                <div className="flex justify-end">{primaryButton(t('dnssec.rolloverDeleteButton'), deleteOld, allowed && !!oldKey, true)}</div>
            </div>
        )
    }

    const showStepper = !kinds.manual && !done && stepIndex >= 0
    return (
        <RolloverDialogFrame title={t('dnssec.rolloverTitle', { zone: zoneName })} onClose={close} busy={busy}>
            <ModalErrorBanner message={modalError} onClose={() => setModalError('')} />
            <p className="text-sm text-text-secondary mb-3">{t('dnssec.rolloverIntro')}</p>

            {!kinds.manual && (
                <fieldset className="mb-4 flex flex-col sm:flex-row gap-2 sm:gap-6 text-sm" disabled={locked}>
                    <legend className="sr-only">{t('dnssec.rolloverKindLabel')}</legend>
                    <label className="flex items-start gap-2 cursor-pointer">
                        <input type="radio" name="dnssec-rollover-kind" className="w-4 h-4 mt-0.5" checked={kind === 'sep'}
                            onChange={() => setKind('sep')} disabled={!kinds.sep} />
                        <span className="text-text-primary">{t('dnssec.rolloverKindSep')}</span>
                    </label>
                    <label className="flex items-start gap-2 cursor-pointer" title={!kinds.zsk ? t('dnssec.rolloverZskUnavailable') : undefined}>
                        <input type="radio" name="dnssec-rollover-kind" className="w-4 h-4 mt-0.5" checked={kind === 'zsk'}
                            onChange={() => setKind('zsk')} disabled={!kinds.zsk} />
                        <span className={kinds.zsk ? 'text-text-primary' : 'text-text-muted'}>
                            {t('dnssec.rolloverKindZsk')}
                            {!kinds.zsk && <span className="block text-xs">{t('dnssec.rolloverZskUnavailable')}</span>}
                        </span>
                    </label>
                </fieldset>
            )}

            {showStepper && <Stepper steps={steps} current={stepIndex} t={t} />}
            {track?.phase && PHASE_KEYS[track.phase] && !done && (
                <p className="text-xs text-text-muted mb-3">
                    {t('dnssec.rolloverPhaseLabel', { phase: t(PHASE_KEYS[track.phase]) })}
                    {loading && <Loader2 className="inline w-3.5 h-3.5 ml-2 animate-spin" aria-hidden="true" />}
                </p>
            )}

            {content}

            {primary && !kinds.manual && !done && (
                <div className="mt-4 pt-3 border-t border-border">
                    <SerialBumpOption visible checked={bump} onChange={setBump} disabled={locked} />
                </div>
            )}
            <div className="mt-3">
                <FollowUpNotes info={followUp.info} warnings={followUp.warnings} />
            </div>

            <div className="flex justify-end pt-4 mt-4 border-t border-border">
                <button type="button" onClick={close} disabled={busy}
                    className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary transition-colors disabled:opacity-50">
                    {t('common.close')}
                </button>
            </div>
        </RolloverDialogFrame>
    )
}
