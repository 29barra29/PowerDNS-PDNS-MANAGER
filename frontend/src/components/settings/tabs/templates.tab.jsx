import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Pencil, Loader2, X, Copy, Star } from 'lucide-react'
import api from '../../../api'
import { ALL_RECORD_TYPE_KEYS, TEMPLATE_CONTENT_PLACEHOLDERS } from '../../../constants/dnsRecordTypes'
import { TTL_MAX, TTL_MIN, formatTtl, isValidTtl, parseTtl } from '../../../lib/ttl.js'
import DnsRecordTypeHint from '../../DnsRecordTypeHint'
import ModalErrorBanner from '../../ModalErrorBanner'
import TtlInput from '../../TtlInput'
import { useSettings } from '../settingsContext'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'templates', order: 40, labelKey: 'settings.templates', icon: Copy, adminOnly: true }

// Tab "Vorlagen" (Zonen-Vorlagen).
// F8 (Welle 1): Standard- und Record-TTL ueber TtlInput mit den Grenzen 60..604800 wie im Backend (D10, F8 3.8);
// Texte uebersetzt (A11); Dialog-Fehler ueber ModalErrorBanner (C05).
export default function TemplatesTab() {
    const { t } = useTranslation()
    const { notify, isAdmin } = useSettings()
    const setError = notify.error
    const setSuccess = notify.success
    const [templateModalError, setTemplateModalError] = useState('')
    // Templates
    const [templates, setTemplates] = useState([])
    const [loadingTemplates, setLoadingTemplates] = useState(false)
    const [showTemplateForm, setShowTemplateForm] = useState(false)
    const [editTemplateId, setEditTemplateId] = useState(null)
    // TTL-Felder halten den Text des Eingabefelds (String), gesendet wird die Zahl
    const emptyTemplate = {
        name: '', description: '', nameservers: '',
        kind: 'Native', soa_edit_api: 'DEFAULT', default_ttl: '3600',
        records: [], is_default: false,
    }
    const [templateForm, setTemplateForm] = useState({ ...emptyTemplate })
    const [savingTemplate, setSavingTemplate] = useState(false)
    const emptyRecord = { name: '@', type: 'A', content: '', ttl: '3600', prio: null }
    const [newRecord, setNewRecord] = useState({ ...emptyRecord })


    async function loadTemplates() {
        setLoadingTemplates(true)
        try {
            const data = await api.getTemplates()
            setTemplates(data.templates || [])
        } catch (err) {
            setError(err.message)
        } finally { setLoadingTemplates(false) }
    }

    function openTemplateAdd() {
        setEditTemplateId(null)
        setTemplateForm({ ...emptyTemplate })
        setNewRecord({ ...emptyRecord })
        setTemplateModalError('')
        setShowTemplateForm(true)
    }

    function openTemplateEdit(t) {
        setEditTemplateId(t.id)
        setTemplateForm({
            name: t.name,
            description: t.description || '',
            nameservers: (t.nameservers || []).join(', '),
            kind: t.kind || 'Native',
            soa_edit_api: t.soa_edit_api || 'DEFAULT',
            default_ttl: String(t.default_ttl || 3600),
            records: t.records || [],
            is_default: t.is_default || false,
        })
        setNewRecord({ ...emptyRecord })
        setTemplateModalError('')
        setShowTemplateForm(true)
    }

    function closeTemplateModal() {
        setShowTemplateForm(false)
        setTemplateModalError('')
    }

    function addRecordToTemplate() {
        if (!newRecord.content.trim()) return
        if (!isValidTtl(newRecord.ttl)) {
            setTemplateModalError(t('ttlInput.outOfRange', { min: TTL_MIN, max: TTL_MAX }))
            return
        }
        const rec = { ...newRecord, ttl: parseTtl(newRecord.ttl) }
        if (rec.type !== 'MX') rec.prio = null
        setTemplateForm({
            ...templateForm,
            records: [...templateForm.records, rec],
        })
        setNewRecord({ ...emptyRecord })
    }

    function removeRecordFromTemplate(idx) {
        setTemplateForm({
            ...templateForm,
            records: templateForm.records.filter((_, i) => i !== idx),
        })
    }

    async function handleSaveTemplate(e) {
        e.preventDefault()
        setTemplateModalError('')
        // Gleiche Grenzen wie das Backend (422): Standard-TTL und jede Record-TTL (auch aus aelteren Vorlagen)
        const badRecord = templateForm.records.find((r) => !isValidTtl(r.ttl))
        if (!isValidTtl(templateForm.default_ttl) || badRecord) {
            const msg = t('ttlInput.outOfRange', { min: TTL_MIN, max: TTL_MAX })
            setTemplateModalError(badRecord ? `${badRecord.name} ${badRecord.type}: ${msg}` : msg)
            return
        }
        setSavingTemplate(true)
        try {
            const nsArray = templateForm.nameservers.split(/[\n,]+/).map(n => n.trim()).filter(Boolean)
            const payload = {
                name: templateForm.name,
                description: templateForm.description,
                nameservers: nsArray,
                kind: templateForm.kind,
                soa_edit_api: templateForm.soa_edit_api,
                default_ttl: parseTtl(templateForm.default_ttl),
                records: templateForm.records,
                is_default: templateForm.is_default,
            }
            if (editTemplateId) {
                await api.updateTemplate(editTemplateId, payload)
                setSuccess(t('settings.templateUpdated'))
            } else {
                await api.createTemplate(payload)
                setSuccess(t('settings.templateCreated'))
            }
            closeTemplateModal()
            loadTemplates()
        } catch (err) {
            setTemplateModalError(err.message)
        } finally {
            setSavingTemplate(false)
        }
    }

    async function handleDeleteTemplate(id, name) {
        if (!window.confirm(t('settings.templateDeleteConfirm', { name }))) return
        try {
            await api.deleteTemplate(id)
            setSuccess(t('settings.templateDeleted', { name }))
            loadTemplates()
        } catch (err) { setError(err.message) }
    }

    useEffect(() => {
        if (!isAdmin) return
        // eslint-disable-next-line react-hooks/set-state-in-effect -- Laden beim Aktivieren wie 2.4.1
        loadTemplates()
    }, []) // eslint-disable-line react-hooks/exhaustive-deps -- nur beim Aktivieren laden (wie 2.4.1)

    // Zusaetzliches Render-Gate (F8 6.7): der Tab ist adminOnly
    if (!isAdmin) return null

    return (
        <>
            <div className="space-y-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div>
                        <h2 className="text-lg font-semibold text-text-primary">{t('settings.templatesTitle')}</h2>
                        <p className="text-sm text-text-muted">{t('settings.templatesSubtitle')}</p>
                    </div>
                    <button onClick={openTemplateAdd} className="self-start sm:self-auto flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all shrink-0">
                        <Plus className="w-4 h-4" /> {t('templates.newTemplate')}
                    </button>
                </div>

                {loadingTemplates ? (
                    <div className="flex justify-center py-8"><Loader2 className="w-6 h-6 text-accent animate-spin" /></div>
                ) : templates.length === 0 ? (
                    <div className="glass-card p-12 text-center">
                        <Copy className="w-16 h-16 mx-auto mb-4 text-text-muted opacity-30" />
                        <h3 className="text-lg font-semibold text-text-primary mb-2">{t('templates.noTemplatesYet')}</h3>
                        <p className="text-sm text-text-muted mb-4">{t('templates.createFirstTemplateHint')}</p>
                        <button onClick={openTemplateAdd} className="px-6 py-2.5 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg font-medium text-sm">
                            <Plus className="w-4 h-4 inline mr-2" /> {t('templates.firstTemplateCreate')}
                        </button>
                    </div>
                ) : (
                    <div className="space-y-3">
                        {templates.map(tmpl => (
                            <div key={tmpl.id} className="glass-card p-5">
                                <div className="flex items-start gap-4">
                                    <div className={`w-12 h-12 rounded-xl flex items-center justify-center shrink-0 ${tmpl.is_default ? 'bg-warning/20' : 'bg-accent/20'}`}>
                                        {tmpl.is_default ? <Star className="w-6 h-6 text-warning" /> : <Copy className="w-6 h-6 text-accent-light" />}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-2 flex-wrap">
                                            <h3 className="font-semibold text-text-primary">{tmpl.name}</h3>
                                            {tmpl.is_default && <span className="text-xs px-2 py-0.5 rounded-full bg-warning/10 text-warning border border-warning/30">{t('templates.defaultLabel')}</span>}
                                        </div>
                                        {tmpl.description && <p className="text-sm text-text-muted mt-0.5">{tmpl.description}</p>}
                                        <div className="flex items-center gap-4 mt-2 text-xs text-text-muted flex-wrap">
                                            <span>NS: <span className="text-text-secondary">{(tmpl.nameservers || []).join(', ') || '–'}</span></span>
                                            <span>{t('templates.metaKind')}: <span className="text-text-secondary">{tmpl.kind}</span></span>
                                            <span>TTL: <span className="text-text-secondary" title={formatTtl(tmpl.default_ttl, t)}>{tmpl.default_ttl}s</span></span>
                                            <span>{t('templates.metaRecords')}: <span className="text-text-secondary">{(tmpl.records || []).length}</span></span>
                                        </div>
                                        {(tmpl.records || []).length > 0 && (
                                            <div className="mt-2 flex flex-wrap gap-1">
                                                {tmpl.records.map((r, i) => (
                                                    <span key={i} className="text-xs px-2 py-0.5 rounded bg-bg-hover border border-border text-text-secondary font-mono">
                                                        {r.name} {r.type} {r.prio ? r.prio + ' ' : ''}{r.content}
                                                    </span>
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                    <div className="flex items-center gap-1 shrink-0">
                                        <button onClick={() => openTemplateEdit(tmpl)} className="p-2 rounded-lg text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors" title={t('templates.editTitle')}>
                                            <Pencil className="w-4 h-4" />
                                        </button>
                                        <button onClick={() => handleDeleteTemplate(tmpl.id, tmpl.name)} className="p-2 rounded-lg text-text-muted hover:text-danger hover:bg-danger/10 transition-colors" title={t('templates.deleteTitle')}>
                                            <Trash2 className="w-4 h-4" />
                                        </button>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>

        {/* =================== TEMPLATE FORM MODAL =================== */}
        {showTemplateForm && (
            <div
                className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
                onClick={() => { if (!savingTemplate) closeTemplateModal() }}
            >
                <div className="glass-card p-6 w-full max-w-2xl max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                    <div className="flex items-center justify-between mb-5">
                        <h2 className="text-lg font-bold text-text-primary">
                            {editTemplateId ? t('templates.editTemplate') : t('templates.createNewTemplate')}
                        </h2>
                        <button onClick={closeTemplateModal} className="p-1 rounded-lg hover:bg-bg-hover text-text-muted" title={t('common.close')} aria-label={t('common.close')}>
                            <X className="w-5 h-5" />
                        </button>
                    </div>

                    <ModalErrorBanner message={templateModalError} onClose={() => setTemplateModalError('')} />

                    <form onSubmit={handleSaveTemplate} className="space-y-4">
                        {/* Name & Beschreibung */}
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('templates.templateName')} *</label>
                                <input type="text" value={templateForm.name} onChange={e => setTemplateForm({ ...templateForm, name: e.target.value })}
                                    placeholder={t('templates.templateNamePlaceholder')} className="w-full px-3 py-2 text-sm" required />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('templates.description')}</label>
                                <input type="text" value={templateForm.description} onChange={e => setTemplateForm({ ...templateForm, description: e.target.value })}
                                    placeholder={t('templates.descriptionPlaceholder')} className="w-full px-3 py-2 text-sm" />
                            </div>
                        </div>

                        {/* Nameservers */}
                        <div>
                            <label className="block text-sm font-medium text-text-secondary mb-1">{t('templates.nameservers')}</label>
                            <textarea value={templateForm.nameservers} onChange={e => setTemplateForm({ ...templateForm, nameservers: e.target.value })}
                                placeholder="ns1.example.com., ns2.example.com." className="w-full px-3 py-2 text-sm min-h-[60px]" />
                            <p className="text-xs text-text-muted mt-1">{t('templates.nameserversHint')}</p>
                        </div>

                        {/* Kind, SOA-EDIT-API, TTL */}
                        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('dashboard.type')}</label>
                                <select value={templateForm.kind} onChange={e => setTemplateForm({ ...templateForm, kind: e.target.value })} className="w-full px-3 py-2 text-sm">
                                    <option value="Native">Native</option>
                                    <option value="Master">Master</option>
                                    <option value="Slave">Slave</option>
                                </select>
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">SOA-EDIT-API</label>
                                <select value={templateForm.soa_edit_api} onChange={e => setTemplateForm({ ...templateForm, soa_edit_api: e.target.value })} className="w-full px-3 py-2 text-sm">
                                    <option value="DEFAULT">DEFAULT</option>
                                    <option value="INCEPTION-INCREMENT">INCEPTION-INCREMENT</option>
                                    <option value="EPOCH">EPOCH</option>
                                </select>
                            </div>
                            <div>
                                <label htmlFor="template-default-ttl" className="block text-sm font-medium text-text-secondary mb-1">{t('templates.defaultTtl')}</label>
                                <TtlInput
                                    id="template-default-ttl"
                                    value={templateForm.default_ttl}
                                    onChange={(v) => setTemplateForm((f) => ({ ...f, default_ttl: v }))}
                                />
                            </div>
                        </div>

                        {/* Standard-Vorlage Checkbox */}
                        <label className="flex items-center gap-2 cursor-pointer">
                            <input type="checkbox" checked={templateForm.is_default} onChange={e => setTemplateForm({ ...templateForm, is_default: e.target.checked })} className="w-4 h-4 rounded" />
                            <span className="text-sm text-text-secondary">{t('settings.defaultTemplate')}</span>
                        </label>

                        {/* Records */}
                        <div className="border-t border-border pt-4">
                            <h3 className="text-sm font-semibold text-text-primary mb-3">{t('templates.standardDnsRecords')}</h3>
                            <p className="text-xs text-text-muted mb-3">{t('settings.templateRecordsHint')}</p>

                            {/* Existing records */}
                            {templateForm.records.length > 0 && (
                                <div className="space-y-1 mb-3">
                                    {templateForm.records.map((r, i) => (
                                        <div key={i} className="flex items-center gap-2 p-2 rounded-lg bg-bg-primary border border-border text-sm">
                                            <span className="font-mono text-text-secondary flex-1">
                                                <span className="text-accent-light">{r.name}</span>{' '}
                                                <span className="text-text-muted">{r.type}</span>{' '}
                                                {r.prio != null && <span className="text-warning">{r.prio} </span>}
                                                <span className="text-text-primary">{r.content}</span>{' '}
                                                <span className="text-text-muted">TTL:{r.ttl}</span>
                                            </span>
                                            <button type="button" onClick={() => removeRecordFromTemplate(i)} className="p-1 rounded hover:bg-danger/10 text-text-muted hover:text-danger" title={t('common.delete')} aria-label={t('common.delete')}>
                                                <Trash2 className="w-3 h-3" />
                                            </button>
                                        </div>
                                    ))}
                                </div>
                            )}

                            {/* Add new record row – flex-wrap, genug Platz für Prio (nur MX) */}
                            <div className="space-y-2">
                                <div className="flex flex-wrap items-start gap-3">
                                    <div className="min-w-[7rem] w-[28%] max-w-[10rem] shrink-0">
                                        <label className="block text-xs font-medium text-text-secondary mb-1">{t('zoneDetail.name')}</label>
                                        <input type="text" value={newRecord.name} onChange={e => setNewRecord({ ...newRecord, name: e.target.value })}
                                            placeholder="@" className="w-full h-9 px-2.5 text-xs rounded-lg border border-border bg-bg-primary" />
                                    </div>
                                    <div className="min-w-[5.5rem] w-24 shrink-0">
                                        <label className="block text-xs font-medium text-text-secondary mb-1">{t('dashboard.type')}</label>
                                        <select value={newRecord.type} onChange={e => setNewRecord({ ...newRecord, type: e.target.value })} className="w-full h-9 px-2 text-xs rounded-lg border border-border bg-bg-primary">
                                            {ALL_RECORD_TYPE_KEYS.map((tp) => (
                                                <option key={tp} value={tp}>{tp}</option>
                                            ))}
                                        </select>
                                    </div>
                                    <div className="min-w-[12rem] flex-1 basis-[min(100%,18rem)]">
                                        <label className="block text-xs font-medium text-text-secondary mb-1">
                                            {t(`templates.recordLabel${newRecord.type}`, { defaultValue: t('templates.recordLabelRdata') })}
                                        </label>
                                        <input type="text" value={newRecord.content} onChange={e => setNewRecord({ ...newRecord, content: e.target.value })}
                                            placeholder={TEMPLATE_CONTENT_PLACEHOLDERS[newRecord.type] || '…'}
                                            className="w-full min-w-0 h-9 px-2.5 text-xs rounded-lg border border-border bg-bg-primary" />
                                    </div>
                                    <div className="min-w-[12rem] w-56 shrink-0">
                                        <label htmlFor="template-record-ttl" className="block text-xs font-medium text-text-secondary mb-1">{t('zoneDetail.ttl')}</label>
                                        <TtlInput
                                            id="template-record-ttl"
                                            value={newRecord.ttl}
                                            onChange={(v) => setNewRecord((r) => ({ ...r, ttl: v }))}
                                        />
                                    </div>
                                    {newRecord.type === 'MX' && (
                                        <div className="min-w-[6.5rem] w-28 shrink-0">
                                            <label className="block text-xs font-medium text-text-secondary mb-1">{t('zoneDetail.fieldPriority')}</label>
                                            <input type="number" value={newRecord.prio ?? ''} onChange={e => setNewRecord({ ...newRecord, prio: parseInt(e.target.value, 10) || 0 })}
                                                placeholder="10" className="w-full h-9 px-2.5 text-xs rounded-lg border border-border bg-bg-primary" />
                                        </div>
                                    )}
                                    <div className="min-w-[8.5rem] shrink-0 self-start">
                                        <label className="block text-xs font-medium text-text-secondary mb-1 opacity-0 pointer-events-none select-none" aria-hidden="true">.</label>
                                        <button type="button" onClick={addRecordToTemplate}
                                            className="w-full h-9 px-3 text-xs font-medium border border-accent/40 text-accent-light rounded-lg hover:bg-accent/10 flex items-center justify-center gap-1">
                                            <Plus className="w-3.5 h-3.5" /> {t('settings.add')}
                                        </button>
                                    </div>
                                </div>
                                <DnsRecordTypeHint recordType={newRecord.type} compact />
                            </div>
                        </div>

                        <div className="flex justify-end gap-3 pt-2 border-t border-border">
                            <button type="button" onClick={closeTemplateModal} disabled={savingTemplate} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-50">
                                {t('common.cancel')}
                            </button>
                            <button type="submit" disabled={savingTemplate} className="px-5 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                                {savingTemplate && <Loader2 className="w-4 h-4 animate-spin" />}
                                {editTemplateId ? t('templates.saveButton') : t('templates.createButton')}
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        )}
        </>
    )
}
