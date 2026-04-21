import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import {
    Globe, Plus, Trash2, Loader2, Shield, AlertCircle, CheckCircle, X
} from 'lucide-react'
import api from '../api'

/* ============================================================================
 *  Helpers für die Zone-Erstellung
 * ========================================================================== */

/** Putzt typische Eingaben wie "https://www.example.com/path" → "example.com". */
function cleanupDomainInput(input) {
    let s = (input || '').trim().toLowerCase()
    if (!s) return ''
    s = s.replace(/^[a-z]+:\/\//, '')      // Protokoll
    s = s.split('/')[0]                    // Pfad
    s = s.split('?')[0]
    s = s.split('#')[0]
    s = s.split(':')[0]                    // Port
    s = s.replace(/^www\./, '')            // www-Prefix
    s = s.replace(/\.+$/, '')              // trailing dot(s)
    return s
}

const DOMAIN_RE = /^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$/
const REVERSE_SUFFIX_RE = /\.(in-addr|ip6)\.arpa$/i

/** Liefert {error, hint, reverse} für eine eingegebene Domain. `t` ist die i18next-Translate-Funktion. */
function describeDomain(input, t) {
    const reverse = parseCidrToArpa(input)
    if (reverse) return { hint: t('zones.reverseZoneCreatedAs', { zone: reverse }), reverseZone: reverse }

    const cleaned = cleanupDomainInput(input)
    if (!cleaned) return { error: t('zones.enterDomainName') }
    if (REVERSE_SUFFIX_RE.test(cleaned)) {
        return { hint: t('zones.reverseZoneDetected') }
    }
    if (!DOMAIN_RE.test(cleaned)) {
        return { error: t('zones.invalidDomainName', { name: cleaned }) }
    }
    return { hint: t('zones.willBeCreatedAs', { name: cleaned }) }
}

/** "192.168.1.0/24" → "1.168.192.in-addr.arpa". Andere CIDRs → null. */
function parseCidrToArpa(input) {
    const s = (input || '').trim()
    const m = s.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\/(\d{1,2})$/)
    if (!m) return null
    const [, a, b, c, , prefix] = m
    const p = parseInt(prefix, 10)
    if ([a, b, c].some(x => parseInt(x, 10) > 255)) return null
    if (p === 24) return `${m[3]}.${b}.${a}.in-addr.arpa`
    if (p === 16) return `${b}.${a}.in-addr.arpa`
    if (p === 8) return `${a}.in-addr.arpa`
    return null
}

/** Validiert einen einzelnen Nameserver-Eintrag. `t` ist die i18next-Translate-Funktion. */
function validateNameserver(ns, t) {
    if (!ns) return ''
    const s = ns.trim().replace(/\.$/, '')
    if (!DOMAIN_RE.test(s)) return t('zones.invalidNameserver')
    return ''
}

/* ============================================================================
 *  ZonesPage
 * ========================================================================== */

