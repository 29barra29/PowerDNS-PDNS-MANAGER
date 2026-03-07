import { useState, useEffect } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, Plus, Trash2, Pencil, Loader2, AlertCircle, Shield } from 'lucide-react'
import api from '../api'

const RECORD_TYPES = {
    A: { label: 'A – IPv4', fields: [{ id: 'ipv4', label: 'IPv4-Adresse', placeholder: '93.184.216.34' }], build: f => f.ipv4 },
    AAAA: { label: 'AAAA – IPv6', fields: [{ id: 'ipv6', label: 'IPv6-Adresse', placeholder: '2001:db8::1' }], build: f => f.ipv6 },
    CNAME: { label: 'CNAME – Weiterleitung', fields: [{ id: 'target', label: 'Ziel-Domain', placeholder: 'example.com.' }], build: f => f.target.endsWith('.') ? f.target : f.target + '.' },
    MX: {
        label: 'MX – Mailserver', fields: [
            { id: 'priority', label: 'Priorität', placeholder: '10', type: 'number' },
            { id: 'mailserver', label: 'Mail-Server', placeholder: 'mail.example.com.' },
        ], build: f => `${f.priority} ${f.mailserver.endsWith('.') ? f.mailserver : f.mailserver + '.'}`
    },
    TXT: { label: 'TXT – Text', fields: [{ id: 'text', label: 'Text', placeholder: 'v=spf1 ...', textarea: true }], build: f => f.text.startsWith('"') ? f.text : `"${f.text}"` },
    NS: { label: 'NS – Nameserver', fields: [{ id: 'ns', label: 'Nameserver', placeholder: 'ns1.example.com.' }], build: f => f.ns.endsWith('.') ? f.ns : f.ns + '.' },
    SRV: {
        label: 'SRV – Dienst', fields: [
            { id: 'pri', label: 'Priorität', placeholder: '10', type: 'number' },
            { id: 'weight', label: 'Gewicht', placeholder: '5', type: 'number' },
            { id: 'port', label: 'Port', placeholder: '443', type: 'number' },
            { id: 'target', label: 'Ziel', placeholder: 'server.example.com.' },
        ], build: f => `${f.pri} ${f.weight} ${f.port} ${f.target.endsWith('.') ? f.target : f.target + '.'}`
    },
    CAA: {
        label: 'CAA – Zertifikat', fields: [
            { id: 'flag', label: 'Flag', placeholder: '0', type: 'number' },
            { id: 'tag', label: 'Tag', placeholder: 'issue', select: ['issue', 'issuewild', 'iodef'] },
            { id: 'val', label: 'Wert', placeholder: 'letsencrypt.org' },
        ], build: f => `${f.flag} ${f.tag} "${f.val}"`
    },
    PTR: { label: 'PTR – Reverse', fields: [{ id: 'host', label: 'Hostname', placeholder: 'host.example.com.' }], build: f => f.host.endsWith('.') ? f.host : f.host + '.' },
    TLSA: {
        label: 'TLSA – DANE', fields: [
            { id: 'usage', label: 'Usage', placeholder: '3', type: 'number' },
            { id: 'sel', label: 'Selector', placeholder: '1', type: 'number' },
            { id: 'match', label: 'Matching', placeholder: '1', type: 'number' },
            { id: 'hash', label: 'Hash', placeholder: 'abc123...' },
        ], build: f => `${f.usage} ${f.sel} ${f.match} ${f.hash}`
    },
    SSHFP: {
        label: 'SSHFP – SSH', fields: [
            { id: 'algo', label: 'Algo', placeholder: '4', type: 'number' },
            { id: 'fptype', label: 'Hash-Typ', placeholder: '2', type: 'number' },
            { id: 'fp', label: 'Fingerprint', placeholder: 'abc...' },
        ], build: f => `${f.algo} ${f.fptype} ${f.fp}`
    },
}

