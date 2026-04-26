import { useState, useEffect, useRef, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate } from 'react-router-dom'
import {
    ArrowLeft, Plus, Trash2, Pencil, Loader2, AlertCircle, CheckCircle, Globe,
    Copy, X, Sparkles, Shield
} from 'lucide-react'
import api from '../api'
import { ALL_RECORD_TYPE_KEYS } from '../constants/dnsRecordTypes'
import DnsRecordTypeHint from '../components/DnsRecordTypeHint'
import ZoneDnssecRegistrarCard from '../components/ZoneDnssecRegistrarCard'
import {
    FIELD_VALIDATORS, getApexWarning, buildQuickTemplates, RECORD_TYPES, MULTI_VALUE_OK,
} from '../zoneDetail/zoneDetailModel'

export default function ZoneDetailPage() {
    const { t } = useTranslation()
    const { server, zoneId } = useParams()
    const navigate = useNavigate()
    const [_zone, setZone] = useState(null)
    /** Metadaten von GET /zones/.../detail (u. a. dnssec: boolean) */
    const [zoneMeta, setZoneMeta] = useState(null)
    /** Antwort von GET /dnssec/.../ds */
    const [dsData, setDsData] = useState(null)
    const [dsLoading, setDsLoading] = useState(false)
    const [dsError, setDsError] = useState('')
    const [enablingDnssec, setEnablingDnssec] = useState(false)
    const [disablingDnssec, setDisablingDnssec] = useState(false)
    /** „DS / Registrar“-Assistent: erklärt 3× DS + einzelne Felder zum Kopieren */
    const [showDsModal, setShowDsModal] = useState(false)
    const [records, setRecords] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [modalError, setModalError] = useState('')
    const [success, setSuccess] = useState('')
    const [showAdd, setShowAdd] = useState(false)
    const [isEdit, setIsEdit] = useState(false)
    const [oldContent, setOldContent] = useState('')
    const [addType, setAddType] = useState('A')
    const [addName, setAddName] = useState('@')
    const [addTTL, setAddTTL] = useState('3600')
    /** Liste von Wert-Sets (für Round-Robin). Beim Edit immer genau ein Set. */
    const [dynFieldsList, setDynFieldsList] = useState([{}])
    const [saving, setSaving] = useState(false)
    /** Ist beim Submit aktiv – verhindert nochmaliges Senden bei Strg+Enter */
    const formRef = useRef(null)

    const [me, setMe] = useState(() => api.getUser())
    const [allServers, setAllServers] = useState([])
    useEffect(() => {
        api.getMe().then((u) => { api.setUser(u); setMe(u) }).catch(() => {})
    }, [])
    useEffect(() => {
        api.getServers().then((d) => setAllServers(d.servers || [])).catch(() => {})
    }, [])

    const currentServerInfo = useMemo(
        () => allServers.find((s) => s.name === server) || null,
        [allServers, server]
    )
    const otherWritableServers = useMemo(
        () => allServers.filter((s) => s.name !== server && s.allow_writes !== false && s.is_reachable),
        [allServers, server]
    )
    const serverCanWrite = currentServerInfo ? currentServerInfo.allow_writes !== false : true

    useEffect(() => {
        if (!success) return
        const timer = setTimeout(() => setSuccess(''), 4000)
        return () => clearTimeout(timer)
    }, [success])

    function closeModal() {
        setShowAdd(false)
        setModalError('')
        setIsEdit(false)
        setOldContent('')
        setDynFieldsList([{}])
    }

    function openAddModal() {
        setIsEdit(false)
        setAddType('A')
        setAddName('@')
        setAddTTL('3600')
        setDynFieldsList([{}])
        setModalError('')
        setShowAdd(true)
    }

    /** Bestehenden Record klonen → öffnet Add-Modal mit denselben Werten, aber ohne oldContent. */
    function openClone(record) {
        setIsEdit(false)
        setOldContent('')
        setAddType(record.type)
        setAddTTL(record.ttl.toString())
        setModalError('')

        let name = record.name.replace(/\.$/, '')
        if (name === zoneName) name = '@'
        else if (name.endsWith(`.${zoneName}`)) name = name.substring(0, name.length - zoneName.length - 1)
        setAddName(name)

        const def = RECORD_TYPES[record.type] || { parse: () => ({}) }
        try { setDynFieldsList([def.parse ? def.parse(record.content) : {}]) } catch { setDynFieldsList([{}]) }
        setShowAdd(true)
    }

    /** Schnellvorlage einfügen */
    function applyQuickTemplate(tpl) {
        setIsEdit(false)
        setOldContent('')
        setAddType(tpl.type)
        setAddName(tpl.name)
        setAddTTL(tpl.ttl)
        setDynFieldsList([{ ...tpl.fields }])
        setModalError('')
        setShowAdd(true)
    }

    const zoneName = zoneId.replace(/\.$/, '')
    const zoneKey = useMemo(() => {
        let z = (zoneId || '').trim().toLowerCase()
        if (!z) return ''
        return z.endsWith('.') ? z : `${z}.`
    }, [zoneId])
    const quickTemplates = useMemo(() => buildQuickTemplates(zoneName), [zoneName])

    /** Bevorzugte DS-Zeile: Digest-Typ 2 (SHA-256) – so will es der Großteil der Registrar-Formulare. */
    const recommendedDsRow = useMemo(() => {
        const rows = dsData?.ds_records || []
        const ok = (r) => r.parsed && !r.parsed.error
        return rows.find((r) => ok(r) && (r.parsed.recommended || r.parsed.digest_type === 2)) || rows.find(ok) || null
    }, [dsData])

    useEffect(() => { loadZone() }, [server, zoneId]) // eslint-disable-line react-hooks/exhaustive-deps -- loadZone stable

    /** ESC schließt Modal, Strg/Cmd+Enter speichert */
    useEffect(() => {
        if (!showAdd) return
        function onKey(e) {
            if (e.key === 'Escape') {
                if (!saving) closeModal()
            } else if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault()
                if (formRef.current && !saving) formRef.current.requestSubmit()
            }
        }
        document.addEventListener('keydown', onKey)
        return () => document.removeEventListener('keydown', onKey)
    }, [showAdd, saving])  

    async function loadZone() {
        setLoading(true)
        setDsError('')
        try {
            const data = await api.listRecords(server, zoneId)
            setRecords(data.records || [])
            setZone(data)

            let meta = null
            try {
                meta = await api.getZone(server, zoneId)
                setZoneMeta(meta)
            } catch {
                setZoneMeta(null)
            }

            if (meta?.dnssec) {
                setDsLoading(true)
                try {
                    const ds = await api.getDsRecords(server, zoneId)
                    setDsData(ds)
                } catch (e) {
                    setDsData(null)
                    setDsError(e.message || String(e))
                } finally {
                    setDsLoading(false)
                }
            } else {
                setDsData(null)
                setDsLoading(false)
            }
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    async function handleEnableDnssec() {
        if (!window.confirm(t('zoneDetail.dnssecEnableConfirm'))) return
        setEnablingDnssec(true)
        setDsError('')
        try {
            await api.enableDNSSEC(server, zoneId, {})
            setSuccess(t('zoneDetail.dnssecEnabledOk'))
            await loadZone()
        } catch (err) {
            setError(err.message)
        } finally {
            setEnablingDnssec(false)
        }
    }

    async function handleDisableDnssec() {
        if (!window.confirm(t('zoneDetail.dnssecDisableConfirm'))) return
        setDisablingDnssec(true)
        setDsError('')
        try {
            await api.disableDNSSEC(server, zoneId)
            setShowDsModal(false)
            setSuccess(t('zoneDetail.dnssecDisabledOk'))
            await loadZone()
        } catch (err) {
            setError(err.message)
        } finally {
            setDisablingDnssec(false)
        }
    }

    function copyToClipboard(text, i18nToast, vars) {
        if (!text) return
        const msg = i18nToast ? t(i18nToast, vars) : null
        navigator.clipboard.writeText(String(text)).then(() => {
            if (msg) setSuccess(msg)
        }).catch(() => {})
    }

    function resolveName(name) {
        let fqdn
        if (name === '@') fqdn = zoneName
        else if (!name.includes(zoneName)) fqdn = `${name}.${zoneName}`
        else fqdn = name
        if (!fqdn.endsWith('.')) fqdn = fqdn + '.'
        return fqdn
    }

    function previewFqdn(name) {
        return resolveName((name || '').trim() || '@')
    }

    function openEdit(record) {
        setIsEdit(true)
        setAddType(record.type)
        setOldContent(record.content)
        setAddTTL(record.ttl.toString())
        setModalError('')

        let name = record.name.replace(/\.$/, '')
        if (name === zoneName) name = '@'
        else if (name.endsWith(`.${zoneName}`)) name = name.substring(0, name.length - zoneName.length - 1)
        setAddName(name)

        const def = RECORD_TYPES[record.type] || { parse: () => ({}) }
        try {
            setDynFieldsList([def.parse ? def.parse(record.content) : {}])
        } catch (e) {
            console.warn('Could not parse record content:', e)
            setDynFieldsList([{}])
        }
        setShowAdd(true)
    }

    /* ----- Live-Validierung -------------------------------------------------*/
    const apexWarning = getApexWarning(addName.trim() || '@', addType)

    /** Validatoren für das aktuell ausgewählte Type – pro Field. */
    const validators = FIELD_VALIDATORS[addType] || {}

    function fieldHint(fieldId, value) {
        const v = validators[fieldId]
        if (!v) return ''
        return v(value)
    }

    /** Existiert ein Record mit (name,type,content) bereits? */
    function findDuplicate(name, type, content) {
        const fqdn = resolveName(name)
        return records.find(r => r.name === fqdn && r.type === type && r.content === content)
    }

    /* ----- Submit -----------------------------------------------------------*/
    async function handleAddRecord(e) {
        e.preventDefault()
        if (saving) return
        setSaving(true)
        setModalError('')

        const def = RECORD_TYPES[addType]
        if (!def) { setSaving(false); return }

        if (apexWarning?.kind === 'error') {
            setModalError(apexWarning.text)
            setSaving(false)
            return
        }

        // Hartfehler in Feldern blocken den Submit
        for (let i = 0; i < dynFieldsList.length; i++) {
            const set = dynFieldsList[i] || {}
            for (const f of def.fields) {
                const val = set[f.id]
                if (val === undefined || String(val).trim() === '') {
                    setModalError(t('zoneDetail.fillField', { label: f.labelKey ? t(f.labelKey) : f.label }))
                    setSaving(false)
                    return
                }
                const hint = fieldHint(f.id, val)
                if (hint && typeof hint === 'object' && hint.error) {
                    const lbl = f.labelKey ? t(f.labelKey) : f.label
                    setModalError(`${lbl}${dynFieldsList.length > 1 ? ` (Wert ${i + 1})` : ''}: ${hint.error}`)
                    setSaving(false)
                    return
                }
            }
        }

        // Werte bauen
        const contents = []
        for (const set of dynFieldsList) {
            try { contents.push(def.build(set)) } catch (err) {
                setModalError(t('zoneDetail.valueBuildFailed', { message: err.message || String(err) }))
                setSaving(false)
                return
            }
        }

        const fqdn = resolveName(addName)

        // Duplikat-Check (nur Add, nicht beim Edit eines bestehenden Eintrags)
        if (!isEdit) {
            for (const c of contents) {
                const dup = findDuplicate(addName, addType, c)
                if (dup) {
                    setModalError(t('zoneDetail.duplicateRecord', {
                        type: addType,
                        name: dup.name.replace(/\.$/, ''),
                        value: c,
                    }))
                    setSaving(false)
                    return
                }
            }
        }

        try {
            if (isEdit) {
                await api.updateRecord(server, zoneId, {
                    name: fqdn,
                    type: addType,
                    ttl: parseInt(addTTL),
                    old_content: oldContent,
                    new_content: contents[0],
                    disabled: false,
                })
            } else {
                await api.createRecord(server, zoneId, {
                    name: fqdn,
                    type: addType,
                    ttl: parseInt(addTTL),
                    records: contents.map(c => ({ content: c, disabled: false })),
                })
            }
            closeModal()
            const displayName = fqdn.replace(/\.$/, '')
            setSuccess(isEdit
                ? t('zoneDetail.recordUpdated', { type: addType, name: displayName })
                : t('zoneDetail.recordCreated', { type: addType, name: displayName }))
            loadZone()
        } catch (err) {
            setModalError(err.message)
        } finally {
            setSaving(false)
        }
    }

    async function handleDelete(name, type) {
        if (!confirm(t('zoneDetail.deleteRecordConfirm', { name, type }))) return
        try {
            await api.deleteRecord(server, zoneId, { name, type })
            setSuccess(t('zoneDetail.recordDeleted', { type, name: name.replace(/\.$/, '') }))
            loadZone()
        } catch (err) {
            setError(err.message)
        }
    }

    if (loading) return <div className="flex items-center justify-center h-64"><Loader2 className="w-8 h-8 text-accent animate-spin" /></div>

    const userCanEdit = !me || me.role === 'admin' || me.zone_permissions?.[zoneKey] !== 'read'
    const canEdit = userCanEdit && serverCanWrite

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

    const canMulti = !isEdit && MULTI_VALUE_OK.has(addType)
    const def = RECORD_TYPES[addType]

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-3 min-w-0">
                    <button onClick={() => navigate('/zones')} className="p-2 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-primary transition-colors shrink-0">
                        <ArrowLeft className="w-5 h-5" />
                    </button>
                    <div className="min-w-0">
                        <h1 className="text-2xl font-bold text-text-primary break-all">{zoneName}</h1>
                        <p className="text-text-muted text-sm">{t('zoneDetail.recordsCount', { server, count: records.length })}</p>
                    </div>
                </div>
                <div className="flex flex-col sm:flex-row flex-wrap items-stretch sm:items-center gap-2 w-full sm:w-auto">
                    <button
                        type="button"
                        onClick={() => setShowDsModal(true)}
                        className="self-start sm:self-auto order-2 sm:order-1 flex items-center justify-center gap-2 px-4 py-2.5 border border-amber-500/40 bg-amber-500/10 hover:bg-amber-500/20 text-amber-200 rounded-lg font-medium text-sm transition-all"
                    >
                        <Shield className="w-4 h-4 shrink-0" />
                        {t('zoneDetail.dnssecModalOpenButton')}
                    </button>
                    <button
                        type="button"
                        onClick={openAddModal}
                        disabled={!canEdit}
                        className="self-start sm:self-auto order-1 sm:order-2 flex items-center justify-center gap-2 px-4 py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all disabled:opacity-40 disabled:cursor-not-allowed disabled:pointer-events-none"
                    >
                        <Plus className="w-4 h-4 shrink-0" /> {t('zoneDetail.addRecord')}
                    </button>
                </div>
            </div>

            {error && (
                <div className="p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-center gap-3">
                    <AlertCircle className="w-5 h-5 shrink-0" />
                    <p className="text-sm">{error}</p>
                    <button onClick={() => setError('')} className="ml-auto text-xs hover:underline">×</button>
                </div>
            )}

            {success && (
                <div className="p-4 rounded-xl bg-success/10 border border-success/30 text-success flex items-center gap-3">
                    <CheckCircle className="w-5 h-5 shrink-0" />
                    <p className="text-sm flex-1">{success}</p>
                    <button onClick={() => setSuccess('')} className="text-xs hover:underline" aria-label={t('common.close')}>×</button>
                </div>
            )}

            {!userCanEdit && (
                <div className="p-4 rounded-xl bg-bg-secondary/80 border border-border text-text-secondary text-sm">
                    {t('zoneDetail.readOnlyZone')}
                </div>
            )}

            {userCanEdit && !serverCanWrite && (
                <div className="p-4 rounded-xl bg-warning/10 border border-warning/30 text-warning flex items-start gap-3">
                    <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
                    <div className="text-sm">
                        <div className="font-medium">
                            {t('zoneDetail.serverReadOnlyTitle', { server })}
                        </div>
                        <div className="mt-1 text-text-secondary">
                            {t('zoneDetail.serverReadOnlyBody')}
                        </div>
                        {otherWritableServers.length > 0 && (
                            <div className="mt-2 flex flex-wrap gap-1.5">
                                {otherWritableServers.map((s) => (
                                    <button
                                        key={s.name}
                                        type="button"
                                        onClick={() => navigate(`/zones/${encodeURIComponent(s.name)}/${encodeURIComponent(zoneId)}`)}
                                        className="text-xs px-2 py-0.5 rounded-full bg-success/10 border border-success/30 text-success hover:bg-success/20 transition-colors"
                                    >
                                        {t('zoneDetail.switchToServer', { server: s.name, defaultValue: 'Wechsel zu {{server}}' })}
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {userCanEdit && serverCanWrite && otherWritableServers.length > 0 && (
                <div className="p-3 rounded-xl bg-bg-secondary/60 border border-border/60 text-text-secondary text-xs flex items-center gap-2">
                    <CheckCircle className="w-4 h-4 text-success shrink-0" />
                    <span>
                        {t('zoneDetail.fanoutInfo', {
                            primary: server,
                            peers: otherWritableServers.map((s) => s.name).join(', '),
                        })}
                    </span>
                </div>
            )}

            <ZoneDnssecRegistrarCard
                t={t}
                zoneMeta={zoneMeta}
                dsLoading={dsLoading}
                dsError={dsError}
                dsData={dsData}
                canEdit={canEdit}
                enablingDnssec={enablingDnssec}
                disablingDnssec={disablingDnssec}
                onOpenModal={() => setShowDsModal(true)}
                onEnableDnssec={handleEnableDnssec}
                onDisableDnssec={handleDisableDnssec}
            />

            {/* Records grouped by type */}
            {records.length === 0 && (
                <div className="glass-card p-8 text-center text-text-muted">
                    <Globe className="w-12 h-12 mx-auto mb-3 opacity-30" />
                    <h2 className="text-lg font-semibold text-text-primary">{t('zoneDetail.noRecordsTitle')}</h2>
                    <p className="text-sm mt-2 max-w-xl mx-auto">{t('zoneDetail.noRecordsBody')}</p>
                    {canEdit && (
                        <button
                            type="button"
                            onClick={openAddModal}
                            className="mt-4 inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent text-white text-sm font-medium"
                        >
                            <Plus className="w-4 h-4" /> {t('zoneDetail.addRecord')}
                        </button>
                    )}
                </div>
            )}

            {sortedTypes.map(type => (
                <div key={type} className="glass-card overflow-hidden">
                    <div className="px-4 py-3 bg-bg-hover/30 border-b border-border flex items-center gap-2">
                        <span className="text-xs font-bold px-2 py-0.5 bg-accent/20 text-accent-light rounded">{type}</span>
                        <span className="text-xs text-text-muted">{t('zoneDetail.recordCount', { count: grouped[type].length })}</span>
                    </div>
                    <div className="overflow-x-auto">
                    <table className="w-full text-sm min-w-[640px]">
                        <thead>
                            <tr className="border-b border-border/50">
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('zoneDetail.name')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('zoneDetail.value')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs w-20">{t('zoneDetail.ttl')}</th>
                                <th className="text-right p-3 text-text-muted font-medium text-xs w-28">{t('zones.actions')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {grouped[type].map((r) => (
                                <tr key={`${r.name}:${r.type}:${r.content}`} className="border-b border-border/30 hover:bg-bg-hover/30 transition-colors">
                                    <td className="p-3 font-mono text-xs text-text-primary">{r.name.replace(/\.$/, '')}</td>
                                    <td className="p-3 font-mono text-xs text-text-secondary break-all">{r.content}</td>
                                    <td className="p-3 text-text-muted text-xs">{r.ttl}</td>
                                    <td className="p-3 text-right whitespace-nowrap">
                                        <button
                                            onClick={() => openEdit(r)}
                                            disabled={!canEdit}
                                            className="p-1 rounded text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors mr-1 disabled:opacity-30 disabled:pointer-events-none"
                                            title={t('zoneDetail.edit')}
                                        >
                                            <Pencil className="w-3.5 h-3.5" />
                                        </button>
                                        {type !== 'SOA' && (
                                            <button
                                                onClick={() => openClone(r)}
                                                disabled={!canEdit}
                                                className="p-1 rounded text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors mr-1 disabled:opacity-30 disabled:pointer-events-none"
                                                title={t('zoneDetail.clone')}
                                            >
                                                <Copy className="w-3.5 h-3.5" />
                                            </button>
                                        )}
                                        {type !== 'SOA' && type !== 'NS' && (
                                            <button
                                                onClick={() => handleDelete(r.name, r.type)}
                                                disabled={!canEdit}
                                                className="p-1 rounded text-text-muted hover:text-danger hover:bg-danger/10 transition-colors disabled:opacity-30 disabled:pointer-events-none"
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
                </div>
            ))}

            {/* Add / Edit Record Modal */}
            {showAdd && (
                <div
                    className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
                    onClick={() => { if (!saving) closeModal() }}
                >
                    <div className="glass-card p-6 w-full max-w-3xl max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                        <div className="flex items-start justify-between mb-4">
                            <h2 className="text-lg font-bold text-text-primary">
                                {isEdit ? t('zoneDetail.editRecord') : t('zoneDetail.addRecord')}
                            </h2>
                            <button
                                type="button"
                                onClick={() => { if (!saving) closeModal() }}
                                className="p-1 rounded hover:bg-bg-hover text-text-muted hover:text-text-primary transition-colors"
                                title={t('common.close')}
                            >
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        {/* Schnellvorlagen-Leiste (nur beim Anlegen) */}
                        {!isEdit && (
                            <div className="mb-4 rounded-xl border border-accent/20 bg-accent/5 p-3">
                                <div className="flex items-center gap-2 mb-2">
                                    <Sparkles className="w-4 h-4 text-accent" />
                                    <span className="text-xs font-medium text-text-secondary">{t('zoneDetail.quickTemplates')}</span>
                                </div>
                                <div className="flex flex-wrap gap-2">
                                    {quickTemplates.map(tpl => (
                                        <button
                                            key={tpl.id}
                                            type="button"
                                            onClick={() => applyQuickTemplate(tpl)}
                                            disabled={!canEdit}
                                            className="text-xs px-2.5 py-1 rounded-md border border-border bg-bg-primary hover:bg-bg-hover transition-colors disabled:opacity-35 disabled:pointer-events-none"
                                            title={tpl.note || tpl.label}
                                        >
                                            {tpl.label}
                                        </button>
                                    ))}
                                </div>
                            </div>
                        )}

                        {/* Modal-weiter Fehler (Submit / Backend) */}
                        {modalError && (
                            <div className="mb-4 p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-start gap-3">
                                <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
                                <div className="flex-1 min-w-0">
                                    <p className="text-sm font-medium">
                                        {isEdit ? t('zoneDetail.updateErrorTitle') : t('zoneDetail.createErrorTitle')}
                                    </p>
                                    <p className="text-sm mt-1 break-words">{modalError}</p>
                                </div>
                                <button
                                    type="button"
                                    onClick={() => setModalError('')}
                                    className="text-xs hover:underline shrink-0"
                                    aria-label={t('common.close')}
                                >
                                    ×
                                </button>
                            </div>
                        )}

                        {/* Apex-Warnung (live, nicht erst beim Submit) */}
                        {apexWarning && (
                            <div className={`mb-4 p-3 rounded-xl border flex items-start gap-2 ${apexWarning.kind === 'error' ? 'bg-danger/10 border-danger/30 text-danger' : 'bg-amber-500/10 border-amber-500/30 text-amber-200'}`}>
                                <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                                <div className="text-xs">
                                    <p className="font-medium mb-0.5">{t('zoneDetail.apexWarningTitle')}</p>
                                    <p>{apexWarning.text}</p>
                                </div>
                            </div>
                        )}

                        <form ref={formRef} onSubmit={handleAddRecord} className="space-y-4">
                            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 items-start">
                                <div className="min-w-0 flex flex-col gap-1">
                                    <label className="block text-xs font-medium text-text-secondary leading-tight">{t('zoneDetail.recordType')}</label>
                                    <select value={addType} disabled={isEdit} onChange={e => { setAddType(e.target.value); setDynFieldsList([{}]) }} className="w-full h-10 px-3 text-sm rounded-lg border border-border bg-bg-primary text-text-primary disabled:opacity-50">
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

                            {/* Dynamische Werte – ggf. mehrere für Round-Robin */}
                            <div className="border-t border-border pt-4 space-y-4">
                                {dynFieldsList.map((set, idx) => {
                                    const fldList = def?.fields || []
                                    return (
                                        <div key={idx} className={dynFieldsList.length > 1 ? 'rounded-lg border border-border/60 p-3 bg-bg-primary/50' : ''}>
                                            {dynFieldsList.length > 1 && (
                                                <div className="flex items-center justify-between mb-2">
                                                    <span className="text-xs font-medium text-text-muted">{t('zoneDetail.valueIndex', { n: idx + 1 })}</span>
                                                    <button
                                                        type="button"
                                                        onClick={() => setDynFieldsList(list => list.filter((_, i) => i !== idx))}
                                                        className="p-1 rounded text-text-muted hover:text-danger hover:bg-danger/10 transition-colors"
                                                        title={t('zoneDetail.removeValue')}
                                                    >
                                                        <Trash2 className="w-3.5 h-3.5" />
                                                    </button>
                                                </div>
                                            )}
                                            <div className={`grid gap-4 ${
                                                addType === 'SRV'
                                                    ? 'grid-cols-1 sm:grid-cols-2 lg:grid-cols-4'
                                                    : fldList.length === 1 && fldList[0].textarea
                                                        ? 'grid-cols-1'
                                                        : 'grid-cols-1 sm:grid-cols-2'
                                            }`}>
                                                {fldList.map((f) => {
                                                    const oneTextareaOnly = fldList.length === 1 && fldList[0].textarea
                                                    const textareaSpan = f.textarea
                                                        ? (oneTextareaOnly && addType === 'TXT' ? 'sm:col-span-2' : oneTextareaOnly ? '' : 'sm:col-span-2 lg:col-span-4')
                                                        : ''
                                                    const value = set[f.id] || ''
                                                    const hint = fieldHint(f.id, value)
                                                    const hintText = typeof hint === 'object' && hint?.error ? hint.error : (typeof hint === 'string' ? hint : '')
                                                    const isError = typeof hint === 'object' && !!hint?.error
                                                    return (
                                                        <div key={f.id} className={`min-w-0 ${textareaSpan}`}>
                                                            <label className="block text-xs font-medium text-text-secondary mb-1">{f.labelKey ? t(f.labelKey) : f.label}</label>
                                                            {f.select ? (
                                                                <select
                                                                    value={value || f.select[0]}
                                                                    onChange={e => setDynFieldsList(list => list.map((s, i) => i === idx ? { ...s, [f.id]: e.target.value } : s))}
                                                                    className="w-full h-10 px-3 text-sm rounded-lg border border-border bg-bg-primary"
                                                                >
                                                                    {f.select.map(o => <option key={o} value={o}>{o}</option>)}
                                                                </select>
                                                            ) : f.textarea ? (
                                                                <textarea
                                                                    value={value}
                                                                    onChange={e => setDynFieldsList(list => list.map((s, i) => i === idx ? { ...s, [f.id]: e.target.value } : s))}
                                                                    placeholder={f.placeholderKey ? t(f.placeholderKey) : f.placeholder}
                                                                    className={`w-full min-h-[100px] rounded-lg border bg-bg-primary px-3 py-2 text-sm font-mono text-[13px] placeholder:font-sans placeholder:text-sm ${isError ? 'border-danger/60' : 'border-border'}`}
                                                                />
                                                            ) : (
                                                                <input
                                                                    type={f.type || 'text'}
                                                                    value={value}
                                                                    onChange={e => setDynFieldsList(list => list.map((s, i) => i === idx ? { ...s, [f.id]: e.target.value } : s))}
                                                                    placeholder={f.placeholder}
                                                                    className={`w-full min-w-0 h-10 px-3 text-sm rounded-lg border bg-bg-primary ${isError ? 'border-danger/60' : 'border-border'}`}
                                                                    autoComplete="off"
                                                                    spellCheck={false}
                                                                />
                                                            )}
                                                            {hintText && (
                                                                <p className={`mt-1 text-xs ${isError ? 'text-danger' : 'text-amber-300'}`}>{hintText}</p>
                                                            )}
                                                        </div>
                                                    )
                                                })}
                                            </div>
                                        </div>
                                    )
                                })}

                                {/* "+ Weiteren Wert" für Multi-Value-Typen */}
                                {canMulti && (
                                    <button
                                        type="button"
                                        onClick={() => setDynFieldsList(list => [...list, {}])}
                                        className="text-xs flex items-center gap-1.5 px-3 py-1.5 rounded-md border border-dashed border-border hover:border-accent/60 hover:text-accent-light text-text-muted transition-colors"
                                    >
                                        <Plus className="w-3.5 h-3.5" /> {t('zoneDetail.addValue')}
                                    </button>
                                )}

                                <DnsRecordTypeHint recordType={addType} />
                            </div>

                            <div className="flex justify-between items-center gap-3 pt-2 border-t border-border">
                                <p className="text-xs text-text-muted hidden sm:block">{t('zoneDetail.kbdHint')}</p>
                                <div className="flex justify-end gap-3">
                                    <button type="button" onClick={closeModal} disabled={saving} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-50">{t('common.cancel')}</button>
                                    <button type="submit" disabled={!canEdit || saving || apexWarning?.kind === 'error'} className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                                        {saving && <Loader2 className="w-4 h-4 animate-spin" />} {t('common.save')}
                                    </button>
                                </div>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {showDsModal && (
                <div
                    className="fixed inset-0 z-[60] flex items-center justify-center bg-black/65 backdrop-blur-sm p-4"
                    onClick={() => setShowDsModal(false)}
                >
                    <div
                        className="glass-card w-full max-w-2xl max-h-[90vh] overflow-y-auto p-5 sm:p-6 shadow-2xl"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <div className="flex items-start justify-between gap-3 mb-4">
                            <h2 className="text-lg font-bold text-text-primary pr-6 leading-snug">
                                {t('zoneDetail.dnssecModalTitle')}
                            </h2>
                            <button
                                type="button"
                                onClick={() => setShowDsModal(false)}
                                className="p-1.5 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-primary shrink-0"
                                title={t('zoneDetail.dnssecModalClose')}
                            >
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        {!zoneMeta?.dnssec ? (
                            <div className="space-y-3 text-sm text-text-secondary">
                                <p>{t('zoneDetail.dnssecModalNeedEnable')}</p>
                                <button
                                    type="button"
                                    onClick={() => { handleEnableDnssec() }}
                                    disabled={!canEdit || enablingDnssec || disablingDnssec}
                                    className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-accent/20 text-accent-light text-sm font-medium disabled:opacity-50"
                                >
                                    {enablingDnssec ? <Loader2 className="w-4 h-4 animate-spin" /> : <Shield className="w-4 h-4" />}
                                    {t('zoneDetail.dnssecEnableButton')}
                                </button>
                            </div>
                        ) : dsLoading ? (
                            <div className="flex items-center gap-2 text-text-muted py-6">
                                <Loader2 className="w-5 h-5 animate-spin text-accent" />
                                {t('zoneDetail.dnssecLoading')}
                            </div>
                        ) : dsError ? (
                            <div className="p-3 rounded-lg bg-danger/10 text-danger text-sm">{dsError}</div>
                        ) : (
                            <div className="space-y-5 text-sm">
                                <div className="rounded-lg border border-border bg-bg-secondary/30 p-4">
                                    <p className="font-medium text-text-primary mb-1">{t('zoneDetail.dnssecModalWhyTitle')}</p>
                                    <p className="text-text-muted leading-relaxed">{t('zoneDetail.dnssecModalWhyBody')}</p>
                                </div>

                                {recommendedDsRow?.parsed && !recommendedDsRow.parsed.error && (
                                    <div className="rounded-lg border-2 border-success/40 bg-success/10 p-4">
                                        <p className="text-xs font-semibold text-success mb-3 uppercase tracking-wide">
                                            {t('zoneDetail.dnssecModalRecommended')}
                                        </p>
                                        <div className="space-y-1">
                                            {[
                                                [t('zoneDetail.dnssecModalFieldKeyTag'), String(recommendedDsRow.parsed.key_tag)],
                                                [t('zoneDetail.dnssecModalFieldAlgorithmName'), String(recommendedDsRow.parsed.algorithm_name || '')],
                                                [t('zoneDetail.dnssecModalFieldAlgorithm'), String(recommendedDsRow.parsed.algorithm)],
                                                [t('zoneDetail.dnssecModalFieldDigestType'), `${recommendedDsRow.parsed.digest_type} – ${recommendedDsRow.parsed.digest_type_name}`],
                                                [t('zoneDetail.dnssecModalFieldDigestHex'), recommendedDsRow.parsed.digest_hex],
                                            ].map(([label, val]) => (
                                                <div
                                                    key={label}
                                                    className="flex flex-col sm:flex-row sm:items-center gap-2 py-1 border-b border-border/30 last:border-0"
                                                >
                                                    <span className="text-xs text-text-muted shrink-0 sm:w-52">{label}</span>
                                                    <code className="flex-1 text-xs font-mono break-all text-text-primary bg-bg-primary/50 px-2 py-1 rounded min-w-0">
                                                        {val}
                                                    </code>
                                                    <button
                                                        type="button"
                                                        onClick={() => copyToClipboard(val, 'zoneDetail.dnssecFieldCopied', { field: label })}
                                                        className="shrink-0 self-end sm:self-center p-1.5 rounded text-accent-light hover:bg-accent/15"
                                                    >
                                                        <Copy className="w-3.5 h-3.5" />
                                                    </button>
                                                </div>
                                            ))}
                                        </div>
                                        <button
                                            type="button"
                                            onClick={() => {
                                                const line = typeof recommendedDsRow.ds === 'string' ? recommendedDsRow.ds : recommendedDsRow.parsed.raw
                                                copyToClipboard(line, 'zoneDetail.dnssecCopied')
                                            }}
                                            className="mt-3 w-full sm:w-auto inline-flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-success/20 hover:bg-success/30 text-success text-xs font-medium"
                                        >
                                            <Copy className="w-3.5 h-3.5" />
                                            {t('zoneDetail.dnssecModalCopyDsLine')}
                                        </button>
                                    </div>
                                )}

                                <div>
                                    <p className="text-xs font-medium text-text-muted mb-2">{t('zoneDetail.dnssecModalDigestVariants')}</p>
                                    <div className="space-y-2">
                                        {(dsData?.ds_records || []).map((row, idx) => {
                                            const p = row.parsed
                                            const line = typeof row.ds === 'string' ? row.ds : String(row.ds)
                                            if (!p || p.error) {
                                                return (
                                                    <div key={idx} className="rounded border border-border p-2 text-xs">
                                                        <code className="break-all">{line}</code>
                                                        <button
                                                            type="button"
                                                            onClick={() => copyToClipboard(line, 'zoneDetail.dnssecCopied')}
                                                            className="mt-2 text-accent-light text-xs"
                                                        >
                                                            {t('zoneDetail.dnssecCopyLine')}
                                                        </button>
                                                    </div>
                                                )
                                            }
                                            return (
                                                <div
                                                    key={idx}
                                                    className={`rounded-lg border p-3 ${p.recommended ? 'border-success/30 bg-success/5' : 'border-border bg-bg-secondary/30'}`}
                                                >
                                                    <div className="flex flex-wrap gap-2 text-[10px] text-text-muted mb-2">
                                                        <span>{t('zoneDetail.dnssecKeyType')}: {row.keytype}</span>
                                                        <span>ID {row.key_id}</span>
                                                        <span>{p.digest_type_name}</span>
                                                    </div>
                                                    <code className="block text-xs font-mono break-all text-text-primary mb-2">{line}</code>
                                                    <button
                                                        type="button"
                                                        onClick={() => copyToClipboard(line, 'zoneDetail.dnssecCopied')}
                                                        className="text-xs text-accent-light hover:underline"
                                                    >
                                                        {t('zoneDetail.dnssecModalCopyDsLine')}
                                                    </button>
                                                </div>
                                            )
                                        })}
                                    </div>
                                </div>

                                {(dsData?.signing_keys || []).length > 0 && (dsData.signing_keys[0].dnskey_parsed || dsData.signing_keys[0].dnskey) && (
                                    <div className="rounded-lg border border-border p-4 space-y-2">
                                        <p className="text-sm font-medium text-text-primary">{t('zoneDetail.dnssecModalDnskeyTitle')}</p>
                                        {(dsData.signing_keys || []).map((k) => {
                                            const d = k.dnskey_parsed
                                            if (d && !d.error) {
                                                return (
                                                    <div key={k.key_id} className="space-y-1 border-b border-border/40 last:border-0 pb-3 last:pb-0">
                                                        {[
                                                            [t('zoneDetail.dnssecModalDnskeyFlags'), String(d.flags)],
                                                            [t('zoneDetail.dnssecModalDnskeyRole'), d.flags_role || '—'],
                                                            [t('zoneDetail.dnssecModalDnskeyProtocol'), String(d.protocol)],
                                                            [t('zoneDetail.dnssecModalFieldAlgorithmName'), d.algorithm_name || String(d.algorithm)],
                                                            [t('zoneDetail.dnssecModalDnskeyPublic'), d.public_key_base64 || '—'],
                                                        ].map(([label, val]) => (
                                                            <div key={label} className="flex flex-col sm:flex-row sm:items-start gap-2 text-xs">
                                                                <span className="text-text-muted shrink-0 sm:w-52">{label}</span>
                                                                <code className="flex-1 font-mono break-all bg-bg-primary/50 px-2 py-1 rounded min-w-0">
                                                                    {val}
                                                                </code>
                                                                <button
                                                                    type="button"
                                                                    onClick={() => copyToClipboard(val, 'zoneDetail.dnssecFieldCopied', { field: label })}
                                                                    className="p-1 rounded hover:bg-bg-hover self-start"
                                                                >
                                                                    <Copy className="w-3.5 h-3.5 text-accent-light" />
                                                                </button>
                                                            </div>
                                                        ))}
                                                    </div>
                                                )
                                            }
                                            if (k.dnskey) {
                                                return (
                                                    <div key={k.key_id}>
                                                        <code className="text-xs break-all block bg-bg-primary/50 p-2 rounded">{k.dnskey}</code>
                                                        <button
                                                            type="button"
                                                            onClick={() => copyToClipboard(k.dnskey, 'zoneDetail.dnssecCopied')}
                                                            className="mt-1 text-xs text-accent-light"
                                                        >
                                                            {t('zoneDetail.dnssecCopyLine')}
                                                        </button>
                                                    </div>
                                                )
                                            }
                                            return null
                                        })}
                                    </div>
                                )}

                                <div className="rounded-lg border border-danger/30 bg-danger/5 p-4 space-y-2">
                                    <p className="text-xs text-text-muted leading-relaxed">{t('zoneDetail.dnssecDisableHint')}</p>
                                    <button
                                        type="button"
                                        onClick={handleDisableDnssec}
                                        disabled={!canEdit || enablingDnssec || disablingDnssec}
                                        className="inline-flex items-center gap-2 px-3 py-2 rounded-lg border border-danger/50 bg-danger/15 text-danger text-xs font-medium hover:bg-danger/25 disabled:opacity-50"
                                    >
                                        {disablingDnssec ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                                        {t('zoneDetail.dnssecDisableButton')}
                                    </button>
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    )
}
