import { useState, useEffect } from 'react'
import { ScrollText, Loader2, CheckCircle2, XCircle } from 'lucide-react'
import api from '../api'

export default function AuditLogPage() {
    const [logs, setLogs] = useState([])
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        api.getAuditLog(200).then(data => {
            setLogs(data.entries || [])
            setLoading(false)
        }).catch(() => setLoading(false))
    }, [])

    const actionLabels = {
        CREATE: 'Erstellt', UPDATE: 'Aktualisiert', DELETE: 'Gelöscht',
        IMPORT: 'Importiert', DNSSEC_ENABLE: 'DNSSEC aktiviert', DNSSEC_DISABLE: 'DNSSEC deaktiviert',
        BULK_UPDATE: 'Massenbearbeitung',
    }

    if (loading) return <div className="flex items-center justify-center h-64"><Loader2 className="w-8 h-8 text-accent animate-spin" /></div>

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-text-primary">Protokoll</h1>
                <p className="text-text-muted text-sm mt-1">Alle Änderungen im Überblick</p>
            </div>

            <div className="glass-card overflow-hidden">
                <table className="w-full text-sm">
                    <thead>
                        <tr className="border-b border-border">
                            <th className="text-left p-3 text-text-muted font-medium text-xs">Zeitpunkt</th>
                            <th className="text-left p-3 text-text-muted font-medium text-xs">Aktion</th>
                            <th className="text-left p-3 text-text-muted font-medium text-xs">Typ</th>
                            <th className="text-left p-3 text-text-muted font-medium text-xs">Ressource</th>
                            <th className="text-left p-3 text-text-muted font-medium text-xs">Server</th>
                            <th className="text-left p-3 text-text-muted font-medium text-xs">Status</th>
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
                                        ? <span className="inline-flex items-center gap-1 text-xs text-success"><CheckCircle2 className="w-3.5 h-3.5" /> OK</span>
                                        : <span className="inline-flex items-center gap-1 text-xs text-danger"><XCircle className="w-3.5 h-3.5" /> Fehler</span>
                                    }
                                </td>
                            </tr>
                        ))}
                        {logs.length === 0 && (
                            <tr><td colSpan={6} className="p-12 text-center text-text-muted">
                                <ScrollText className="w-12 h-12 mx-auto mb-3 opacity-30" />
                                <p>Noch keine Einträge</p>
                            </td></tr>
                        )}
                    </tbody>
                </table>
            </div>
        </div>
    )
}
