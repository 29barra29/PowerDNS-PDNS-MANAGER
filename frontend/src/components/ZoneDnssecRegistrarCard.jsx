import { Shield, Loader2, Trash2 } from 'lucide-react'

/**
 * DNSSEC-Hinweis- und Aktionskarte (Registrar-DS, nicht manuelle DNSKEY-RDATA).
 * Aus ZoneDetailPage ausgelagert, Verhalten unverändert.
 */
export default function ZoneDnssecRegistrarCard({
    t,
    zoneMeta,
    dsLoading,
    dsError,
    dsData,
    canEdit,
    enablingDnssec,
    disablingDnssec,
    onOpenModal,
    onEnableDnssec,
    onDisableDnssec,
}) {
    return (
        <div className="glass-card p-5 border border-amber-500/25 bg-amber-500/5">
            <div className="flex items-start gap-3">
                <div className="w-10 h-10 rounded-xl bg-amber-500/15 flex items-center justify-center shrink-0">
                    <Shield className="w-5 h-5 text-amber-400" />
                </div>
                <div className="min-w-0 flex-1 space-y-3">
                    <div>
                        <h2 className="text-base font-semibold text-text-primary">{t('zoneDetail.dnssecRegistrarCardTitle')}</h2>
                        <p className="text-sm text-text-muted mt-1 leading-relaxed">{t('zoneDetail.dnssecRegistrarIntro')}</p>
                        <p className="text-xs text-text-muted mt-2 leading-relaxed">{t('zoneDetail.dnssecManualRecordHint')}</p>
                    </div>

                    {!zoneMeta?.dnssec ? (
                        <div className="rounded-lg border border-border bg-bg-secondary/40 p-4 text-sm text-text-secondary">
                            <p className="font-medium text-text-primary mb-2">{t('zoneDetail.dnssecNotActiveTitle')}</p>
                            <p className="text-text-muted mb-3">{t('zoneDetail.dnssecNotActiveBody')}</p>
                            <button
                                type="button"
                                onClick={onEnableDnssec}
                                disabled={!canEdit || enablingDnssec || disablingDnssec}
                                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent/20 hover:bg-accent/30 text-accent-light text-sm font-medium disabled:opacity-50"
                            >
                                {enablingDnssec ? <Loader2 className="w-4 h-4 animate-spin" /> : <Shield className="w-4 h-4" />}
                                {t('zoneDetail.dnssecEnableButton')}
                            </button>
                        </div>
                    ) : dsLoading ? (
                        <div className="flex items-center gap-2 text-sm text-text-muted py-2">
                            <Loader2 className="w-4 h-4 animate-spin text-accent" />
                            {t('zoneDetail.dnssecLoading')}
                        </div>
                    ) : dsError ? (
                        <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">{dsError}</div>
                    ) : (dsData?.ds_records?.length ?? 0) === 0 ? (
                        <p className="text-sm text-text-muted">{t('zoneDetail.dnssecEmpty')}</p>
                    ) : (
                        <div className="space-y-3">
                            <p className="text-sm text-text-secondary">{t('zoneDetail.dnssecCardShortHint')}</p>
                            <button
                                type="button"
                                onClick={onOpenModal}
                                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent/15 hover:bg-accent/25 text-accent-light text-sm font-medium"
                            >
                                <Shield className="w-4 h-4" />
                                {t('zoneDetail.dnssecModalOpenButton')}
                            </button>
                        </div>
                    )}

                    {zoneMeta?.dnssec && (
                        <div className="pt-3 mt-2 border-t border-amber-500/20 space-y-2">
                            <p className="text-xs text-text-muted leading-relaxed">{t('zoneDetail.dnssecDisableHint')}</p>
                            <button
                                type="button"
                                onClick={onDisableDnssec}
                                disabled={!canEdit || enablingDnssec || disablingDnssec}
                                className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-danger/40 bg-danger/10 text-danger text-xs font-medium hover:bg-danger/20 disabled:opacity-50"
                            >
                                {disablingDnssec ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                                {t('zoneDetail.dnssecDisableButton')}
                            </button>
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}