export default function ZoneDetailPage() {
    const { server, zoneId } = useParams()
    const navigate = useNavigate()
    const [zone, setZone] = useState(null)
    const [records, setRecords] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [showAdd, setShowAdd] = useState(false)
    const [addType, setAddType] = useState('A')
    const [addName, setAddName] = useState('@')
    const [addTTL, setAddTTL] = useState('3600')
    const [dynFields, setDynFields] = useState({})
    const [saving, setSaving] = useState(false)

    const zoneName = zoneId.replace(/\.$/, '')

    useEffect(() => { loadZone() }, [server, zoneId])

    async function loadZone() {
        setLoading(true)
        try {
            const data = await api.listRecords(server, zoneId)
            setRecords(data.records || [])
            setZone(data)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    function resolveName(name) {
        if (name === '@') return zoneName
        if (!name.includes(zoneName)) return `${name}.${zoneName}`
        return name
    }

    async function handleAddRecord(e) {
        e.preventDefault()
        setSaving(true)
        setError('')
        const def = RECORD_TYPES[addType]
        if (!def) return

        // Validate fields
        for (const f of def.fields) {
            if (!dynFields[f.id]?.trim()) {
                setError(`Bitte "${f.label}" ausfüllen`)
                setSaving(false)
                return
            }
        }

        const content = def.build(dynFields)
        const fqdn = resolveName(addName)

        try {
            await api.createRecord(server, zoneId, {
                name: fqdn,
                type: addType,
                ttl: parseInt(addTTL),
                records: [{ content, disabled: false }],
            })
            setShowAdd(false)
            setDynFields({})
            setAddName('@')
            loadZone()
        } catch (err) {
            setError(err.message)
        } finally {
            setSaving(false)
        }
    }

    async function handleDelete(name, type) {
        if (!confirm(`Eintrag "${name}" (${type}) löschen?`)) return
        try {
            await api.deleteRecord(server, zoneId, { name, type })
            loadZone()
        } catch (err) {
            setError(err.message)
        }
    }

    if (loading) return <div className="flex items-center justify-center h-64"><Loader2 className="w-8 h-8 text-accent animate-spin" /></div>

    // Group records by type
    const grouped = {}
    records.forEach(r => {
        if (!grouped[r.type]) grouped[r.type] = []
        grouped[r.type].push(r)
    })
    const typeOrder = ['SOA', 'NS', 'A', 'AAAA', 'CNAME', 'MX', 'TXT', 'SRV', 'CAA', 'TLSA', 'SSHFP', 'PTR']
    const sortedTypes = Object.keys(grouped).sort((a, b) => {
        const ai = typeOrder.indexOf(a), bi = typeOrder.indexOf(b)
        return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi)
    })

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <button onClick={() => navigate('/zones')} className="p-2 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-primary transition-colors">
                        <ArrowLeft className="w-5 h-5" />
                    </button>
                    <div>
                        <h1 className="text-2xl font-bold text-text-primary">{zoneName}</h1>
                        <p className="text-text-muted text-sm">Server: {server} • {records.length} Einträge</p>
                    </div>
                </div>
                <button
                    onClick={() => { setShowAdd(true); setAddType('A'); setDynFields({}) }}
                    className="flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all"
                >
                    <Plus className="w-4 h-4" /> Eintrag hinzufügen
                </button>
            </div>

            {error && (
                <div className="p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-center gap-3">
                    <AlertCircle className="w-5 h-5 shrink-0" />
                    <p className="text-sm">{error}</p>
                    <button onClick={() => setError('')} className="ml-auto text-xs hover:underline">×</button>
                </div>
            )}

            {/* Records grouped by type */}
            {sortedTypes.map(type => (
                <div key={type} className="glass-card overflow-hidden">
                    <div className="px-4 py-3 bg-bg-hover/30 border-b border-border flex items-center gap-2">
                        <span className="text-xs font-bold px-2 py-0.5 bg-accent/20 text-accent-light rounded">{type}</span>
                        <span className="text-xs text-text-muted">{grouped[type].length} Eintrag/Einträge</span>
                    </div>
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-border/50">
                                <th className="text-left p-3 text-text-muted font-medium text-xs">Name</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">Wert</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs w-20">TTL</th>
                                <th className="text-right p-3 text-text-muted font-medium text-xs w-20">Aktionen</th>
                            </tr>
                        </thead>
                        <tbody>
                            {grouped[type].map((r, i) => (
                                <tr key={i} className="border-b border-border/30 hover:bg-bg-hover/30 transition-colors">
                                    <td className="p-3 font-mono text-xs text-text-primary">{r.name.replace(/\.$/, '')}</td>
                                    <td className="p-3 font-mono text-xs text-text-secondary break-all">{r.content}</td>
                                    <td className="p-3 text-text-muted text-xs">{r.ttl}</td>
                                    <td className="p-3 text-right">
                                        {type !== 'SOA' && type !== 'NS' && (
                                            <button
                                                onClick={() => handleDelete(r.name, r.type)}
                                                className="p-1 rounded text-text-muted hover:text-danger hover:bg-danger/10 transition-colors"
                                                title="Löschen"
                                            >
                                                <Trash2 className="w-3.5 h-3.5" />
                                            </button>
                                        )}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ))}

            {/* Add Record Modal */}
            {showAdd && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setShowAdd(false)}>
                    <div className="glass-card p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                        <h2 className="text-lg font-bold text-text-primary mb-4">DNS-Eintrag hinzufügen</h2>
                        <form onSubmit={handleAddRecord} className="space-y-4">
                            {/* Type, Name, TTL */}
                            <div className="grid grid-cols-3 gap-4">
                                <div>
                                    <label className="block text-xs font-medium text-text-secondary mb-1">Eintragstyp</label>
                                    <select value={addType} onChange={e => { setAddType(e.target.value); setDynFields({}) }} className="w-full px-3 py-2 text-sm">
                                        {Object.entries(RECORD_TYPES).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                                    </select>
                                </div>
                                <div>
                                    <label className="block text-xs font-medium text-text-secondary mb-1">Name</label>
                                    <div className="flex items-center gap-1">
                                        <input value={addName} onChange={e => setAddName(e.target.value)} className="flex-1 px-3 py-2 text-sm" placeholder="@" />
                                        <span className="text-xs text-text-muted whitespace-nowrap">.{zoneName}</span>
                                    </div>
                                    <p className="text-xs text-text-muted mt-0.5">@ = Hauptdomain</p>
                                </div>
                                <div>
                                    <label className="block text-xs font-medium text-text-secondary mb-1">TTL</label>
                                    <select value={addTTL} onChange={e => setAddTTL(e.target.value)} className="w-full px-3 py-2 text-sm">
                                        <option value="60">1 Min</option>
                                        <option value="300">5 Min</option>
                                        <option value="3600">1 Std</option>
                                        <option value="14400">4 Std</option>
                                        <option value="86400">1 Tag</option>
                                    </select>
                                </div>
                            </div>

                            {/* Dynamic fields */}
                            <div className="border-t border-border pt-4">
                                <div className="grid grid-cols-2 gap-4">
                                    {RECORD_TYPES[addType]?.fields.map(f => (
                                        <div key={f.id} className={f.textarea ? 'col-span-2' : ''}>
                                            <label className="block text-xs font-medium text-text-secondary mb-1">{f.label}</label>
                                            {f.select ? (
                                                <select
                                                    value={dynFields[f.id] || f.select[0]}
                                                    onChange={e => setDynFields({ ...dynFields, [f.id]: e.target.value })}
                                                    className="w-full px-3 py-2 text-sm"
                                                >
                                                    {f.select.map(o => <option key={o} value={o}>{o}</option>)}
                                                </select>
                                            ) : f.textarea ? (
                                                <textarea
                                                    value={dynFields[f.id] || ''}
                                                    onChange={e => setDynFields({ ...dynFields, [f.id]: e.target.value })}
                                                    placeholder={f.placeholder}
                                                    className="w-full px-3 py-2 text-sm min-h-[80px]"
                                                />
                                            ) : (
                                                <input
                                                    type={f.type || 'text'}
                                                    value={dynFields[f.id] || ''}
                                                    onChange={e => setDynFields({ ...dynFields, [f.id]: e.target.value })}
                                                    placeholder={f.placeholder}
                                                    className="w-full px-3 py-2 text-sm"
                                                />
                                            )}
                                        </div>
                                    ))}
                                </div>
                            </div>

                            <div className="flex justify-end gap-3 pt-2 border-t border-border">
                                <button type="button" onClick={() => setShowAdd(false)} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary">Abbrechen</button>
                                <button type="submit" disabled={saving} className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                                    {saving && <Loader2 className="w-4 h-4 animate-spin" />} Speichern
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    )
}
