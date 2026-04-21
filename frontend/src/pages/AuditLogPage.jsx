import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { ScrollText, Loader2, CheckCircle2, XCircle } from 'lucide-react'
import api from '../api'

export default function AuditLogPage() {
    const { t } = useTranslation()
    const [logs, setLogs] = useState([])
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        api.getAuditLog(200).then(data => {
            setLogs(data.entries || [])
            setLoading(false)
        }).catch(() => setLoading(false))
    }, [])

    const actionLabels = {
        CREATE: t('audit.actionCreated'),
        UPDATE: t('audit.actionUpdated'),
        DELETE: t('audit.actionDeleted'),
        IMPORT: t('audit.actionImported'),
        DNSSEC_ENABLE: t('audit.actionDnssecEnable'),
        DNSSEC_DISABLE: t('audit.actionDnssecDisable'),
        BULK_UPDATE: t('audit.actionBulkUpdate'),
    }

    if (loading) return <div className="flex items-center justify-center h-64"><Loader2 className="w-8 h-8 text-accent animate-spin" /></div>

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-text-primary">{t('audit.title')}</h1>
                <p className="text-text-muted text-sm mt-1">{t('audit.subtitle')}</p>
            </div>

            <div className="glass-card overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[760px]">
                    <thead>
                        <tr className="border-b border-border">
                            <th className="text-left p-3 text-text-muted font-medium text-xs">{t('audit.timestamp')}</th>
                            <th className="text-left p-3 text-text-muted font-medium text-xs">{t('audit.action')}</th>
                            <th className="text-left p-3 text-text-muted font-medium text-xs">{t('audit.resourceType')}</th>
                            <th className="text-left p-3 text-text-muted font-medium text-xs">{t('audit.resource')}</th>
                            <th className="text-left p-3 text-text-muted font-medium text-xs">{t('audit.server')}</th>
                            <th className="text-left p-3 text-text-muted font-medium text-xs">{t('audit.status')}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {logs.map((log, i) => (
                            <tr key={i} className="border-b border-border/30 hover:bg-bg-hover/30">
                                <td className="p-3 text-xs text-text-secondary whitespace-nowrap">
                                    {new Date(log.timestamp).toLocaleString('de-DE')}
                                </td>
                                <td className="p-3 text-xs font-medium text-text-primary">{actionLabels[log.action] || log.action}</td>
                                <td className="p-3"><span className="text-xs px-1.5 py-0.5 bg-accent/10 text-accent-light rounded">{log.resource_type}</span></td>
                                <td className="p-3 text-xs text-text-secondary font-mono">{log.resource_name}</td>
                                <td className="p-3 text-xs text-text-muted">{log.server_name || '-'}</td>
                                <td className="p-3">
                                    {log.status === 'success'
                                        ? <span className="inline-flex items-center gap-1 text-xs text-success"><CheckCircle2 className="w-3.5 h-3.5" /> {t('audit.ok')}</span>
                                        : <span className="inline-flex items-center gap-1 text-xs text-danger"><XCircle className="w-3.5 h-3.5" /> {t('audit.error')}</span>
                                    }
                                </td>
                            </tr>
                        ))}
                        {logs.length === 0 && (
                            <tr><td colSpan={6} className="p-12 text-center text-text-muted">
                                <ScrollText className="w-12 h-12 mx-auto mb-3 opacity-30" />
                                <p>{t('audit.noEntries')}</p>
                            </td></tr>
                        )}
                    </tbody>
                </table>
              </div>
            </div>
        </div>
    )
}
