import { useCallback, useEffect, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Shield, Loader2, Copy, X, Trash2 } from 'lucide-react'
import api from '../api'
import ZoneDnssecRegistrarCard from '../components/ZoneDnssecRegistrarCard'
import { useZoneDetail } from './zoneDetailContext'

// DNSSEC-Bereich der Zonenansicht (Plan B.14): kapselt die Registrar-Karte und den DS-/Registrar-Assistenten
// (DS-Modal) aus ZoneDetailPage 2.4.1 - Verhalten unveraendert. F4-B ersetzt diese Datei (ZoneDnssecCard).
//
// Vertrag mit der Shell (bleibt beim Ersetzen erhalten):
//   - default export  ZoneDnssecSection  -> Karte im Records-Tab (tabs/10-records.tab.jsx)
//   - named export    ZoneDnssecHost     -> wird von der Shell IMMER gemountet (auch waehrend des Ladens);
//                                           laedt die DS-Daten ueber subscribeZoneLoaded und rendert das DS-Modal.
//   - Slot-State 'dnssec.dsModal' (true/false) oeffnet das DS-Modal; die Kopf-Aktion
//     header-actions/10-dnssec-ds.action.jsx setzt ihn.

const DS_MODAL_KEY = 'dnssec.dsModal'
const STATE_KEY = 'dnssec.ds'
const INITIAL_STATE = Object.freeze({
    dsData: null, dsLoading: false, dsError: '', loadedSeq: 0, enabling: false, disabling: false,
})

/** Gemeinsamer Zustand + Aktionen fuer Karte und Modal (ueber den Slot-State der Zonenansicht). */
function useDnssec() {
    const { t } = useTranslation()
    const ctx = useZoneDetail()
    const { server, zoneId, slotState, setSlotState, setSuccess, setError, loadZone, zoneMeta, zoneLoadSeq } = ctx
    const state = slotState[STATE_KEY] || INITIAL_STATE

    const update = useCallback(
        (patch) => setSlotState(STATE_KEY, (prev) => ({ ...INITIAL_STATE, ...prev, ...patch })),
        [setSlotState],
    )
    const setShowDsModal = useCallback((open) => setSlotState(DS_MODAL_KEY, !!open), [setSlotState])

    // Nach einem Laden mit aktivem DNSSEC sind die DS-Daten erst nach dem Nachladen aktuell -> so lange "laedt".
    const dsLoading = state.dsLoading || (!!zoneMeta?.dnssec && state.loadedSeq !== zoneLoadSeq)

    async function handleEnableDnssec() {
        if (!window.confirm(t('zoneDetail.dnssecEnableConfirm'))) return
        update({ enabling: true, dsError: '' })
        try {
            await api.enableDNSSEC(server, zoneId, {})
            setSuccess(t('zoneDetail.dnssecEnabledOk'))
            await loadZone()
        } catch (err) {
            setError(err.message)
        } finally {
            update({ enabling: false })
        }
    }

    async function handleDisableDnssec() {
        if (!window.confirm(t('zoneDetail.dnssecDisableConfirm'))) return
        update({ disabling: true, dsError: '' })
        try {
            await api.disableDNSSEC(server, zoneId)
            setShowDsModal(false)
            setSuccess(t('zoneDetail.dnssecDisabledOk'))
            await loadZone()
        } catch (err) {
            setError(err.message)
        } finally {
            update({ disabling: false })
        }
    }

    function copyToClipboard(text, i18nToast, vars) {
        if (!text) return
        const msg = i18nToast ? t(i18nToast, vars) : null
        navigator.clipboard.writeText(String(text)).then(() => {
            if (msg) setSuccess(msg)
        }).catch(() => {})
    }

    return {
        t, ctx, state, dsLoading, setShowDsModal,
        handleEnableDnssec, handleDisableDnssec, copyToClipboard,
    }
}

/** Karte "DNSSEC beim Registrar" im Records-Tab. */
export default function ZoneDnssecSection() {
    const { t, ctx, state, dsLoading, setShowDsModal, handleEnableDnssec, handleDisableDnssec } = useDnssec()
    return (
        <ZoneDnssecRegistrarCard
            t={t}
            zoneMeta={ctx.zoneMeta}
            dsLoading={dsLoading}
            dsError={state.dsError}
            dsData={state.dsData}
            canEdit={ctx.canEdit}
            enablingDnssec={state.enabling}
            disablingDnssec={state.disabling}
            onOpenModal={() => setShowDsModal(true)}
            onEnableDnssec={handleEnableDnssec}
            onDisableDnssec={handleDisableDnssec}
        />
    )
}

