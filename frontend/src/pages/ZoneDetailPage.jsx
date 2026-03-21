import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, Plus, Trash2, Pencil, Loader2, AlertCircle, Shield } from 'lucide-react'
import api from '../api'
import { ALL_RECORD_TYPE_KEYS } from '../constants/dnsRecordTypes'
import DnsRecordTypeHint from '../components/DnsRecordTypeHint'

/** Ein Feld – freier RDATA-Text (PowerDNS-Format); placeholderKey = Kurzbeispiel */
function rdataRecord(typeKey, labelKey) {
    return {
        labelKey,
        rdataTypeKey: typeKey,
        fields: [
            {
                id: 'raw',
                labelKey: 'zoneDetail.fieldRdata',
                textarea: true,
                placeholderKey: `zoneDetail.rdataPh${typeKey}`,
            },
        ],
        build: (f) => f.raw.trim(),
        parse: (c) => ({ raw: c }),
    }
}

const RECORD_TYPES = {
    A: { labelKey: 'zoneDetail.recordA', label: 'A – IPv4', fields: [{ id: 'ipv4', labelKey: 'zoneDetail.fieldIpv4', label: 'IPv4-Adresse', placeholder: '93.184.216.34' }], build: f => f.ipv4, parse: c => ({ ipv4: c }) },
    AAAA: { labelKey: 'zoneDetail.recordAAAA', label: 'AAAA – IPv6', fields: [{ id: 'ipv6', labelKey: 'zoneDetail.fieldIpv6', label: 'IPv6-Adresse', placeholder: '2001:db8::1' }], build: f => f.ipv6, parse: c => ({ ipv6: c }) },
    CNAME: { labelKey: 'zoneDetail.recordCNAME', label: 'CNAME – Weiterleitung', fields: [{ id: 'target', labelKey: 'zoneDetail.fieldTarget', label: 'Ziel-Domain', placeholder: 'example.com.' }], build: f => f.target.endsWith('.') ? f.target : f.target + '.', parse: c => ({ target: c }) },
    MX: {
        labelKey: 'zoneDetail.recordMX', label: 'MX – Mailserver', fields: [
            { id: 'priority', labelKey: 'zoneDetail.fieldPriority', label: 'Priorität', placeholder: '10', type: 'number' },
            { id: 'mailserver', labelKey: 'zoneDetail.fieldMailserver', label: 'Mail-Server', placeholder: 'mail.example.com.' },
        ], build: f => `${f.priority} ${f.mailserver.endsWith('.') ? f.mailserver : f.mailserver + '.'}`,
        parse: c => { const s = c.split(' '); return { priority: s[0], mailserver: s[1] } }
    },
    TXT: { labelKey: 'zoneDetail.recordTXT', label: 'TXT – Text', fields: [{ id: 'text', labelKey: 'zoneDetail.fieldText', label: 'Text', placeholder: 'v=spf1 ...', textarea: true }], build: f => f.text.startsWith('"') ? f.text : `"${f.text}"`, parse: c => { let t = c; if (t.startsWith('"') && t.endsWith('"')) t = t.substring(1, t.length - 1); return { text: t } } },
    NS: { labelKey: 'zoneDetail.recordNS', label: 'NS – Nameserver', fields: [{ id: 'ns', labelKey: 'zoneDetail.fieldNs', label: 'Nameserver', placeholder: 'ns1.example.com.' }], build: f => f.ns.endsWith('.') ? f.ns : f.ns + '.', parse: c => ({ ns: c }) },
    SOA: {
        labelKey: 'zoneDetail.recordSOA', label: 'SOA – Start of Authority', fields: [
            { id: 'mname', labelKey: 'zoneDetail.fieldMname', label: 'Primary NS', placeholder: 'ns1.example.com.' },
            { id: 'rname', labelKey: 'zoneDetail.fieldRname', label: 'Hostmaster Email', placeholder: 'hostmaster.example.com.' },
            { id: 'serial', labelKey: 'zoneDetail.fieldSerial', label: 'Serial', type: 'number' },
            { id: 'refresh', labelKey: 'zoneDetail.fieldRefresh', label: 'Refresh', type: 'number' },
            { id: 'retry', labelKey: 'zoneDetail.fieldRetry', label: 'Retry', type: 'number' },
            { id: 'expire', labelKey: 'zoneDetail.fieldExpire', label: 'Expire', type: 'number' },
            { id: 'minimum', labelKey: 'zoneDetail.fieldMinimum', label: 'Minimum TTL', type: 'number' },
        ], build: f => `${f.mname.endsWith('.') ? f.mname : f.mname + '.'} ${f.rname.endsWith('.') ? f.rname : f.rname + '.'} ${f.serial} ${f.refresh} ${f.retry} ${f.expire} ${f.minimum}`,
        parse: c => { const s = c.split(' '); return { mname: s[0], rname: s[1], serial: s[2], refresh: s[3], retry: s[4], expire: s[5], minimum: s[6] } }
    },
    SRV: {
        labelKey: 'zoneDetail.recordSRV', label: 'SRV – Dienst', fields: [
            { id: 'pri', labelKey: 'zoneDetail.fieldPri', label: 'Priorität', placeholder: '10', type: 'number' },
            { id: 'weight', labelKey: 'zoneDetail.fieldWeight', label: 'Gewicht', placeholder: '5', type: 'number' },
            { id: 'port', labelKey: 'zoneDetail.fieldPort', label: 'Port', placeholder: '443', type: 'number' },
            { id: 'target', labelKey: 'zoneDetail.fieldTarget', label: 'Ziel', placeholder: 'server.example.com.' },
        ], build: f => `${f.pri} ${f.weight} ${f.port} ${f.target.endsWith('.') ? f.target : f.target + '.'}`,
        parse: c => { const s = c.split(' '); return { pri: s[0], weight: s[1], port: s[2], target: s[3] } }
    },
    CAA: {
        labelKey: 'zoneDetail.recordCAA', label: 'CAA – Zertifikat', fields: [
            { id: 'flag', labelKey: 'zoneDetail.fieldFlag', label: 'Flag', placeholder: '0', type: 'number' },
            { id: 'tag', labelKey: 'zoneDetail.fieldTag', label: 'Tag', placeholder: 'issue', select: ['issue', 'issuewild', 'iodef'] },
            { id: 'val', labelKey: 'zoneDetail.fieldVal', label: 'Wert', placeholder: 'letsencrypt.org' },
        ], build: f => `${f.flag} ${f.tag} "${f.val}"`,
        parse: c => { const s = c.split(' '); return { flag: s[0], tag: s[1], val: s.slice(2).join(' ').replace(/"/g, '') } }
    },
    PTR: { labelKey: 'zoneDetail.recordPTR', label: 'PTR – Reverse', fields: [{ id: 'host', labelKey: 'zoneDetail.fieldHost', label: 'Hostname', placeholder: 'host.example.com.' }], build: f => f.host.endsWith('.') ? f.host : f.host + '.', parse: c => ({ host: c }) },
    TLSA: {
        labelKey: 'zoneDetail.recordTLSA', label: 'TLSA – DANE', fields: [
            { id: 'usage', labelKey: 'zoneDetail.fieldUsage', label: 'Usage', placeholder: '3', type: 'number' },
            { id: 'sel', labelKey: 'zoneDetail.fieldSel', label: 'Selector', placeholder: '1', type: 'number' },
            { id: 'match', labelKey: 'zoneDetail.fieldMatch', label: 'Matching', placeholder: '1', type: 'number' },
            { id: 'hash', labelKey: 'zoneDetail.fieldHash', label: 'Hash', placeholder: 'abc123...' },
        ], build: f => `${f.usage} ${f.sel} ${f.match} ${f.hash}`,
        parse: c => { const s = c.split(' '); return { usage: s[0], sel: s[1], match: s[2], hash: s[3] } }
    },
    SSHFP: {
        labelKey: 'zoneDetail.recordSSHFP', label: 'SSHFP – SSH', fields: [
            { id: 'algo', labelKey: 'zoneDetail.fieldAlgo', label: 'Algo', placeholder: '4', type: 'number' },
            { id: 'fptype', labelKey: 'zoneDetail.fieldFptype', label: 'Hash-Typ', placeholder: '2', type: 'number' },
            { id: 'fp', labelKey: 'zoneDetail.fieldFp', label: 'Fingerprint', placeholder: 'abc...' },
        ], build: f => `${f.algo} ${f.fptype} ${f.fp}`,
        parse: c => { const s = c.split(' '); return { algo: s[0], fptype: s[1], fp: s[2] } }
    },
    // Weitere von PowerDNS unterstützte Typen – einheitlich als RDATA (Experten / RFC-Format)
    ALIAS: rdataRecord('ALIAS', 'zoneDetail.recordALIAS'),
    DNAME: rdataRecord('DNAME', 'zoneDetail.recordDNAME'),
    LOC: rdataRecord('LOC', 'zoneDetail.recordLOC'),
    NAPTR: rdataRecord('NAPTR', 'zoneDetail.recordNAPTR'),
    DS: rdataRecord('DS', 'zoneDetail.recordDS'),
    DNSKEY: rdataRecord('DNSKEY', 'zoneDetail.recordDNSKEY'),
    NSEC: rdataRecord('NSEC', 'zoneDetail.recordNSEC'),
    NSEC3: rdataRecord('NSEC3', 'zoneDetail.recordNSEC3'),
    NSEC3PARAM: rdataRecord('NSEC3PARAM', 'zoneDetail.recordNSEC3PARAM'),
    RRSIG: rdataRecord('RRSIG', 'zoneDetail.recordRRSIG'),
    SPF: rdataRecord('SPF', 'zoneDetail.recordSPF'),
    HTTPS: rdataRecord('HTTPS', 'zoneDetail.recordHTTPS'),
    SVCB: rdataRecord('SVCB', 'zoneDetail.recordSVCB'),
    OPENPGPKEY: rdataRecord('OPENPGPKEY', 'zoneDetail.recordOPENPGPKEY'),
}

export default function ZoneDetailPage() {
    const { t } = useTranslation()
    const { server, zoneId } = useParams()
    const navigate = useNavigate()
    const [_zone, setZone] = useState(null)
    const [records, setRecords] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [showAdd, setShowAdd] = useState(false)
    const [isEdit, setIsEdit] = useState(false)
    const [oldContent, setOldContent] = useState('')
    const [addType, setAddType] = useState('A')
    const [addName, setAddName] = useState('@')
    const [addTTL, setAddTTL] = useState('3600')
    const [dynFields, setDynFields] = useState({})
    const [saving, setSaving] = useState(false)

    const zoneName = zoneId.replace(/\.$/, '')

    useEffect(() => { loadZone() }, [server, zoneId]) // eslint-disable-line react-hooks/exhaustive-deps -- loadZone stable

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
        let fqdn;
        if (name === '@') fqdn = zoneName;
        else if (!name.includes(zoneName)) fqdn = `${name}.${zoneName}`;
        else fqdn = name;
        // PowerDNS requires trailing dot for FQDN
        if (!fqdn.endsWith('.')) fqdn = fqdn + '.';
        return fqdn;
    }

    /** Anzeige: wie der Record-Name in PowerDNS landet (kein „@.zone.zone“) */
    function previewFqdn(name) {
        return resolveName((name || '').trim() || '@')
    }

    function openEdit(record) {
        setIsEdit(true);
        setAddType(record.type);
        setOldContent(record.content);
        setAddTTL(record.ttl.toString());
        
        let name = record.name.replace(/\.$/, '');
        if (name === zoneName) name = '@';
        else if (name.endsWith(`.${zoneName}`)) name = name.substring(0, name.length - zoneName.length - 1);
        setAddName(name);
        
        const def = RECORD_TYPES[record.type] || { parse: () => ({}) };
        try {
            setDynFields(def.parse ? def.parse(record.content) : {});
        } catch (e) {
            console.warn('Could not parse record content:', e);
            setDynFields({});
        }
        setShowAdd(true);
    }

    async function handleAddRecord(e) {
        e.preventDefault()
        setSaving(true)
        setError('')
        const def = RECORD_TYPES[addType]
        if (!def) {
            setSaving(false);
            return;
        }

        // Validate fields
        for (const f of def.fields) {
            if (dynFields[f.id] === undefined || dynFields[f.id].toString().trim() === '') {
                setError(t('zoneDetail.fillField', { label: f.labelKey ? t(f.labelKey) : f.label }))
                setSaving(false)
                return
            }
        }

        const content = def.build(dynFields)
        const fqdn = resolveName(addName)

        try {
            if (isEdit) {
                await api.updateRecord(server, zoneId, {
                    name: fqdn,
                    type: addType,
                    ttl: parseInt(addTTL),
                    old_content: oldContent,
                    new_content: content,
                    disabled: false
                });
            } else {
                await api.createRecord(server, zoneId, {
                    name: fqdn,
                    type: addType,
                    ttl: parseInt(addTTL),
                    records: [{ content, disabled: false }],
                });
            }
            setShowAdd(false)
            setDynFields({})
            setAddName('@')
            setIsEdit(false)
            loadZone()
        } catch (err) {
            setError(err.message)
        } finally {
            setSaving(false)
        }
    }

    async function handleDelete(name, type) {
        if (!confirm(t('zoneDetail.deleteRecordConfirm', { name, type }))) return
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
    const typeOrder = ALL_RECORD_TYPE_KEYS
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
                        <p className="text-text-muted text-sm">{t('zoneDetail.recordsCount', { server, count: records.length })}</p>
                    </div>
                </div>
                <button
                    onClick={() => { setShowAdd(true); setIsEdit(false); setAddType('A'); setAddName('@'); setAddTTL('3600'); setDynFields({}) }}
                    className="flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all"
                >
                    <Plus className="w-4 h-4" /> {t('zoneDetail.addRecord')}
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
                        <span className="text-xs text-text-muted">{t('zoneDetail.recordCount', { count: grouped[type].length })}</span>
                    </div>
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-border/50">
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('zoneDetail.name')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('zoneDetail.value')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs w-20">{t('zoneDetail.ttl')}</th>
                                <th className="text-right p-3 text-text-muted font-medium text-xs w-20">{t('zones.actions')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {grouped[type].map((r, i) => (
                                <tr key={i} className="border-b border-border/30 hover:bg-bg-hover/30 transition-colors">
                                    <td className="p-3 font-mono text-xs text-text-primary">{r.name.replace(/\.$/, '')}</td>
                                    <td className="p-3 font-mono text-xs text-text-secondary break-all">{r.content}</td>
                                    <td className="p-3 text-text-muted text-xs">{r.ttl}</td>
                                    <td className="p-3 text-right">
                                        <button
                                            onClick={() => openEdit(r)}
                                            className="p-1 rounded text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors mr-1"
                                            title={t('zoneDetail.edit')}
                                        >
                                            <Pencil className="w-3.5 h-3.5" />
                                        </button>
                                        {type !== 'SOA' && type !== 'NS' && (
                                            <button
                                                onClick={() => handleDelete(r.name, r.type)}
                                                className="p-1 rounded text-text-muted hover:text-danger hover:bg-danger/10 transition-colors"
                                                title={t('zoneDetail.deleteRecord')}
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
                    <div className="glass-card p-6 w-full max-w-3xl max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                        <h2 className="text-lg font-bold text-text-primary mb-4">{isEdit ? t('zoneDetail.editRecord') : t('zoneDetail.addRecord')}</h2>
                        <form onSubmit={handleAddRecord} className="space-y-4">
                            {/* Type, Name, TTL – gleiche Zeilenhöhe, kein Überlappen (min-w-0 in Grid) */}
                            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 items-start">
                                <div className="min-w-0 flex flex-col gap-1">
                                    <label className="block text-xs font-medium text-text-secondary leading-tight">{t('zoneDetail.recordType')}</label>
                                    <select value={addType} disabled={isEdit} onChange={e => { setAddType(e.target.value); setDynFields({}) }} className="w-full h-10 px-3 text-sm rounded-lg border border-border bg-bg-primary text-text-primary disabled:opacity-50">
                                        {ALL_RECORD_TYPE_KEYS.filter((k) => RECORD_TYPES[k]).map((k) => {
                                            const v = RECORD_TYPES[k]
                                            return <option key={k} value={k}>{v.labelKey ? t(v.labelKey) : v.label}</option>
                                        })}
                                    </select>
                                </div>
                                <div className="min-w-0 flex flex-col gap-1.5">
                                    <label className="block text-xs font-medium text-text-secondary leading-tight">{t('zoneDetail.nameRelative')}</label>
                                    <input
                                        value={addName}
                                        disabled={isEdit}
                                        onChange={(e) => setAddName(e.target.value)}
                                        className="w-full h-10 px-3 text-sm rounded-lg border border-border bg-bg-primary text-text-primary disabled:opacity-50"
                                        placeholder="@"
                                        autoComplete="off"
                                        spellCheck={false}
                                    />
                                    <div className="rounded-lg border border-accent/25 bg-accent/5 px-2.5 py-2">
                                        <p className="text-[10px] font-medium uppercase tracking-wide text-text-muted mb-0.5">{t('zoneDetail.namePreviewLabel')}</p>
                                        <p className="font-mono text-sm text-accent-light break-all" title={previewFqdn(addName)}>{previewFqdn(addName)}</p>
                                    </div>
                                    <p className="text-xs text-text-muted leading-snug">{t('zoneDetail.mainDomainHint')}</p>
                                </div>
                                <div className="min-w-0 flex flex-col gap-1">
                                    <label className="block text-xs font-medium text-text-secondary leading-tight">{t('zoneDetail.ttl')}</label>
                                    <select value={addTTL} onChange={e => setAddTTL(e.target.value)} className="w-full h-10 px-3 text-sm rounded-lg border border-border bg-bg-primary text-text-primary">
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
                                <div className={`grid gap-4 ${
                                    addType === 'SRV'
                                        ? 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4'
                                        : RECORD_TYPES[addType]?.fields?.length === 1 && RECORD_TYPES[addType].fields[0].textarea
                                            ? 'grid-cols-1'
                                            : 'grid-cols-1 sm:grid-cols-2'
                                }`}>
                                    {RECORD_TYPES[addType]?.fields.map((f) => {
                                        const fldList = RECORD_TYPES[addType]?.fields || []
                                        const oneTextareaOnly = fldList.length === 1 && fldList[0].textarea
                                        const textareaSpan = f.textarea
                                            ? (oneTextareaOnly && addType === 'TXT' ? 'sm:col-span-2' : oneTextareaOnly ? '' : 'sm:col-span-2 lg:col-span-4')
                                            : ''
                                        return (
                                        <div key={f.id} className={`min-w-0 ${textareaSpan}`}>
                                            <label className="block text-xs font-medium text-text-secondary mb-1">{f.labelKey ? t(f.labelKey) : f.label}</label>
                                            {f.select ? (
                                                <select
                                                    value={dynFields[f.id] || f.select[0]}
                                                    onChange={e => setDynFields({ ...dynFields, [f.id]: e.target.value })}
                                                    className="w-full h-10 px-3 text-sm rounded-lg border border-border bg-bg-primary"
                                                >
                                                    {f.select.map(o => <option key={o} value={o}>{o}</option>)}
                                                </select>
                                            ) : f.textarea ? (
                                                <textarea
                                                    value={dynFields[f.id] || ''}
                                                    onChange={e => setDynFields({ ...dynFields, [f.id]: e.target.value })}
                                                    placeholder={f.placeholderKey ? t(f.placeholderKey) : f.placeholder}
                                                    className="w-full min-h-[100px] rounded-lg border border-border bg-bg-primary px-3 py-2 text-sm font-mono text-[13px] placeholder:font-sans placeholder:text-sm"
                                                />
                                            ) : (
                                                <input
                                                    type={f.type || 'text'}
                                                    value={dynFields[f.id] || ''}
                                                    onChange={e => setDynFields({ ...dynFields, [f.id]: e.target.value })}
                                                    placeholder={f.placeholder}
                                                    className="w-full min-w-0 h-10 px-3 text-sm rounded-lg border border-border bg-bg-primary"
                                                />
                                            )}
                                        </div>
                                        )
                                    })}
                                </div>
                                <DnsRecordTypeHint recordType={addType} />
                            </div>

                            <div className="flex justify-end gap-3 pt-2 border-t border-border">
                                <button type="button" onClick={() => setShowAdd(false)} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary">{t('common.cancel')}</button>
                                <button type="submit" disabled={saving} className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                                    {saving && <Loader2 className="w-4 h-4 animate-spin" />} {t('common.save')}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    )
}
