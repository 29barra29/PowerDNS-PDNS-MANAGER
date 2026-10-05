import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
    AlertCircle, ChevronDown, ChevronRight, KeyRound, Loader2, Plus, RefreshCw, Shield, ShieldCheck, ShieldOff,
} from 'lucide-react'
import DnssecHints from './DnssecHints'
import DnssecKeysTable from './DnssecKeysTable'
import DnssecPeersList from './DnssecPeersList'
import {
    MAX_KEYS_DEFAULT, activeAlgorithms, cardState, formatNsec, keyModelOf, rolloverRunning,
} from '../../zoneDetail/dnssecModel.js'

// DNSSEC-Karte der Zonenansicht (F4 §2.1, ersetzt ZoneDnssecRegistrarCard): Zustand, Zusammenfassung, Aktionen,
// Hinweise, Schluesseltabelle (einklappbar) und andere Server mit dieser Zone. Laedt nichts selbst – Status und
// Aktionen kommen vom DNSSEC-Abschnitt (zoneDetail/ZoneDnssecSection.jsx).
// Props: { status, loading, error, canManage, busyKeyId, onReload(), onOpen(modal), onKeyAction(action, key) }
const STATE_STYLE = {
    signed: { cls: 'bg-success/10 border-success/30 text-success', Icon: ShieldCheck, key: 'dnssec.stateSigned' },
    off: { cls: 'bg-bg-secondary border-border text-text-secondary', Icon: ShieldOff, key: 'dnssec.stateOff' },
    keysInactive: { cls: 'bg-warning/10 border-warning/30 text-warning', Icon: AlertCircle, key: 'dnssec.stateKeysInactive' },
    presigned: { cls: 'bg-accent/10 border-accent/30 text-accent-light', Icon: Shield, key: 'dnssec.statePresigned' },
}

function ActionButton({ icon: Icon, label, onClick, disabled, title, tone = 'default' }) {
    const cls = {
        default: 'border-border bg-bg-secondary hover:bg-bg-hover text-text-primary',
        accent: 'border-accent/30 bg-accent/15 hover:bg-accent/25 text-accent-light',
        danger: 'border-danger/40 bg-danger/10 hover:bg-danger/20 text-danger',
    }[tone]
    return (
        <span title={title} className="inline-flex">
            <button
                type="button"
                onClick={onClick}
                disabled={disabled}
                className={`inline-flex items-center gap-2 px-3 py-2 rounded-lg border text-sm font-medium transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${cls}`}
            >
                <Icon className="w-4 h-4 shrink-0" aria-hidden="true" />
                {label}
            </button>
        </span>
    )
}

function SummaryRow({ label, children }) {
    return (
        <div className="flex flex-col sm:flex-row sm:gap-3 text-sm">
            <dt className="text-text-muted sm:w-48 shrink-0">{label}</dt>
            <dd className="text-text-primary min-w-0 break-words">{children}</dd>
        </div>
    )
}