/** Immer gemountet: laedt DS-Daten nach jedem Laden der Zone und zeigt das DS-Modal. */
export function ZoneDnssecHost() {
    const { server, zoneId, subscribeZoneLoaded, setSlotState, slotState, loading } = useZoneDetail()
    const reqRef = useRef(0)

    useEffect(() => subscribeZoneLoaded(async (event) => {
        if (!event.ok) return
        const req = ++reqRef.current
        const update = (patch) => setSlotState(STATE_KEY, (prev) => ({ ...INITIAL_STATE, ...prev, ...patch }))
        if (!event.zoneMeta?.dnssec) {
            update({ dsData: null, dsLoading: false, dsError: '', loadedSeq: event.seq })
            return
        }
        update({ dsLoading: true, dsError: '' })
        try {
            const ds = await api.getDsRecords(server, zoneId)
            if (req !== reqRef.current) return
            update({ dsData: ds, dsError: '', dsLoading: false, loadedSeq: event.seq })
        } catch (e) {
            if (req !== reqRef.current) return
            update({ dsData: null, dsError: e.message || String(e), dsLoading: false, loadedSeq: event.seq })
        }
    }), [subscribeZoneLoaded, server, zoneId, setSlotState])

    // Wie 2.4.1: waehrend des Ganzseiten-Ladens ist kein Modal sichtbar.
    if (slotState[DS_MODAL_KEY] !== true || loading) return null
    return <DnssecDsModal />
}