export default function ZonesPage() {
    const { t } = useTranslation()
    const navigate = useNavigate()
    const [zones, setZones] = useState([])
    const [_servers, setServers] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [success, setSuccess] = useState('')
    const [showCreate, setShowCreate] = useState(false)
    const [createResult, setCreateResult] = useState(null)
    const defaultNS = (localStorage.getItem('defaultNameservers') || 'ns1.example.com.,ns2.example.com.').split(',').map(n => n.trim()).filter(Boolean)
    const [createForm, setCreateForm] = useState({
        name: '',
        kind: 'Native',
        soa_edit_api: 'DEFAULT',
        enable_dnssec: false,
    })
    /** Nameserver als Array – jede Zeile ein Eintrag. */
    const [nameservers, setNameservers] = useState(defaultNS.slice())
    const [creating, setCreating] = useState(false)
    const [user, setUser] = useState(() => api.getUser())
    const isAdmin = user?.role === 'admin'
    const formRef = useRef(null)

    useEffect(() => {
        const u = api.getUser()
        // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time mount sync from localStorage
        if (u) setUser(u)
    }, [])

    const [templates, setTemplates] = useState([])
    const [selectedTemplateId, setSelectedTemplateId] = useState('')

    useEffect(() => { loadZones(); loadTemplates() }, []) // eslint-disable-line react-hooks/exhaustive-deps -- run once on mount

    useEffect(() => {
        if (!success) return
        const timer = setTimeout(() => setSuccess(''), 4000)
        return () => clearTimeout(timer)
    }, [success])

    /** ESC schließt Modal, Strg/Cmd+Enter speichert */
    useEffect(() => {
        if (!showCreate) return
        function onKey(e) {
            if (e.key === 'Escape') {
                if (!creating) closeCreateModal()
            } else if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault()
                if (formRef.current && !creating) formRef.current.requestSubmit()
            }
        }
        document.addEventListener('keydown', onKey)
        return () => document.removeEventListener('keydown', onKey)
    }, [showCreate, creating])  

    async function loadTemplates() {
        try {
            const data = await api.getTemplates()
            const list = data.templates || []
            setTemplates(list)
            const def = list.find(tpl => tpl.is_default)
            if (def) {
                setSelectedTemplateId(String(def.id))
                applyTemplate(def)
            }
        } catch { /* ignore */ }
    }

    function applyTemplate(tpl) {
        if (!tpl) return
        setCreateForm(prev => ({
            ...prev,
            kind: tpl.kind || 'Native',
            soa_edit_api: tpl.soa_edit_api || 'DEFAULT',
            enable_dnssec: false,
        }))
        const ns = (tpl.nameservers || []).filter(Boolean)
        setNameservers(ns.length ? ns : defaultNS.slice())
    }

    function handleTemplateChange(e) {
        const id = e.target.value
        setSelectedTemplateId(id)
        if (id === '') {
            setCreateForm(prev => ({
                ...prev,
                kind: 'Native',
                soa_edit_api: 'DEFAULT',
            }))
            setNameservers(defaultNS.slice())
            return
        }
        const tpl = templates.find(tt => String(tt.id) === id)
        if (tpl) applyTemplate(tpl)
    }

    async function loadZones() {
        try {
            const sData = await api.getServers()
            setServers(sData.servers || [])
            const reachable = (sData.servers || []).filter(s => s.is_reachable)
            const results = await Promise.all(reachable.map(async (s) => {
                try {
                    const zData = await api.listZones(s.name)
                    return (zData.zones || []).map(z => ({ ...z, _server: s.name }))
                } catch {
                    return []
                }
            }))
            const allZones = results.flat()
            const seen = new Set()
            setZones(allZones.filter(z => { if (seen.has(z.name)) return false; seen.add(z.name); return true }))
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    /* ----- Submit -----------------------------------------------------------*/
    async function handleCreate(e) {
        e.preventDefault()
        if (creating) return
        setCreating(true)
        setError('')
        setCreateResult(null)

        const rawInput = createForm.name.trim()
        const reverseFromCidr = parseCidrToArpa(rawInput)
        const cleaned = reverseFromCidr || cleanupDomainInput(rawInput)

        if (!cleaned) {
            setError(t('zones.enterDomainName'))
            setCreating(false)
            return
        }
        if (!REVERSE_SUFFIX_RE.test(cleaned) && !DOMAIN_RE.test(cleaned)) {
            setError(t('zones.invalidDomainName', { name: rawInput }))
            setCreating(false)
            return
        }

        // Nameserver: leere ignorieren, Trailing-Dot anhängen
        const nsArray = nameservers
            .map(n => n.trim())
            .filter(Boolean)
            .map(n => n.endsWith('.') ? n : `${n}.`)

        // Validiere jeden NS einzeln
        for (const ns of nsArray) {
            if (validateNameserver(ns, t)) {
                setError(t('zones.invalidNameserverNamed', { name: ns }))
                setCreating(false)
                return
            }
        }

        try {
            const res = await api.createZone({
                ...createForm,
                name: cleaned,
                nameservers: nsArray.length ? nsArray : defaultNS,
            })
            setCreateResult(res || {})

            const details = (res && res.details) ? res.details : {}
            const hasError = Object.values(details).some(v => String(v).startsWith('error:'))
            if (hasError) {
                const errParts = Object.entries(details)
                    .filter(([, v]) => String(v).startsWith('error:'))
                    .map(([srv, v]) => `${srv}: ${String(v).replace(/^error:\s*/, '')}`)
                setError(errParts.length ? errParts.join(' · ') : t('zones.serverReportedError'))
            }

            // Template-Records (nur ohne Server-Fehler)
            const selectedTemplate = templates.find(tpl => String(tpl.id) === selectedTemplateId)
            if (selectedTemplate && (selectedTemplate.records || []).length > 0 && !hasError) {
                const zoneNameDot = cleaned.endsWith('.') ? cleaned : `${cleaned}.`
                const sData = await api.getServers()
                const srv = (sData.servers || []).find(s => s.is_reachable)
                if (srv) {
                    for (const rec of selectedTemplate.records) {
                        try {
                            const recName = rec.name === '@' ? zoneNameDot : `${rec.name}.${zoneNameDot}`
                            let content = rec.content
                            if (rec.type === 'MX' && rec.prio != null) content = `${rec.prio} ${content}`
                            await api.createRecord(srv.name, zoneNameDot, {
                                name: recName,
                                type: rec.type,
                                ttl: rec.ttl || selectedTemplate.default_ttl || 3600,
                                records: [{ content, disabled: false }],
                            })
                        } catch (recErr) { console.warn('Template record failed:', recErr) }
                    }
                }
            }

            if (!hasError) {
                setShowCreate(false)
                const defTemplate = templates.find(tpl => tpl.is_default)
                setCreateForm({
                    name: '',
                    kind: defTemplate?.kind || 'Native',
                    soa_edit_api: defTemplate?.soa_edit_api || 'DEFAULT',
                    enable_dnssec: false,
                })
                setNameservers(defTemplate?.nameservers?.length ? defTemplate.nameservers.slice() : defaultNS.slice())
                setCreateResult(null)
                setSuccess(t('zones.createdSuccess', { zone: cleaned }))
                loadZones()
            }
        } catch (err) {
            setError(err.message)
        } finally {
            setCreating(false)
        }
    }

    function closeCreateModal() {
        setShowCreate(false)
        setError('')
        setCreateResult(null)
    }

    function openCreateModal() {
        setError('')
        setCreateResult(null)
        const def = templates.find(tt => tt.is_default)
        setCreateForm({
            name: '',
            kind: def?.kind || 'Native',
            soa_edit_api: def?.soa_edit_api || 'DEFAULT',
            enable_dnssec: false,
        })
        setNameservers(def?.nameservers?.length ? def.nameservers.slice() : defaultNS.slice())
        setSelectedTemplateId(def ? String(def.id) : '')
        setShowCreate(true)
    }

    async function handleDelete(server, zone) {
        if (!confirm(t('zones.deleteZoneConfirm', { zone }))) return
        try {
            await api.deleteZone(server, zone)
            setSuccess(t('zones.deletedSuccess', { zone: zone.replace(/\.$/, '') }))
            loadZones()
        } catch (err) {
            setError(err.message)
        }
    }

    if (loading) return <div className="flex items-center justify-center h-64"><Loader2 className="w-8 h-8 text-accent animate-spin" /></div>

    /* ----- abgeleiteter Zustand fürs Modal ----------------------------------*/
    const domainInfo = describeDomain(createForm.name, t)
    const selectedTemplate = templates.find(tt => String(tt.id) === selectedTemplateId)

    return (
        <div className="space-y-6">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-text-primary">{isAdmin ? t('zones.title') : t('zones.myZones')}</h1>
                    <p className="text-text-muted text-sm mt-1">{t('zones.zonesCount', { count: zones.length })}</p>
                </div>
                {isAdmin && (
                    <button
                        onClick={openCreateModal}
                        className="self-start sm:self-auto flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all"
                    >
                        <Plus className="w-4 h-4" /> {t('zones.newZone')}
                    </button>
                )}
            </div>

            {error && (
                <div className="p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-center gap-3">
                    <AlertCircle className="w-5 h-5 shrink-0" />
                    <p className="text-sm">{error}</p>
                    <button onClick={() => setError('')} className="ml-auto text-xs hover:underline">{t('zones.close')}</button>
                </div>
            )}

            {success && (
                <div className="p-4 rounded-xl bg-success/10 border border-success/30 text-success flex items-center gap-3">
                    <CheckCircle className="w-5 h-5 shrink-0" />
                    <p className="text-sm flex-1">{success}</p>
                    <button onClick={() => setSuccess('')} className="text-xs hover:underline" aria-label={t('common.close')}>×</button>
                </div>
            )}

            {/* Zone table */}
            <div className="glass-card overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-sm min-w-[760px]">
                    <thead>
                        <tr className="border-b border-border">
                            <th className="text-left p-4 text-text-muted font-medium">{t('zones.zone')}</th>
                            <th className="text-left p-4 text-text-muted font-medium">{t('dashboard.type')}</th>
                            <th className="text-left p-4 text-text-muted font-medium">{t('zones.serial')}</th>
                            <th className="text-left p-4 text-text-muted font-medium">{t('zones.dnssec')}</th>
                            <th className="text-left p-4 text-text-muted font-medium">{t('audit.server')}</th>
                            <th className="text-right p-4 text-text-muted font-medium">{t('zones.actions')}</th>
                        </tr>
                    </thead>
                    <tbody>
                        {zones.map(z => (
                            <tr
                                key={z.name}
                                className="border-b border-border/50 hover:bg-bg-hover/50 cursor-pointer transition-colors"
                                onClick={() => navigate(`/zones/${z._server}/${z.name}`)}
                            >
                                <td className="p-4">
                                    <div className="flex items-center gap-2">
                                        <Globe className="w-4 h-4 text-accent-light" />
                                        <span className="font-medium text-text-primary">{z.name.replace(/\.$/, '')}</span>
                                    </div>
                                </td>
                                <td className="p-4 text-text-secondary">{z.kind}</td>
                                <td className="p-4 text-text-secondary font-mono text-xs">{z.serial}</td>
                                <td className="p-4">
                                    {z.dnssec
                                        ? <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 bg-success/10 text-success rounded-full border border-success/30"><Shield className="w-3 h-3" /> {t('zones.dnssecOn')}</span>
                                        : <span className="text-xs text-text-muted">{t('zones.dnssecOff')}</span>
                                    }
                                </td>
                                <td className="p-4 text-text-secondary">{z._server}</td>
                                <td className="p-4 text-right">
                                    {isAdmin && (
                                        <button
                                            onClick={(e) => { e.stopPropagation(); handleDelete(z._server, z.name) }}
                                            className="p-1.5 rounded-lg text-text-muted hover:text-danger hover:bg-danger/10 transition-colors"
                                            title={t('zones.deleteZone')}
                                        >
                                            <Trash2 className="w-4 h-4" />
                                        </button>
                                    )}
                                </td>
                            </tr>
                        ))}
                        {zones.length === 0 && (
                            <tr><td colSpan={6} className="p-12 text-center text-text-muted">
                                <Globe className="w-12 h-12 mx-auto mb-3 opacity-30" />
                                <p>{t('zones.noZonesYet')}</p>
                            </td></tr>
                        )}
                    </tbody>
                </table>
              </div>
            </div>

            {/* Create Zone Modal */}
            {showCreate && (
                <div
                    className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
                    onClick={() => { if (!creating) closeCreateModal() }}
                >
                    <div className="glass-card p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                        <div className="flex items-start justify-between mb-4">
                            <h2 className="text-lg font-bold text-text-primary">{t('zones.createZone')}</h2>
                            <button
                                type="button"
                                onClick={() => { if (!creating) closeCreateModal() }}
                                className="p-1 rounded hover:bg-bg-hover text-text-muted hover:text-text-primary transition-colors"
                                title={t('common.close')}
                            >
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        {error && (
                            <div className="mb-4 p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-start gap-3">
                                <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium">{t('zones.createError')}</p>
                                    <p className="text-sm mt-1">{error}</p>
                                </div>
                                <button type="button" onClick={() => setError('')} className="text-xs hover:underline shrink-0" aria-label="Meldung schließen">{t('zones.close')}</button>
                            </div>
                        )}

                        {createResult?.details && Object.keys(createResult.details).length > 0 && (
                            <div className="mb-4 p-4 rounded-xl bg-bg-hover/50 border border-border">
                                <p className="text-sm font-medium text-text-primary mb-2">{t('zones.resultPerServer')}</p>
                                <ul className="space-y-1.5 text-sm">
                                    {Object.entries(createResult.details).map(([srv, status]) => {
                                        const isError = String(status).startsWith('error:')
                                        const msg = String(status).replace(/^error:\s*/, '')
                                        return (
                                            <li key={srv} className="flex items-center gap-2">
                                                {isError ? (
                                                    <AlertCircle className="w-4 h-4 text-danger shrink-0" />
                                                ) : (
                                                    <CheckCircle className="w-4 h-4 text-success shrink-0" />
                                                )}
                                                <span className="text-text-secondary">{srv}:</span>
                                                <span className={isError ? 'text-danger' : 'text-text-primary'}>
                                                    {isError ? msg : (status === 'created' ? t('zones.created') : status === 'synced' ? t('zones.synced') : status)}
                                                </span>
                                            </li>
                                        )
                                    })}
                                </ul>
                                <button type="button" onClick={closeCreateModal} className="mt-3 text-xs text-accent hover:underline">{t('zones.doneClose')}</button>
                            </div>
                        )}

                        <form ref={formRef} onSubmit={handleCreate} className="space-y-4">
                            {/* Template Selector + Vorschau */}
                            {templates.length > 0 && (
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('zones.template')}</label>
                                    <select
                                        value={selectedTemplateId}
                                        onChange={handleTemplateChange}
                                        className="w-full px-3 py-2 text-sm"
                                    >
                                        <option value="">{t('zones.noTemplate')}</option>
                                        {templates.map(tt => (
                                            <option key={tt.id} value={tt.id}>
                                                {tt.name} {tt.is_default ? '⭐' : ''} {(tt.records || []).length > 0 ? `(${(tt.records || []).length} Records)` : ''}
                                            </option>
                                        ))}
                                    </select>
                                    {selectedTemplate && (selectedTemplate.records || []).length > 0 && (
                                        <div className="mt-2 rounded-lg border border-accent/20 bg-accent/5 p-3">
                                            <p className="text-xs font-medium text-text-secondary mb-1.5">{t('zones.templatePreviewTitle')}</p>
                                            <ul className="space-y-1 text-xs font-mono">
                                                {selectedTemplate.records.slice(0, 8).map((rec, i) => (
                                                    <li key={i} className="text-text-secondary">
                                                        <span className="inline-block min-w-[3.5rem] text-accent-light">{rec.type}</span>
                                                        <span className="text-text-primary">{rec.name === '@' ? '@' : `${rec.name}`}</span>
                                                        <span className="text-text-muted"> → </span>
                                                        <span>{rec.type === 'MX' && rec.prio != null ? `${rec.prio} ${rec.content}` : rec.content}</span>
                                                    </li>
                                                ))}
                                                {selectedTemplate.records.length > 8 && (
                                                    <li className="text-text-muted">…{t('zones.templateMoreRecords', { count: selectedTemplate.records.length - 8 })}</li>
                                                )}
                                            </ul>
                                        </div>
                                    )}
                                    <p className="text-xs text-text-muted mt-1">
                                        {t('zones.templateHint')}{' '}
                                        <a href="/settings" className="text-accent hover:underline">{t('zones.manageTemplates')}</a>
                                    </p>
                                </div>
                            )}

                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('zones.domain')}</label>
                                <input
                                    type="text"
                                    value={createForm.name}
                                    onChange={(e) => setCreateForm({ ...createForm, name: e.target.value })}
                                    onBlur={(e) => {
                                        if (parseCidrToArpa(e.target.value)) return
                                        const cleaned = cleanupDomainInput(e.target.value)
                                        if (cleaned && cleaned !== e.target.value.trim().toLowerCase()) {
                                            setCreateForm(prev => ({ ...prev, name: cleaned }))
                                        }
                                    }}
                                    placeholder="example.com  oder  192.168.1.0/24"
                                    className="w-full px-3 py-2 text-sm"
                                    required
                                    autoFocus
                                    autoComplete="off"
                                    spellCheck={false}
                                />
                                {createForm.name.trim() !== '' && (
                                    domainInfo.error
                                        ? <p className="mt-1 text-xs text-danger flex items-center gap-1"><AlertCircle className="w-3 h-3" /> {domainInfo.error}</p>
                                        : <p className="mt-1 text-xs text-success flex items-center gap-1"><CheckCircle className="w-3 h-3" /> {domainInfo.hint}</p>
                                )}
                                <p className="mt-1 text-xs text-text-muted">{t('zones.domainCleanupHint')}</p>
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('dashboard.type')}</label>
                                    <select
                                        value={createForm.kind}
                                        onChange={(e) => setCreateForm({ ...createForm, kind: e.target.value })}
                                        className="w-full px-3 py-2 text-sm"
                                    >
                                        <option value="Native">{t('zones.nativeRecommended')}</option>
                                        <option value="Master">Master</option>
                                        <option value="Slave">Slave</option>
                                    </select>
                                    <p className="text-xs text-text-muted mt-1 leading-relaxed">
                                        <strong className="text-text-secondary">Native:</strong> {t('zones.typeHintNative')}<br/>
                                        <strong className="text-text-secondary">Master/Slave:</strong> {t('zones.typeHintMasterSlave')}
                                    </p>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">SOA-EDIT-API</label>
                                    <select
                                        value={createForm.soa_edit_api}
                                        onChange={(e) => setCreateForm({ ...createForm, soa_edit_api: e.target.value })}
                                        className="w-full px-3 py-2 text-sm"
                                    >
                                        <option value="DEFAULT">DEFAULT</option>
                                        <option value="INCEPTION-INCREMENT">INCEPTION-INCREMENT</option>
                                        <option value="EPOCH">EPOCH</option>
                                    </select>
                                    <p className="text-xs text-text-muted mt-1 leading-relaxed">
                                        {t('zones.soaEditQuestion')}<br/>
                                        <strong className="text-text-secondary">DEFAULT:</strong> {t('zones.soaEditDefault')}<br/>
                                        <strong className="text-text-secondary">INCEPTION-INCREMENT:</strong> {t('zones.soaEditInception')}<br/>
                                        <strong className="text-text-secondary">EPOCH:</strong> {t('zones.soaEditEpoch')}
                                    </p>
                                </div>
                            </div>

                            {/* Nameserver-Liste */}
                            <div>
                                <div className="flex items-center justify-between mb-1">
                                    <label className="block text-sm font-medium text-text-secondary">{t('templates.nameservers')}</label>
                                    <button
                                        type="button"
                                        onClick={() => setNameservers(list => [...list, ''])}
                                        className="text-xs flex items-center gap-1 text-accent hover:underline"
                                    >
                                        <Plus className="w-3 h-3" /> {t('zones.addNameserver')}
                                    </button>
                                </div>
                                <div className="space-y-2">
                                    {nameservers.map((ns, idx) => {
                                        const err = validateNameserver(ns, t)
                                        return (
                                            <div key={idx}>
                                                <div className="flex gap-2 items-start">
                                                    <input
                                                        type="text"
                                                        value={ns}
                                                        onChange={(e) => setNameservers(list => list.map((v, i) => i === idx ? e.target.value : v))}
                                                        placeholder={`ns${idx + 1}.example.com`}
                                                        className={`flex-1 px-3 py-2 text-sm ${ns.trim() && err ? 'border-danger/60' : ''}`}
                                                        autoComplete="off"
                                                        spellCheck={false}
                                                    />
                                                    {nameservers.length > 1 && (
                                                        <button
                                                            type="button"
                                                            onClick={() => setNameservers(list => list.filter((_, i) => i !== idx))}
                                                            className="p-2 rounded text-text-muted hover:text-danger hover:bg-danger/10 transition-colors"
                                                            title={t('zones.removeNameserver')}
                                                        >
                                                            <Trash2 className="w-4 h-4" />
                                                        </button>
                                                    )}
                                                </div>
                                                {ns.trim() && err && (
                                                    <p className="mt-1 text-xs text-danger flex items-center gap-1"><AlertCircle className="w-3 h-3" /> {err}</p>
                                                )}
                                            </div>
                                        )
                                    })}
                                </div>
                                <p className="text-xs text-text-muted mt-1">
                                    {t('zones.nameserverListHint')}{' '}
                                    <a href="/settings" className="text-accent hover:underline">{t('zones.defaultInSettings')}</a>.
                                </p>
                            </div>

                            <div>
                                <label className="flex items-start gap-2 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={createForm.enable_dnssec}
                                        onChange={(e) => setCreateForm({ ...createForm, enable_dnssec: e.target.checked })}
                                        className="w-4 h-4 rounded mt-0.5"
                                    />
                                    <div>
                                        <span className="text-sm text-text-secondary">{t('zones.enableDnssec')}</span>
                                        {createForm.enable_dnssec && (
                                            <div className="mt-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-100">
                                                <p className="font-medium mb-1 flex items-center gap-1.5">
                                                    <Shield className="w-3.5 h-3.5" />
                                                    {t('zones.dnssecRegistrarTitle')}
                                                </p>
                                                <p className="text-amber-100/90 leading-snug">{t('zones.dnssecRegistrarBody')}</p>
                                            </div>
                                        )}
                                    </div>
                                </label>
                            </div>

                            <div className="flex justify-between items-center gap-3 pt-2 border-t border-border">
                                <p className="text-xs text-text-muted hidden sm:block">{t('zoneDetail.kbdHint')}</p>
                                <div className="flex justify-end gap-3">
                                    <button type="button" onClick={closeCreateModal} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary transition-colors">
                                        {t('common.cancel')}
                                    </button>
                                    <button
                                        type="submit"
                                        disabled={creating || !!domainInfo.error}
                                        className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                                    >
                                        {creating && <Loader2 className="w-4 h-4 animate-spin" />}
                                        {t('settings.create')}
                                    </button>
                                </div>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    )
}
