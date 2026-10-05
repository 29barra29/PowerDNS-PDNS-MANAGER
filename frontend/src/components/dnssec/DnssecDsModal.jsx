import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { CheckCircle, Loader2, Shield } from 'lucide-react'
import ModalErrorBanner from '../ModalErrorBanner'
import DnssecDialog, { CopyButton } from './DnssecDialog'
import DnssecHints from './DnssecHints'
import { DS_STATUS_KEYS, dsGroups, keyTypeLabel, parseDnskey, publishedSepKeys } from '../../zoneDetail/dnssecModel.js'

// DS-/Registrar-Assistent (F4 §2.3, ersetzt das Inline-Modal aus 2.4.1). Daten aus status.keys (kein eigener
// Request). Empfohlen: DS (Digest-Typ 2) der SEP-Schluessel mit ds_status "current"; Gruppen je SEP-Schluessel mit
// Status-Badge; SHA-1/SHA-384 nur auf Wunsch; DNSKEY-Block fuer veroeffentlichte SEP-Schluessel. Kein Deaktivieren
// mehr hier (eigener Dialog). Kopieren mit await; Erfolg als Hinweis (2 s), Fehler im Dialog (F8-C04).
// Props: { zoneName, status, loading, error, canManage, onEnable(), onClose(), onReload() }
const STATUS_TONE = {
    current: 'bg-success/10 border-success/30 text-success',
    new: 'bg-accent/10 border-accent/30 text-accent-light',
    retired: 'bg-danger/10 border-danger/30 text-danger',
    inactive: 'bg-bg-secondary border-border text-text-muted',
    unpublished_active: 'bg-warning/10 border-warning/30 text-warning',
}

function FieldRow({ label, value, onCopied }) {
    return (
        <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-2 py-1 border-b border-border/30 last:border-0">
            <span className="text-xs text-text-muted shrink-0 sm:w-48">{label}</span>
            <code className="flex-1 text-xs font-mono break-all text-text-primary bg-bg-primary/50 px-2 py-1 rounded min-w-0">{value}</code>
            <CopyButton value={value} label={label} onResult={onCopied} className="self-end sm:self-center" />
        </div>
    )
}