/** "DS / Registrar"-Assistent: erklaert DS-Varianten und einzelne Felder zum Kopieren (2.4.1). */
function DnssecDsModal() {
    const { t, ctx, state, dsLoading, setShowDsModal, handleEnableDnssec, handleDisableDnssec, copyToClipboard } = useDnssec()
    const { zoneMeta, canEdit } = ctx
    const { dsData, dsError, enabling: enablingDnssec, disabling: disablingDnssec } = state

    /** Bevorzugte DS-Zeile: Digest-Typ 2 (SHA-256) – so will es der Großteil der Registrar-Formulare. */
    const recommendedDsRow = useMemo(() => {
        const rows = dsData?.ds_records || []
        const ok = (r) => r.parsed && !r.parsed.error
        return rows.find((r) => ok(r) && (r.parsed.recommended || r.parsed.digest_type === 2)) || rows.find(ok) || null
    }, [dsData])

    return (
        <div
            className="fixed inset-0 z-[60] flex items-center justify-center bg-black/65 backdrop-blur-sm p-4"
            onClick={() => setShowDsModal(false)}
        >
            <div
                className="glass-card w-full max-w-2xl max-h-[90vh] overflow-y-auto p-5 sm:p-6 shadow-2xl"
                onClick={(e) => e.stopPropagation()}
            >
                <div className="flex items-start justify-between gap-3 mb-4">
                    <h2 className="text-lg font-bold text-text-primary pr-6 leading-snug">
                        {t('zoneDetail.dnssecModalTitle')}
                    </h2>
                    <button
                        type="button"
                        onClick={() => setShowDsModal(false)}
                        className="p-1.5 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-primary shrink-0"
                        title={t('zoneDetail.dnssecModalClose')}
                    >
                        <X className="w-5 h-5" />
                    </button>
                </div>

                {!zoneMeta?.dnssec ? (
                    <div className="space-y-3 text-sm text-text-secondary">
                        <p>{t('zoneDetail.dnssecModalNeedEnable')}</p>
                        <button
                            type="button"
                            onClick={() => { handleEnableDnssec() }}
                            disabled={!canEdit || enablingDnssec || disablingDnssec}
                            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent/20 text-accent-light text-sm font-medium disabled:opacity-50"
                        >
                            {enablingDnssec ? <Loader2 className="w-4 h-4 animate-spin" /> : <Shield className="w-4 h-4" />}
                            {t('zoneDetail.dnssecEnableButton')}
                        </button>
                    </div>
                ) : dsLoading ? (
                    <div className="flex items-center gap-2 text-text-muted py-6">
                        <Loader2 className="w-5 h-5 animate-spin text-accent" />
                        {t('zoneDetail.dnssecLoading')}
                    </div>
                ) : dsError ? (
                    <div className="p-3 rounded-lg bg-danger/10 text-danger text-sm">{dsError}</div>
                ) : (
                    <div className="space-y-5 text-sm">
                        <div className="rounded-lg border border-border bg-bg-secondary/30 p-4">
                            <p className="font-medium text-text-primary mb-1">{t('zoneDetail.dnssecModalWhyTitle')}</p>
                            <p className="text-text-muted leading-relaxed">{t('zoneDetail.dnssecModalWhyBody')}</p>
                        </div>

                        {recommendedDsRow?.parsed && !recommendedDsRow.parsed.error && (
                            <div className="rounded-lg border-2 border-success/40 bg-success/10 p-4">
                                <p className="text-xs font-semibold text-success mb-3 uppercase tracking-wide">
                                    {t('zoneDetail.dnssecModalRecommended')}
                                </p>
                                <div className="space-y-1">
                                    {[
                                        [t('zoneDetail.dnssecModalFieldKeyTag'), String(recommendedDsRow.parsed.key_tag)],
                                        [t('zoneDetail.dnssecModalFieldAlgorithmName'), String(recommendedDsRow.parsed.algorithm_name || '')],
                                        [t('zoneDetail.dnssecModalFieldAlgorithm'), String(recommendedDsRow.parsed.algorithm)],
                                        [t('zoneDetail.dnssecModalFieldDigestType'), `${recommendedDsRow.parsed.digest_type} – ${recommendedDsRow.parsed.digest_type_name}`],
                                        [t('zoneDetail.dnssecModalFieldDigestHex'), recommendedDsRow.parsed.digest_hex],
                                    ].map(([label, val]) => (
                                        <div
                                            key={label}
                                            className="flex flex-col sm:flex-row sm:items-center gap-2 py-1 border-b border-border/30 last:border-0"
                                        >
                                            <span className="text-xs text-text-muted shrink-0 sm:w-52">{label}</span>
                                            <code className="flex-1 text-xs font-mono break-all text-text-primary bg-bg-primary/50 px-2 py-1 rounded min-w-0">
                                                {val}
                                            </code>
                                            <button
                                                type="button"
                                                onClick={() => copyToClipboard(val, 'zoneDetail.dnssecFieldCopied', { field: label })}
                                                className="shrink-0 self-end sm:self-center p-1.5 rounded text-accent-light hover:bg-accent/15"
                                            >
                                                <Copy className="w-3.5 h-3.5" />
                                            </button>
                                        </div>
                                    ))}
                                </div>
                                <button
                                    type="button"
                                    onClick={() => {
                                        const line = typeof recommendedDsRow.ds === 'string' ? recommendedDsRow.ds : recommendedDsRow.parsed.raw
                                        copyToClipboard(line, 'zoneDetail.dnssecCopied')
                                    }}
                                    className="mt-3 w-full sm:w-auto inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-success/20 hover:bg-success/30 text-success text-xs font-medium"
                                >
                                    <Copy className="w-3.5 h-3.5" />
                                    {t('zoneDetail.dnssecModalCopyDsLine')}
                                </button>
                            </div>
                        )}

                        <div>
                            <p className="text-xs font-medium text-text-muted mb-2">{t('zoneDetail.dnssecModalDigestVariants')}</p>
                            <div className="space-y-2">
                                {(dsData?.ds_records || []).map((row, idx) => {
                                    const p = row.parsed
                                    const line = typeof row.ds === 'string' ? row.ds : String(row.ds)
                                    if (!p || p.error) {
                                        return (
                                            <div key={idx} className="rounded border border-border p-2 text-xs">
                                                <code className="break-all">{line}</code>
                                                <button
                                                    type="button"
                                                    onClick={() => copyToClipboard(line, 'zoneDetail.dnssecCopied')}
                                                    className="mt-2 text-accent-light text-xs"
                                                >
                                                    {t('zoneDetail.dnssecCopyLine')}
                                                </button>
                                            </div>
                                        )
                                    }
                                    return (
                                        <div
                                            key={idx}
                                            className={`rounded-lg border p-3 ${p.recommended ? 'border-success/30 bg-success/5' : 'border-border bg-bg-secondary/30'}`}
                                        >
                                            <div className="flex flex-wrap gap-2 text-[10px] text-text-muted mb-2">
                                                <span>{t('zoneDetail.dnssecKeyType')}: {row.keytype}</span>
                                                <span>ID {row.key_id}</span>
                                                <span>{p.digest_type_name}</span>
                                            </div>
                                            <code className="block text-xs font-mono break-all text-text-primary mb-2">{line}</code>
                                            <button
                                                type="button"
                                                onClick={() => copyToClipboard(line, 'zoneDetail.dnssecCopied')}
                                                className="text-xs text-accent-light hover:underline"
                                            >
                                                {t('zoneDetail.dnssecModalCopyDsLine')}
                                            </button>
                                        </div>
                                    )
                                })}
                            </div>
                        </div>

                        {(dsData?.signing_keys || []).length > 0 && (dsData.signing_keys[0].dnskey_parsed || dsData.signing_keys[0].dnskey) && (
                            <div className="rounded-lg border border-border p-4 space-y-2">
                                <p className="text-sm font-medium text-text-primary">{t('zoneDetail.dnssecModalDnskeyTitle')}</p>
                                {(dsData.signing_keys || []).map((k) => {
                                    const d = k.dnskey_parsed
                                    if (d && !d.error) {
                                        return (
                                            <div key={k.key_id} className="space-y-1 border-b border-border/40 last:border-0 pb-3 last:pb-0">
                                                {[
                                                    [t('zoneDetail.dnssecModalDnskeyFlags'), String(d.flags)],
                                                    [t('zoneDetail.dnssecModalDnskeyRole'), d.flags_role || '—'],
                                                    [t('zoneDetail.dnssecModalDnskeyProtocol'), String(d.protocol)],
                                                    [t('zoneDetail.dnssecModalFieldAlgorithmName'), d.algorithm_name || String(d.algorithm)],
                                                    [t('zoneDetail.dnssecModalDnskeyPublic'), d.public_key_base64 || '—'],
                                                ].map(([label, val]) => (
                                                    <div key={label} className="flex flex-col sm:flex-row sm:items-start gap-2 text-xs">
                                                        <span className="text-text-muted shrink-0 sm:w-52">{label}</span>
                                                        <code className="flex-1 font-mono break-all bg-bg-primary/50 px-2 py-1 rounded min-w-0">
                                                            {val}
                                                        </code>
                                                        <button
                                                            type="button"
                                                            onClick={() => copyToClipboard(val, 'zoneDetail.dnssecFieldCopied', { field: label })}
                                                            className="p-1 rounded hover:bg-bg-hover self-start"
                                                        >
                                                            <Copy className="w-3.5 h-3.5 text-accent-light" />
                                                        </button>
                                                    </div>
                                                ))}
                                            </div>
                                        )
                                    }
                                    if (k.dnskey) {
                                        return (
                                            <div key={k.key_id}>
                                                <code className="text-xs break-all block bg-bg-primary/50 p-2 rounded">{k.dnskey}</code>
                                                <button
                                                    type="button"
                                                    onClick={() => copyToClipboard(k.dnskey, 'zoneDetail.dnssecCopied')}
                                                    className="mt-1 text-xs text-accent-light"
                                                >
                                                    {t('zoneDetail.dnssecCopyLine')}
                                                </button>
                                            </div>
                                        )
                                    }
                                    return null
                                })}
                            </div>
                        )}

                        <div className="rounded-lg border border-danger/30 bg-danger/5 p-4 space-y-2">
                            <p className="text-xs text-text-muted leading-relaxed">{t('zoneDetail.dnssecDisableHint')}</p>
                            <button
                                type="button"
                                onClick={handleDisableDnssec}
                                disabled={!canEdit || enablingDnssec || disablingDnssec}
                                className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-danger/50 bg-danger/15 text-danger text-xs font-medium hover:bg-danger/25 disabled:opacity-50"
                            >
                                {disablingDnssec ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                                {t('zoneDetail.dnssecDisableButton')}
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    )
}