export default function ZoneDnssecCard({ status, loading, error, canManage, busyKeyId = null, onReload, onOpen, onKeyAction }) {
    const { t } = useTranslation()
    const [tableOpen, setTableOpen] = useState(null) // null = automatisch (F4 §2.1 Nr. 5)
    const state = cardState(status)
    const keys = status?.keys || []
    const maxKeys = status?.capabilities?.max_keys ?? MAX_KEYS_DEFAULT
    const lockedTitle = canManage ? undefined : t('dnssec.actionsDisabled')
    const autoOpen = rolloverRunning(status) || (keys.length > 0 && !keys.some((k) => k.active))
    const showTable = tableOpen === null ? autoOpen : tableOpen
    const style = state ? STATE_STYLE[state] : null
    const keyModel = keyModelOf(keys)
    const keyModelText = keyModel === 'csk' ? t('dnssec.keyModelCsk') : keyModel === 'ksk_zsk' ? t('dnssec.keyModelKskZsk')
        : keyModel === 'mixed' ? t('dnssec.keyModelMixed') : '—'

    return (
        <section className="glass-card p-5 border border-amber-500/25 bg-amber-500/5" aria-labelledby="dnssec-card-title">
            <div className="flex items-start gap-3">
                <div className="w-10 h-10 rounded-xl bg-amber-500/15 flex items-center justify-center shrink-0">
                    <Shield className="w-5 h-5 text-amber-400" aria-hidden="true" />
                </div>
                <div className="min-w-0 flex-1 space-y-4">
                    <div className="flex flex-wrap items-center gap-2">
                        <h2 id="dnssec-card-title" className="text-base font-semibold text-text-primary">{t('dnssec.cardTitle')}</h2>
                        {style && (
                            <span className={`inline-flex items-center gap-1.5 text-xs px-2 py-0.5 rounded-full border ${style.cls}`}>
                                <style.Icon className="w-3.5 h-3.5" aria-hidden="true" />
                                {t(style.key)}
                            </span>
                        )}
                        <button
                            type="button"
                            onClick={onReload}
                            disabled={loading}
                            className="ml-auto inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs text-text-muted hover:text-text-primary hover:bg-bg-hover disabled:opacity-50"
                            title={t('dnssec.reload')}
                        >
                            {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden="true" /> : <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" />}
                            {t('dnssec.reload')}
                        </button>
                    </div>

                    {error && (
                        <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm" role="alert">
                            {t('dnssec.loadError', { error })}
                        </div>
                    )}

                    {!status && !error && (
                        <div className="flex items-center gap-2 text-sm text-text-muted py-2" role="status">
                            <Loader2 className="w-4 h-4 animate-spin text-accent" aria-hidden="true" />
                            {t('dnssec.loading')}
                        </div>
                    )}

                    {status && state === 'off' && (
                        <div className="rounded-lg border border-border bg-bg-secondary/40 p-4 text-sm text-text-secondary">
                            <p className="font-medium text-text-primary mb-2">{t('zoneDetail.dnssecNotActiveTitle')}</p>
                            <p className="text-text-muted mb-3">{t('zoneDetail.dnssecNotActiveBody')}</p>
                            <ActionButton icon={Shield} label={t('dnssec.btnEnable')} tone="accent" onClick={() => onOpen('enable')}
                                disabled={!canManage} title={lockedTitle} />
                        </div>
                    )}

                    {status && state === 'signed' && (
                        <dl className="space-y-1.5">
                            <SummaryRow label={t('dnssec.summaryAlgorithm')}>{activeAlgorithms(keys).join(', ') || '—'}</SummaryRow>
                            <SummaryRow label={t('dnssec.summaryKeyModel')}>{keyModelText}</SummaryRow>
                            <SummaryRow label={t('dnssec.summaryDenial')}>
                                <span>{formatNsec(status.nsec, t)}</span>
                                {status.nsec?.opt_out && <span className="ml-2 text-xs px-1.5 py-0.5 rounded border border-border">{t('dnssec.badgeOptOut')}</span>}
                                {status.nsec?.narrow && <span className="ml-2 text-xs px-1.5 py-0.5 rounded border border-border">{t('dnssec.badgeNarrow')}</span>}
                            </SummaryRow>
                            <SummaryRow label={t('dnssec.summaryServer')}>
                                {status.server}
                                <span className="text-text-muted">{' · '}{status.server_version
                                    ? t('dnssec.serverVersion', { version: status.server_version })
                                    : t('dnssec.versionUnknown')}</span>
                            </SummaryRow>
                        </dl>
                    )}

                    {status && (state === 'signed' || state === 'keysInactive') && (
                        <div className="space-y-3">
                            <div className="flex flex-wrap gap-2">
                                <ActionButton icon={Shield} label={t('zoneDetail.dnssecModalOpenButton')} tone="accent" onClick={() => onOpen('ds')} />
                                {state === 'signed' && (
                                    <ActionButton icon={RefreshCw} label={t('dnssec.btnRollover')} onClick={() => onOpen('rollover')}
                                        disabled={!canManage} title={lockedTitle} />
                                )}
                                <ActionButton icon={KeyRound} label={t('dnssec.btnNsec')} onClick={() => onOpen('nsec')}
                                    disabled={!canManage || keys.length === 0} title={lockedTitle} />
                                <ActionButton icon={Plus} label={t('dnssec.btnAddKey')} onClick={() => onOpen('addKey')}
                                    disabled={!canManage || keys.length >= maxKeys}
                                    title={keys.length >= maxKeys ? t('dnssec.maxKeysReached', { max: maxKeys }) : lockedTitle} />
                            </div>
                        </div>
                    )}

                    {status && <DnssecHints hints={status.hints} />}

                    {status && keys.length > 0 && (
                        <div className="rounded-lg border border-border bg-bg-secondary/20">
                            <button
                                type="button"
                                onClick={() => setTableOpen(!showTable)}
                                aria-expanded={showTable}
                                className="w-full flex items-center gap-2 px-3 py-2 text-sm font-medium text-text-secondary hover:text-text-primary"
                            >
                                {showTable ? <ChevronDown className="w-4 h-4" aria-hidden="true" /> : <ChevronRight className="w-4 h-4" aria-hidden="true" />}
                                {t('dnssec.keysTitle', { count: keys.length })}
                            </button>
                            {showTable && (
                                <div className="px-3 pb-3">
                                    <DnssecKeysTable
                                        keys={keys}
                                        canManage={canManage}
                                        busyKeyId={busyKeyId}
                                        supportsPublished={status.capabilities?.supports_published}
                                        onAction={onKeyAction}
                                    />
                                </div>
                            )}
                        </div>
                    )}

                    {status && <DnssecPeersList peers={status.peers} />}

                    {status && (state === 'signed' || state === 'keysInactive') && (
                        <div className="pt-3 border-t border-amber-500/20 space-y-2">
                            <p className="text-sm font-medium text-text-primary">{t('zoneDetail.dnssecRegistrarCardTitle')}</p>
                            <p className="text-xs text-text-muted leading-relaxed">{t('zoneDetail.dnssecRegistrarIntro')}</p>
                            <p className="text-xs text-text-muted leading-relaxed">{t('zoneDetail.dnssecManualRecordHint')}</p>
                        </div>
                    )}

                    {status && (state === 'signed' || state === 'keysInactive') && (
                        <div className="pt-3 border-t border-amber-500/20 space-y-2">
                            <p className="text-xs text-text-muted leading-relaxed">{t('zoneDetail.dnssecDisableHint')}</p>
                            <ActionButton icon={ShieldOff} label={t('dnssec.btnDisable')} tone="danger" onClick={() => onOpen('disable')}
                                disabled={!canManage} title={lockedTitle} />
                        </div>
                    )}
                </div>
            </div>
        </section>
    )
}