export default function DnssecDsModal({ zoneName, status, loading, error, canManage, onEnable, onClose, onReload }) {
    const { t } = useTranslation()
    const [showAll, setShowAll] = useState(false)
    const [notice, setNotice] = useState('')
    const [copyError, setCopyError] = useState('')

    useEffect(() => {
        if (!notice) return undefined
        const timer = setTimeout(() => setNotice(''), 2000)
        return () => clearTimeout(timer)
    }, [notice])

    function onCopied(ok, label, wholeLine = false) {
        if (ok) {
            setCopyError('')
            setNotice(wholeLine || !label ? t('zoneDetail.dnssecCopied') : t('zoneDetail.dnssecFieldCopied', { field: label }))
        } else {
            setNotice('')
            setCopyError(t('secretModal.copyFailed'))
        }
    }
    const lineCopied = (ok) => onCopied(ok, null, true)

    const keys = status?.keys || []
    const hasSep = keys.some((k) => k.role === 'sep')
    const groups = dsGroups(keys, { showAll })
    const recommended = groups.filter((g) => g.recommended)
    const anySha1 = groups.some((g) => g.hasSha1)
    const dnskeys = publishedSepKeys(keys)

    let body
    if (!status && (loading || !error)) {
        body = (
            <div className="flex items-center gap-2 text-text-muted py-6" role="status">
                <Loader2 className="w-5 h-5 animate-spin text-accent" aria-hidden="true" />
                {t('dnssec.loading')}
            </div>
        )
    } else if (!status) {
        body = (
            <div className="space-y-3">
                <div className="p-3 rounded-lg bg-danger/10 border border-danger/30 text-danger text-sm">{t('dnssec.loadError', { error })}</div>
                <button type="button" onClick={onReload} className="px-3 py-1.5 rounded-lg text-sm border border-border text-text-secondary hover:bg-bg-hover">
                    {t('dnssec.reload')}
                </button>
            </div>
        )
    } else if (!hasSep) {
        body = (
            <div className="space-y-3 text-sm text-text-secondary">
                <p>{t('zoneDetail.dnssecModalNeedEnable')}</p>
                {canManage && !status.presigned && keys.length === 0 && (
                    <button
                        type="button"
                        onClick={onEnable}
                        className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent/20 hover:bg-accent/30 text-accent-light text-sm font-medium"
                    >
                        <Shield className="w-4 h-4" aria-hidden="true" />
                        {t('zoneDetail.dnssecEnableButton')}
                    </button>
                )}
            </div>
        )
    } else {
        body = (
            <div className="space-y-5 text-sm">
                <div className="rounded-lg border border-border bg-bg-secondary/30 p-4">
                    <p className="font-medium text-text-primary mb-1">{t('zoneDetail.dnssecModalWhyTitle')}</p>
                    <p className="text-text-muted leading-relaxed">{t('zoneDetail.dnssecModalWhyBody')}</p>
                </div>

                {recommended.map(({ key, recommended: ds }) => {
                    const p = ds.parsed || {}
                    return (
                        <div key={`rec-${key.id}`} className="rounded-lg border-2 border-success/40 bg-success/10 p-4">
                            <p className="text-xs font-semibold text-success mb-1 uppercase tracking-wide">{t('zoneDetail.dnssecModalRecommended')}</p>
                            <p className="text-xs text-text-muted mb-3">{t('dnssec.dsKeyHeading', { id: key.id, type: keyTypeLabel(key), tag: key.key_tag ?? '—' })}</p>
                            <div className="space-y-1">
                                <FieldRow label={t('zoneDetail.dnssecModalFieldKeyTag')} value={String(p.key_tag ?? '')} onCopied={onCopied} />
                                <FieldRow label={t('zoneDetail.dnssecModalFieldAlgorithmName')} value={String(p.algorithm_name || '')} onCopied={onCopied} />
                                <FieldRow label={t('zoneDetail.dnssecModalFieldAlgorithm')} value={String(p.algorithm ?? '')} onCopied={onCopied} />
                                <FieldRow label={t('zoneDetail.dnssecModalFieldDigestType')} value={`${p.digest_type ?? ''} – ${p.digest_type_name || ''}`} onCopied={onCopied} />
                                <FieldRow label={t('zoneDetail.dnssecModalFieldDigestHex')} value={String(p.digest_hex || '')} onCopied={onCopied} />
                            </div>
                            <div className="mt-3">
                                <CopyButton value={ds.ds} onResult={lineCopied} text={t('zoneDetail.dnssecModalCopyDsLine')}
                                    className="px-3 py-2 bg-success/20 hover:bg-success/30 text-success" />
                            </div>
                        </div>
                    )
                })}

                <div>
                    <div className="flex flex-wrap items-center justify-between gap-2 mb-2">
                        <p className="text-xs font-medium text-text-muted">{t('zoneDetail.dnssecModalDigestVariants')}</p>
                        <button type="button" onClick={() => setShowAll((v) => !v)} className="text-xs text-accent-light hover:underline">
                            {showAll ? t('dnssec.dsHideAllDigests') : t('dnssec.dsShowAllDigests')}
                        </button>
                    </div>
                    <div className="space-y-3">
                        {groups.map((g) => (
                            <div key={`grp-${g.key.id}`} className="rounded-lg border border-border bg-bg-secondary/30 p-3">
                                <div className="flex flex-wrap items-center gap-2 mb-2">
                                    <span className="text-sm text-text-primary font-medium">
                                        {t('dnssec.dsKeyHeading', { id: g.key.id, type: keyTypeLabel(g.key), tag: g.key.key_tag ?? '—' })}
                                    </span>
                                    {DS_STATUS_KEYS[g.status] && (
                                        <span className={`text-xs px-2 py-0.5 rounded-full border ${STATUS_TONE[g.status] || STATUS_TONE.inactive}`}>
                                            {t(DS_STATUS_KEYS[g.status])}
                                        </span>
                                    )}
                                </div>
                                <ul className="space-y-1.5">
                                    {g.lines.map((d) => (
                                        <li key={d.ds} className="flex items-start gap-2">
                                            <code className="flex-1 text-xs font-mono break-all text-text-primary bg-bg-primary/50 px-2 py-1 rounded min-w-0">{d.ds}</code>
                                            <span className="text-[11px] text-text-muted shrink-0 mt-1">{d.parsed?.digest_type_name || ''}</span>
                                            <CopyButton value={d.ds} label={d.parsed?.digest_type_name} onResult={lineCopied} />
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        ))}
                    </div>
                    {showAll && anySha1 && (
                        <div className="mt-3">
                            <DnssecHints hints={[{ code: 'sha1_ds', level: 'info', params: {} }]} exclude={[]} />
                        </div>
                    )}
                </div>

                {dnskeys.length > 0 && (
                    <div className="rounded-lg border border-border p-4 space-y-3">
                        <p className="text-sm font-medium text-text-primary">{t('zoneDetail.dnssecModalDnskeyTitle')}</p>
                        {dnskeys.map((k) => {
                            const d = parseDnskey(k.dnskey)
                            return (
                                <div key={`dnskey-${k.id}`} className="space-y-1 border-b border-border/40 last:border-0 pb-3 last:pb-0">
                                    <p className="text-xs text-text-muted">{t('dnssec.dsKeyHeading', { id: k.id, type: keyTypeLabel(k), tag: k.key_tag ?? '—' })}</p>
                                    {d ? (
                                        <>
                                            <FieldRow label={t('zoneDetail.dnssecModalDnskeyFlags')} value={String(d.flags)} onCopied={onCopied} />
                                            <FieldRow label={t('zoneDetail.dnssecModalDnskeyProtocol')} value={String(d.protocol)} onCopied={onCopied} />
                                            <FieldRow label={t('zoneDetail.dnssecModalFieldAlgorithm')} value={String(d.algorithm)} onCopied={onCopied} />
                                            <FieldRow label={t('zoneDetail.dnssecModalDnskeyPublic')} value={d.publicKey} onCopied={onCopied} />
                                        </>
                                    ) : null}
                                    <div className="pt-1">
                                        <CopyButton value={k.dnskey} onResult={(ok) => onCopied(ok, 'DNSKEY')} text={t('dnssec.copyDnskey')} />
                                    </div>
                                </div>
                            )
                        })}
                    </div>
                )}
            </div>
        )
    }

    return (
        <DnssecDialog title={`${t('zoneDetail.dnssecModalTitle')} – ${zoneName}`} icon={Shield} onClose={onClose}>
            <ModalErrorBanner message={copyError} onClose={() => setCopyError('')} />
            {notice && (
                <p className="mb-3 text-xs text-success flex items-center gap-1.5" role="status">
                    <CheckCircle className="w-3.5 h-3.5" aria-hidden="true" />
                    {notice}
                </p>
            )}
            {body}
        </DnssecDialog>
    )
}
