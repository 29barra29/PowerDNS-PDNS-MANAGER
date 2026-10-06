import { useTranslation } from 'react-i18next'
import { Eye, EyeOff, Loader2, Power, PowerOff, Trash2 } from 'lucide-react'
import { algorithmLabel, keyTypeLabel } from '../../zoneDetail/dnssecModel.js'

// Schluesseltabelle (F4 §2.4) – nur Darstellung; Aktionen per Callback onAction(action, key) mit
// action = 'activate' | 'deactivate' | 'publish' | 'unpublish' | 'delete'. Alle Knoepfe gesperrt, wenn
// !canManage oder gerade eine Schluesselaktion laeuft (busyKeyId !== null).
function Badge({ tone, children }) {
    const cls = {
        ok: 'bg-success/10 border-success/30 text-success',
        off: 'bg-bg-secondary border-border text-text-muted',
        info: 'bg-accent/10 border-accent/30 text-accent-light',
        warn: 'bg-warning/10 border-warning/30 text-warning',
    }[tone] || 'bg-bg-secondary border-border text-text-muted'
    return <span className={`inline-block text-[11px] px-1.5 py-0.5 rounded border whitespace-nowrap ${cls}`}>{children}</span>
}

function ActionButton({ icon: Icon, label, onClick, disabled, title, danger, busy }) {
    return (
        <button
            type="button"
            onClick={onClick}
            disabled={disabled}
            title={title || label}
            aria-label={label}
            className={`p-1.5 rounded transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${danger
                ? 'text-text-muted hover:text-danger hover:bg-danger/10'
                : 'text-text-muted hover:text-accent-light hover:bg-accent/10'}`}
        >
            {busy ? <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" /> : <Icon className="w-4 h-4" aria-hidden="true" />}
        </button>
    )
}

export default function DnssecKeysTable({ keys, canManage, busyKeyId = null, supportsPublished, onAction }) {
    const { t } = useTranslation()
    const list = keys || []
    if (!list.length) return <p className="text-sm text-text-muted">{t('dnssec.keysEmpty')}</p>
    const locked = !canManage || busyKeyId !== null
    const lockedTitle = !canManage ? t('dnssec.actionsDisabled') : undefined
    const publishBlocked = supportsPublished === false

    return (
        <div className="overflow-x-auto -mx-1">
            <table className="w-full text-sm">
                <thead>
                    <tr className="text-left text-xs text-text-muted border-b border-border">
                        <th className="px-2 py-2 font-medium">{t('dnssec.colId')}</th>
                        <th className="px-2 py-2 font-medium">{t('dnssec.colType')}</th>
                        <th className="px-2 py-2 font-medium">{t('dnssec.colAlgorithm')}</th>
                        <th className="px-2 py-2 font-medium">{t('dnssec.colBits')}</th>
                        <th className="px-2 py-2 font-medium">{t('dnssec.colKeyTag')}</th>
                        <th className="px-2 py-2 font-medium">{t('dnssec.colState')}</th>
                        <th className="px-2 py-2 font-medium text-right">{t('dnssec.colActions')}</th>
                    </tr>
                </thead>
                <tbody>
                    {list.map((k) => {
                        const busy = busyKeyId === k.id
                        return (
                            <tr key={k.id} className="border-b border-border/40 last:border-0 align-top">
                                <td className="px-2 py-2 font-mono text-text-primary">{k.id}</td>
                                <td className="px-2 py-2 text-text-primary">{keyTypeLabel(k)}</td>
                                <td className="px-2 py-2 text-text-secondary whitespace-nowrap">{algorithmLabel(k.algorithm, k.algorithm_number)}</td>
                                <td className="px-2 py-2 text-text-secondary">{k.bits ?? '—'}</td>
                                <td className="px-2 py-2 font-mono text-text-secondary">{k.key_tag ?? '—'}</td>
                                <td className="px-2 py-2">
                                    <div className="flex flex-wrap gap-1">
                                        {k.active
                                            ? <Badge tone="ok">{t('dnssec.badgeActive')}</Badge>
                                            : <Badge tone="off">{t('dnssec.badgeInactive')}</Badge>}
                                        {k.published === null || k.published === undefined
                                            ? <Badge tone="off">{t('dnssec.badgePublishedImplicit')}</Badge>
                                            : k.published
                                                ? <Badge tone="info">{t('dnssec.badgePublished')}</Badge>
                                                : <Badge tone="warn">{t('dnssec.badgeUnpublished')}</Badge>}
                                        {k.rollover_role === 'new' && <Badge tone="info">{t('dnssec.badgeRoleNew')}</Badge>}
                                        {k.rollover_role === 'old' && <Badge tone="warn">{t('dnssec.badgeRoleOld')}</Badge>}
                                    </div>
                                </td>
                                <td className="px-2 py-1.5">
                                    <div className="flex justify-end gap-0.5">
                                        {k.active ? (
                                            <ActionButton icon={PowerOff} label={t('dnssec.actionDeactivate')} busy={busy}
                                                disabled={locked} title={lockedTitle} onClick={() => onAction('deactivate', k)} />
                                        ) : (
                                            <ActionButton icon={Power} label={t('dnssec.actionActivate')} busy={busy}
                                                disabled={locked} title={lockedTitle} onClick={() => onAction('activate', k)} />
                                        )}
                                        {k.published_effective !== false ? (
                                            <ActionButton icon={EyeOff} label={t('dnssec.actionUnpublish')}
                                                disabled={locked || publishBlocked}
                                                title={publishBlocked ? t('dnssec.publishNeeds43') : lockedTitle}
                                                onClick={() => onAction('unpublish', k)} />
                                        ) : (
                                            <ActionButton icon={Eye} label={t('dnssec.actionPublish')}
                                                disabled={locked || publishBlocked}
                                                title={publishBlocked ? t('dnssec.publishNeeds43') : lockedTitle}
                                                onClick={() => onAction('publish', k)} />
                                        )}
                                        <ActionButton icon={Trash2} label={t('dnssec.actionDelete')} danger
                                            disabled={locked} title={lockedTitle} onClick={() => onAction('delete', k)} />
                                    </div>
                                </td>
                            </tr>
                        )
                    })}
                </tbody>
            </table>
        </div>
    )
}
