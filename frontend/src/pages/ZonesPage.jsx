import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { Globe, Plus, Upload, Trash2, Loader2, Shield, AlertCircle, CheckCircle } from 'lucide-react'
import api from '../api'

export default function ZonesPage() {
    const { t } = useTranslation()
    const navigate = useNavigate()
    const [zones, setZones] = useState([])
    const [servers, setServers] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [showCreate, setShowCreate] = useState(false)
    /** Nach Zone erstellen: Pro-Server-Ergebnis (damit bei 2 echten Servern klar ist, was wo passiert ist) */
    const [createResult, setCreateResult] = useState(null)
    const defaultNS = (localStorage.getItem('defaultNameservers') || 'ns1.example.com.,ns2.example.com.').split(',').map(n => n.trim()).filter(Boolean)
    const [createForm, setCreateForm] = useState({ 
        name: '', 
        kind: 'Native', 
        nameservers: defaultNS.join(',\n'), 
        soa_edit_api: 'DEFAULT', 
        enable_dnssec: false 
    })
    const [creating, setCreating] = useState(false)
    const [user, setUser] = useState(() => api.getUser())
    const isAdmin = user?.role === 'admin'
    useEffect(() => {
        const u = api.getUser()
        if (u) setUser(u)
    }, [])

    // Templates
    const [templates, setTemplates] = useState([])
    const [selectedTemplateId, setSelectedTemplateId] = useState('')

    useEffect(() => { loadZones(); loadTemplates() }, [])

    async function loadTemplates() {
        try {
            const data = await api.getTemplates()
            const list = data.templates || []
            setTemplates(list)
            // Auto-select default template
            const def = list.find(t => t.is_default)
            if (def) {
                setSelectedTemplateId(String(def.id))
                applyTemplate(def)
            }
        } catch (err) { /* ignore */ }
    }

    function applyTemplate(t) {
        if (!t) return
        setCreateForm(prev => ({
            ...prev,
            nameservers: (t.nameservers || []).join(', '),
            kind: t.kind || 'Native',
            soa_edit_api: t.soa_edit_api || 'DEFAULT',
            enable_dnssec: false,
        }))
    }

    function handleTemplateChange(e) {
        const id = e.target.value
        setSelectedTemplateId(id)
        if (id === '') {
            // Keine Vorlage
            setCreateForm(prev => ({
                ...prev,
                nameservers: defaultNS.join(',\n'),
                kind: 'Native',
                soa_edit_api: 'DEFAULT',
            }))
            return
        }
        const t = templates.find(t => String(t.id) === id)
        if (t) applyTemplate(t)
    }

    async function loadZones() {
        try {
            const sData = await api.getServers()
            setServers(sData.servers || [])
            const allZones = []
            for (const s of (sData.servers || [])) {
                if (!s.is_reachable) continue
                try {
                    const zData = await api.listZones(s.name)
                        ; (zData.zones || []).forEach(z => allZones.push({ ...z, _server: s.name }))
                } catch { }
            }
            // Deduplicate
            const seen = new Set()
            setZones(allZones.filter(z => { if (seen.has(z.name)) return false; seen.add(z.name); return true }))
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    async function handleCreate(e) {
        e.preventDefault()
        setCreating(true)
        setError('')
        setCreateResult(null)

        const domainName = createForm.name.trim().toLowerCase().replace(/\.$/, '')
        const domainRegex = /^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$/
        if (!domainRegex.test(domainName)) {
            setError(`"${createForm.name.trim()}" ist kein gültiger Domainname. Beispiel: meinedomain.de`)
            setCreating(false)
            return
        }

        try {
            const rawNsArray = createForm.nameservers.split(/[\n,]+/).map(n => n.trim()).filter(Boolean)
            const nsArray = rawNsArray.map(ns => ns.endsWith('.') ? ns : `${ns}.`)

            const res = await api.createZone({
                ...createForm,
                nameservers: nsArray.length ? nsArray : defaultNS,
            })

            // Pro-Server-Ergebnis anzeigen (wichtig bei 2 echten Servern oder 1 DB mit 2 Einträgen)
            setCreateResult(res || {})

            const details = (res && res.details) ? res.details : {}
            const hasError = Object.values(details).some(v => String(v).startsWith('error:'))
            if (hasError) {
                const errParts = Object.entries(details)
                    .filter(([, v]) => String(v).startsWith('error:'))
                    .map(([srv, v]) => `${srv}: ${String(v).replace(/^error:\s*/, '')}`)
                setError(errParts.length ? errParts.join(' · ') : 'Ein Server hat einen Fehler gemeldet.')
            }

            // Template-Records nur wenn mindestens ein Server die Zone erstellt hat
            const selectedTemplate = templates.find(t => String(t.id) === selectedTemplateId)
            if (selectedTemplate && (selectedTemplate.records || []).length > 0 && !hasError) {
                const zoneName = createForm.name.trim().toLowerCase()
                const zoneNameDot = zoneName.endsWith('.') ? zoneName : `${zoneName}.`
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
                const defTemplate = templates.find(t => t.is_default)
                setCreateForm({
                    name: '',
                    kind: defTemplate?.kind || 'Native',
                    nameservers: (defTemplate?.nameservers || defaultNS).join(',\n'),
                    soa_edit_api: defTemplate?.soa_edit_api || 'DEFAULT',
                    enable_dnssec: false,
                })
                setCreateResult(null)
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

    async function handleDelete(server, zone) {
        if (!confirm(t('zones.deleteZoneConfirm', { zone }))) return
        try {
            await api.deleteZone(server, zone)
            loadZones()
        } catch (err) {
            setError(err.message)
        }
    }

    if (loading) return <div className="flex items-center justify-center h-64"><Loader2 className="w-8 h-8 text-accent animate-spin" /></div>

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-text-primary">{isAdmin ? t('zones.title') : t('zones.myZones')}</h1>
                    <p className="text-text-muted text-sm mt-1">{t('zones.zonesCount', { count: zones.length })}</p>
                </div>
                {isAdmin && (
                    <button
                        onClick={() => setShowCreate(true)}
                        className="flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all"
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

            {/* Zone table */}
            <div className="glass-card overflow-hidden">
                <table className="w-full text-sm">
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

            {/* Create Zone Modal */}
            {showCreate && (
                <div
                    className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
                    onClick={closeCreateModal}
                >
                    <div className="glass-card p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                        <h2 className="text-lg font-bold text-text-primary mb-4">{t('zones.createZone')}</h2>

                        {/* Fehlermeldung im Modal */}
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

                        {/* Pro-Server-Ergebnis: bei 2 Servern / 2 DBs sieht man genau, was wo passiert ist */}
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

                        <form onSubmit={handleCreate} className="space-y-4">
                            {/* Template Selector */}
                            {templates.length > 0 && (
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('zones.template')}</label>
                                    <select
                                        value={selectedTemplateId}
                                        onChange={handleTemplateChange}
                                        className="w-full px-3 py-2 text-sm"
                                    >
                                        <option value="">{t('zones.noTemplate')}</option>
                                        {templates.map(t => (
                                            <option key={t.id} value={t.id}>
                                                {t.name} {t.is_default ? '⭐' : ''} {(t.records || []).length > 0 ? `(${(t.records || []).length} Records)` : ''}
                                            </option>
                                        ))}
                                    </select>
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
                                    placeholder="example.com"
                                    className="w-full px-3 py-2 text-sm"
                                    required
                                    autoFocus
                                />
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
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('templates.nameservers')}</label>
                                <textarea
                                    value={createForm.nameservers}
                                    onChange={(e) => setCreateForm({ ...createForm, nameservers: e.target.value })}
                                    className="w-full px-3 py-2 text-sm min-h-[60px]"
                                    placeholder="ns1.example.com., ns2.example.com."
                                ></textarea>
                                <p className="text-xs text-text-muted mt-1">{t('zones.nameserversHintCreate')} <a href="/settings" className="text-accent hover:underline">{t('zones.defaultInSettings')}</a>.</p>
                            </div>
                            <label className="flex items-center gap-2 cursor-pointer">
                                <input
                                    type="checkbox"
                                    checked={createForm.enable_dnssec}
                                    onChange={(e) => setCreateForm({ ...createForm, enable_dnssec: e.target.checked })}
                                    className="w-4 h-4 rounded"
                                />
                                <span className="text-sm text-text-secondary">{t('zones.enableDnssec')}</span>
                            </label>
                            <div className="flex justify-end gap-3 pt-2">
                                <button type="button" onClick={closeCreateModal} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary transition-colors">
                                    {t('common.cancel')}
                                </button>
                                <button
                                    type="submit"
                                    disabled={creating}
                                    className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                                >
                                    {creating && <Loader2 className="w-4 h-4 animate-spin" />}
                                    {t('settings.create')}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    )
}
