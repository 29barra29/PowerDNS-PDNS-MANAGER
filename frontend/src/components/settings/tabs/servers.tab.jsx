import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Server, Plus, Trash2, Pencil, Loader2, AlertCircle, AlertTriangle, CheckCircle2, RefreshCw, Wifi, WifiOff, Eye, EyeOff, X, Zap } from 'lucide-react'
import api from '../../../api'
import ModalErrorBanner from '../../ModalErrorBanner'
import { useDialogFocus } from '../../../lib/useDialogFocus'
import { useSettings } from '../settingsContext'
import ModalPortal from '../../common/ModalPortal'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'servers', order: 30, labelKey: 'settings.servers', icon: Server, adminOnly: true }

// Tab "Server" (PowerDNS-Server verwalten).
// F8 (Welle 1): "Anzeigen" holt den Key immer frisch, Fehler im Dialog (C01, D07); gesendet wird der Key nur,
// wenn das Feld geaendert wurde (f90); Texte uebersetzt (A11); Dialog-Fehler ueber ModalErrorBanner (C05).
// F5 (WS-F5-FE): Badges je `api_key_status` (unreadable/missing); ein solcher Server ist nicht geladen und wird bei
// Zonen-Aenderungen uebersprungen [D4] – Hinweis in der Liste und nach dem Neu-Eintragen (Zonen abgleichen).
// Im Dialog: kein "Anzeigen" ohne lesbaren Key, Hinweisbox, Speichern nur mit neuem Key. Verlangt das Backend den
// Key nach einer URL-Aenderung neu (400 `secret_reentry_required` [S3]), erscheint der Hinweis am Key-Feld.

// Status des gespeicherten Keys; fehlt das Feld (aelteres Backend), gilt der Key als gesetzt.
function keyStatus(s) {
    return s?.api_key_status === 'unreadable' || s?.api_key_status === 'missing' ? s.api_key_status : 'set'
}
export default function ServersTab() {
    const { t } = useTranslation()
    const { notify, isAdmin } = useSettings()
    const setError = notify.error
    const setSuccess = notify.success
    const [servers, setServers] = useState([])
    const [loadingServers, setLoadingServers] = useState(true)
    const [revealingKey, setRevealingKey] = useState(false)
    // true, sobald der Admin das Key-Feld selbst geaendert hat; nur dann wird api_key beim Bearbeiten gesendet
    const [apiKeyDirty, setApiKeyDirty] = useState(false)
    // api_key_status des bearbeiteten Servers ('set' beim Anlegen) und ob er aktiv ist
    const [editKeyStatus, setEditKeyStatus] = useState('set')
    const [editIsActive, setEditIsActive] = useState(true)
    // Backend verlangt den Key neu (URL geaendert) bzw. Pflicht-Key fehlt -> Feldhinweis
    const [apiKeyFieldHint, setApiKeyFieldHint] = useState('')
    // Nach dem Neu-Eintragen eines Keys fuer einen nicht geladenen Server: Hinweis "Zonen abgleichen" [D4]
    const [syncHintServer, setSyncHintServer] = useState('')

    // Add/Edit Server
    const [showForm, setShowForm] = useState(false)
    const [editId, setEditId] = useState(null)
    const [form, setForm] = useState({ name: '', display_name: '', url: '', api_key: '', description: '' })
    const [saving, setSaving] = useState(false)
    const [showApiKey, setShowApiKey] = useState(false)

    // Test connection
    const [testing, setTesting] = useState(false)
    const [testResult, setTestResult] = useState(null)
    // Modal-spezifische Fehler – damit man sie nicht hinter dem Overlay verliert
    const [serverModalError, setServerModalError] = useState('')


    async function loadServers() {
        try {
            const data = await api.getServerConfigs()
            setServers(data.servers || [])
        } catch (err) {
            setError(err.message)
        } finally {
            setLoadingServers(false)
        }
    }

    // ===== Server functions =====
    function openAdd() {
        setEditId(null)
        setForm({ name: '', display_name: '', url: '', api_key: '', description: '', allow_writes: true })
        setTestResult(null)
        setShowApiKey(false)
        setApiKeyDirty(false)
        setEditKeyStatus('set')
        setEditIsActive(true)
        setApiKeyFieldHint('')
        setServerModalError('')
        setShowForm(true)
    }

    function openEdit(s) {
        setEditId(s.id)
        setForm({
            name: s.name,
            display_name: s.display_name || '',
            url: s.url,
            // API-Key wird beim Bearbeiten NIE vorausgefüllt. Leeres Feld = Backend behält den bestehenden Schlüssel.
            // Admin kann auf "Anzeigen" klicken, um den aktuellen Schlüssel auf Anforderung zu sehen (audit-loggt).
            api_key: '',
            description: s.description || '',
            allow_writes: s.allow_writes !== false,
        })
        setTestResult(null)
        setShowApiKey(false)
        setApiKeyDirty(false)
        setEditKeyStatus(keyStatus(s))
        setEditIsActive(s.is_active !== false)
        setApiKeyFieldHint('')
        setServerModalError('')
        setShowForm(true)
    }

    function closeServerModal() {
        setShowForm(false)
        setServerModalError('')
        setApiKeyFieldHint('')
        setTestResult(null)
    }

    const serverDialogRef = useDialogFocus({ onClose: closeServerModal, canClose: !saving, active: showForm })

    // Key immer frisch vom Server holen (kein Cache, f90); Fehler erscheinen im Dialog (f91)
    async function handleRevealApiKey() {
        if (!editId || editKeyStatus !== 'set') return
        setRevealingKey(true)
        setServerModalError('')
        try {
            const res = await api.revealServerApiKey(editId)
            const key = res?.api_key || ''
            setForm((prev) => ({ ...prev, api_key: key }))
            setApiKeyDirty(false)
            setShowApiKey(true)
        } catch (err) {
            setServerModalError(err.message || t('settings.apiKeyRevealFailed'))
        } finally {
            setRevealingKey(false)
        }
    }

    async function handleTest() {
        if (!form.url || !form.api_key) {
            setTestResult({ success: false, error: t('settings.testNeedsUrlAndKey') })
            return
        }
        setTesting(true)
        setTestResult(null)
        try {
            const result = await api.testConnection({ url: form.url, api_key: form.api_key })
            setTestResult(result)
        } catch (err) {
            setTestResult({ success: false, error: err.message })
        } finally {
            setTesting(false)
        }
    }

    async function handleSave(e) {
        e.preventDefault()
        // Nicht lesbarer/fehlender Key: ohne neuen Key bliebe der aktive Server ungeladen (F5 §2 D). Ein
        // deaktivierter Server ist ohnehin nicht geladen – dort darf z. B. die Beschreibung ohne Key gespeichert werden.
        const needsKey = !!editId && editKeyStatus !== 'set' && editIsActive
        if (needsKey && !(apiKeyDirty && form.api_key.trim())) {
            setApiKeyFieldHint(t('settingsMore.apiKeyRequiredUnreadable'))
            setServerModalError(t('settingsMore.apiKeyRequiredUnreadable'))
            return
        }
        setSaving(true)
        setServerModalError('')
        setApiKeyFieldHint('')
        try {
            if (editId) {
                await api.updateServerConfig(editId, {
                    display_name: form.display_name,
                    url: form.url,
                    // '' = gespeicherten Key behalten; ein nur angezeigter Key wird nicht zurueckgeschickt (f90)
                    api_key: apiKeyDirty ? form.api_key : '',
                    description: form.description,
                    allow_writes: form.allow_writes,
                })
                setSuccess(t('settings.serverUpdated'))
                // Server war nicht geladen und hat jetzt einen Key: Zonen koennen auf ihm veraltet sein [D4]
                if (needsKey) setSyncHintServer(form.display_name || form.name)
            } else {
                await api.addServerConfig(form)
                setSuccess(t('settings.serverAdded'))
            }
            closeServerModal()
            loadServers()
        } catch (err) {
            if (err?.code === 'secret_reentry_required') setApiKeyFieldHint(t('settingsMore.apiKeyReentryRequired'))
            setServerModalError(err.message)
        } finally {
            setSaving(false)
        }
    }

    async function handleDelete(id, name) {
        if (!window.confirm(t('settings.serverDeleteConfirm', { name }))) return
        try {
            await api.deleteServerConfig(id)
            setSuccess(t('settings.serverDeleted', { name }))
            loadServers()
        } catch (err) {
            setError(err.message)
        }
    }

    async function toggleActive(s) {
        try {
            await api.updateServerConfig(s.id, { is_active: !s.is_active })
            loadServers()
        } catch (err) {
            setError(err.message)
        }
    }

    async function toggleAllowWrites(s) {
        try {
            await api.updateServerConfig(s.id, { allow_writes: !(s.allow_writes !== false) })
            loadServers()
        } catch (err) {
            setError(err.message)
        }
    }

    useEffect(() => {
        if (!isAdmin) return
        // eslint-disable-next-line react-hooks/set-state-in-effect -- Laden beim Aktivieren wie 2.4.1
        loadServers()
    }, []) // eslint-disable-line react-hooks/exhaustive-deps -- nur beim Aktivieren laden (wie 2.4.1)

    // Zusaetzliches Render-Gate (F8 6.7): der Tab ist adminOnly, die Daten gibt es ohnehin nur fuer Admins
    if (!isAdmin) return null

    return (
        <>
            <div className="space-y-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <h2 className="text-lg font-semibold text-text-primary">{t('settingsMore.powerDnsServers')}</h2>
                    <div className="flex gap-2 flex-wrap">
                        <button onClick={loadServers} className="flex items-center gap-2 px-3 py-2 text-sm text-text-muted hover:text-text-primary hover:bg-bg-hover rounded-lg transition-colors border border-border">
                            <RefreshCw className="w-4 h-4" /> {t('settingsMore.refresh')}
                        </button>
                        <button onClick={openAdd} className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all">
                            <Plus className="w-4 h-4" /> {t('settingsMore.addServer')}
                        </button>
                    </div>
                </div>

                {/* Info-Box: DNS Server werden in der Datenbank gespeichert */}
                <div className="p-4 rounded-xl bg-accent/5 border border-accent/20">
                    <p className="text-sm text-text-secondary">
                        <strong className="text-accent-light">💡 {t('common.hint')}:</strong> {t('settings.serversHint')}
                        {t('settingsMore.serversManagedHint')}
                    </p>
                </div>

                {syncHintServer && (
                    <div role="status" className="p-4 rounded-xl bg-warning/10 border border-warning/30 text-warning flex items-start gap-3">
                        <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" aria-hidden="true" />
                        <p className="flex-1 min-w-0 text-sm break-words">{t('settingsMore.apiKeyReenteredSyncHint', { name: syncHintServer })}</p>
                        <button type="button" onClick={() => setSyncHintServer('')} className="p-1 rounded-lg hover:bg-warning/10 shrink-0" title={t('common.close')} aria-label={t('common.close')}>
                            <X className="w-4 h-4" />
                        </button>
                    </div>
                )}

                {loadingServers ? (
                    <div className="flex justify-center py-8"><Loader2 className="w-6 h-6 text-accent animate-spin" /></div>
                ) : servers.length === 0 ? (
                    <div className="glass-card p-12 text-center">
                        <Server className="w-16 h-16 mx-auto mb-4 text-text-muted opacity-30" />
                        <h3 className="text-lg font-semibold text-text-primary mb-2">{t('settingsMore.noServerConfigured')}</h3>
                        <p className="text-sm text-text-muted mb-4">
                            {t('settingsMore.noServerHint')}
                        </p>
                        <button onClick={openAdd} className="px-6 py-2.5 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg font-medium text-sm">
                            <Plus className="w-4 h-4 inline mr-2" /> {t('settingsMore.addFirstServer')}
                        </button>
                    </div>
                ) : (
                    <div className="space-y-3">
                        {servers.map(s => (
                            <div key={s.id} className={`glass-card p-5 ${!s.is_active ? 'opacity-50' : ''}`}>
                                <div className="flex items-start gap-4">
                                    {/* Status */}
                                    <div className={`w-12 h-12 rounded-xl flex items-center justify-center shrink-0 ${s.is_online ? 'bg-success/20' : 'bg-danger/20'
                                        }`}>
                                        {s.is_online ? <Wifi className="w-6 h-6 text-success" /> : <WifiOff className="w-6 h-6 text-danger" />}
                                    </div>

                                    {/* Info */}
                                    <div className="flex-1 min-w-0">
                                        <div className="flex items-center gap-2 flex-wrap">
                                            <h3 className="font-semibold text-text-primary">{s.display_name || s.name}</h3>
                                            <span className="text-xs px-2 py-0.5 rounded-full bg-bg-hover text-text-muted border border-border font-mono">{s.name}</span>
                                            <span className={`text-xs px-2 py-0.5 rounded-full ${s.is_online
                                                ? 'bg-success/10 text-success border border-success/30'
                                                : 'bg-danger/10 text-danger border border-danger/30'
                                                }`}>
                                                {s.is_online ? t('dashboard.online') : t('dashboard.offline')}
                                            </span>
                                            {!s.is_active && (
                                                <span className="text-xs px-2 py-0.5 rounded-full bg-warning/10 text-warning border border-warning/30">{t('settings.serverDisabledBadge')}</span>
                                            )}
                                            {keyStatus(s) === 'unreadable' && (
                                                <span className="text-xs px-2 py-0.5 rounded-full bg-danger/10 text-danger border border-danger/30">{t('settingsMore.apiKeyUnreadable')}</span>
                                            )}
                                            {keyStatus(s) === 'missing' && (
                                                <span className="text-xs px-2 py-0.5 rounded-full bg-warning/10 text-warning border border-warning/30">{t('settingsMore.apiKeyMissing')}</span>
                                            )}
                                            <button type="button" onClick={() => toggleAllowWrites(s)} className={`text-xs px-2 py-0.5 rounded-full border cursor-pointer hover:opacity-80 transition-opacity ${s.allow_writes !== false ? 'bg-success/10 text-success border-success/30' : 'bg-bg-hover text-text-muted border-border'}`} title={s.allow_writes !== false ? t('settings.allowWritesTitleOn') : t('settings.allowWritesTitleOff')}>
                                                {s.allow_writes !== false ? t('settings.allowWritesYes') : t('settings.allowWritesNo')}
                                            </button>
                                        </div>
                                        <p className="text-sm text-text-muted font-mono mt-1">{s.url}</p>
                                        <div className="flex items-center gap-4 mt-2 text-xs text-text-muted">
                                            {s.version && <span>{t('dashboard.version')}: <span className="text-text-secondary">{s.version}</span></span>}
                                            {s.zone_count != null && <span>{t('settings.zonesCount')}: <span className="text-text-secondary">{s.zone_count}</span></span>}
                                            {s.description && <span className="italic">{s.description}</span>}
                                        </div>
                                        {s.is_active && keyStatus(s) !== 'set' && (
                                            <p className="mt-2 text-xs text-warning flex items-start gap-1.5">
                                                <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-px" aria-hidden="true" />
                                                <span>{t('settingsMore.apiKeyNotLoadedHint')}</span>
                                            </p>
                                        )}
                                    </div>

                                    {/* Actions */}
                                    <div className="flex items-center gap-1 shrink-0">
                                        <button onClick={() => openEdit(s)} className="p-2 rounded-lg text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors" title={t('common.edit')} aria-label={t('common.edit')}>
                                            <Pencil className="w-4 h-4" />
                                        </button>
                                        <button onClick={() => toggleActive(s)} className={`p-2 rounded-lg transition-colors ${s.is_active ? 'text-text-muted hover:text-warning hover:bg-warning/10' : 'text-success hover:bg-success/10'
                                            }`} title={s.is_active ? t('settings.serverDeactivate') : t('settings.serverActivate')} aria-label={s.is_active ? t('settings.serverDeactivate') : t('settings.serverActivate')}>
                                            {s.is_active ? <WifiOff className="w-4 h-4" /> : <Wifi className="w-4 h-4" />}
                                        </button>
                                        <button onClick={() => handleDelete(s.id, s.name)} className="p-2 rounded-lg text-text-muted hover:text-danger hover:bg-danger/10 transition-colors" title={t('common.delete')} aria-label={t('common.delete')}>
                                            <Trash2 className="w-4 h-4" />
                                        </button>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>

        {/* =================== ADD/EDIT SERVER MODAL =================== */}
        {showForm && (
            <ModalPortal>
                <div
                    className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
                    onClick={() => { if (!saving) closeServerModal() }}
                >
                    <div
                        ref={serverDialogRef}
                        role="dialog"
                        aria-modal="true"
                        aria-labelledby="server-modal-title"
                        className="glass-card p-6 w-full max-w-xl max-h-[90vh] overflow-y-auto"
                        onClick={e => e.stopPropagation()}
                    >
                        <div className="flex items-center justify-between mb-5">
                            <h2 id="server-modal-title" className="text-lg font-bold text-text-primary">
                                {editId ? t('settingsMore.editServer') : t('settingsMore.addNewServer')}
                            </h2>
                            <button type="button" onClick={closeServerModal} disabled={saving} className="p-1 rounded-lg hover:bg-bg-hover text-text-muted disabled:opacity-50" title={t('common.close')} aria-label={t('common.close')}>
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        <ModalErrorBanner
                            message={serverModalError}
                            title={editId ? t('settingsMore.updateErrorTitle') : t('settingsMore.createErrorTitle')}
                            onClose={() => setServerModalError('')}
                        />

                        <form onSubmit={handleSave} className="space-y-4">
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('settingsMore.serverName')} *</label>
                                    <input
                                        type="text" value={form.name}
                                        onChange={e => setForm({ ...form, name: e.target.value })}
                                        placeholder="server1" className="w-full px-3 py-2 text-sm"
                                        required disabled={!!editId} minLength={1}
                                        data-autofocus={!editId ? true : undefined}
                                    />
                                    <p className="text-xs text-text-muted mt-0.5">{t('settingsMore.serverNameHint')}</p>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">{t('settingsMore.serverDisplayName')}</label>
                                    <input
                                        type="text" value={form.display_name}
                                        onChange={e => setForm({ ...form, display_name: e.target.value })}
                                        placeholder={t('settings.nameserverPlaceholder')} className="w-full px-3 py-2 text-sm"
                                        data-autofocus={editId && editKeyStatus === 'set' ? true : undefined}
                                    />
                                </div>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('settingsMore.powerDnsApiUrl')} *</label>
                                <input
                                    type="url" value={form.url}
                                    onChange={e => setForm({ ...form, url: e.target.value })}
                                    placeholder={t('settings.pdnsApiUrlPlaceholder')} className="w-full px-3 py-2 text-sm"
                                    required
                                />
                                <p className="text-xs text-text-muted mt-0.5">{t('settingsMore.powerDnsApiUrlHint')}</p>
                            </div>

                            <div>
                                <label htmlFor="server-api-key" className="block text-sm font-medium text-text-secondary mb-1">
                                    {t('settingsMore.apiKey')}{!editId || (editKeyStatus !== 'set' && editIsActive) ? ' *' : ''}
                                </label>
                                {editId && editKeyStatus !== 'set' && (
                                    <div role="alert" className={`mb-2 p-3 rounded-lg border text-sm flex items-start gap-2 ${editKeyStatus === 'unreadable' ? 'bg-danger/10 border-danger/30 text-danger' : 'bg-warning/10 border-warning/30 text-warning'}`}>
                                        <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />
                                        <div className="space-y-1">
                                            <p>{editKeyStatus === 'unreadable' ? t('settingsMore.apiKeyUnreadableHint') : t('settingsMore.apiKeyMissingHint')}</p>
                                            {editIsActive && <p className="text-xs">{t('settingsMore.apiKeyNotLoadedHint')}</p>}
                                        </div>
                                    </div>
                                )}
                                <div className="relative">
                                    <input
                                        id="server-api-key"
                                        type={showApiKey ? 'text' : 'password'} value={form.api_key}
                                        onChange={e => { setForm({ ...form, api_key: e.target.value }); setApiKeyDirty(true); setApiKeyFieldHint('') }}
                                        placeholder={editId && editKeyStatus === 'set' ? t('settings.apiKeyKeepPlaceholder') : t('settings.apiKeyPlaceholder')}
                                        className="w-full px-3 py-2 pr-20 text-sm"
                                        required={!editId || (editKeyStatus !== 'set' && editIsActive)}
                                        maxLength={500}
                                        autoComplete="off"
                                        aria-invalid={apiKeyFieldHint ? true : undefined}
                                        aria-describedby={apiKeyFieldHint ? 'server-api-key-hint' : undefined}
                                        data-autofocus={editId && editKeyStatus !== 'set' ? true : undefined}
                                    />
                                    <button type="button" onClick={() => setShowApiKey(!showApiKey)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary">
                                        {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                    </button>
                                </div>
                                {apiKeyFieldHint && (
                                    <p id="server-api-key-hint" className="mt-1 text-xs text-warning">{apiKeyFieldHint}</p>
                                )}
                                {/* "Anzeigen" nur bei lesbarem Key (unlesbar/fehlend -> Backend 409, F5 §2 D) */}
                                {editId && editKeyStatus === 'set' && (
                                    <button
                                        type="button"
                                        onClick={handleRevealApiKey}
                                        disabled={revealingKey}
                                        className="mt-1 text-xs text-accent-light hover:text-accent flex items-center gap-1"
                                    >
                                        {revealingKey ? <Loader2 className="w-3 h-3 animate-spin" /> : <Eye className="w-3 h-3" />}
                                        {t('settings.revealExistingApiKey')}
                                    </button>
                                )}
                                <p className="text-xs text-text-muted mt-0.5">{t('settingsMore.apiKeyHint')}</p>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('settingsMore.description')}</label>
                                <input
                                    type="text" value={form.description}
                                    onChange={e => setForm({ ...form, description: e.target.value })}
                                    placeholder={t('settings.serverDescriptionPlaceholder')} className="w-full px-3 py-2 text-sm"
                                />
                            </div>

                            <label className="flex items-center gap-3 cursor-pointer p-3 rounded-lg border border-border hover:bg-bg-hover/50 transition-colors">
                                <input
                                    type="checkbox"
                                    checked={form.allow_writes !== false}
                                    onChange={e => setForm({ ...form, allow_writes: e.target.checked })}
                                    className="w-4 h-4 rounded"
                                />
                                <div>
                                    <span className="text-sm font-medium text-text-primary">{t('settingsMore.saveOnThisServer')}</span>
                                    <p className="text-xs text-text-muted mt-0.5">{t('settings.writeToServerHint')}</p>
                                </div>
                            </label>

                            {/* Test Connection */}
                            <div className="border-t border-border pt-4">
                                <button
                                    type="button" onClick={handleTest} disabled={testing}
                                    className="flex items-center gap-2 px-4 py-2 text-sm font-medium border border-accent/40 text-accent-light rounded-lg hover:bg-accent/10 disabled:opacity-50 transition-all"
                                >
                                    {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
                                    {t('settingsMore.testConnection')}
                                </button>

                                {testResult && (
                                    <div className={`mt-3 p-3 rounded-lg text-sm ${testResult.success
                                        ? 'bg-success/10 border border-success/30 text-success'
                                        : 'bg-danger/10 border border-danger/30 text-danger'
                                        }`}>
                                        {testResult.success ? (
                                            <div>
                                                <div className="flex items-center gap-2 font-medium mb-1">
                                                    <CheckCircle2 className="w-4 h-4" /> {t('settingsMore.connectionSuccess')}
                                                </div>
                                                <div className="text-xs space-y-0.5 text-text-secondary">
                                                    <p>{t('settingsMore.version')}: {testResult.server_info.version}</p>
                                                    <p>{t('dashboard.type')}: {testResult.server_info.daemon_type}</p>
                                                    <p>{t('settings.zonesCount')}: {testResult.server_info.zone_count}</p>
                                                </div>
                                            </div>
                                        ) : (
                                            <div className="flex items-center gap-2">
                                                <AlertCircle className="w-4 h-4 shrink-0" />
                                                <span>{testResult.error}</span>
                                            </div>
                                        )}
                                    </div>
                                )}
                            </div>

                            <div className="flex justify-end gap-3 pt-2 border-t border-border">
                                <button type="button" onClick={closeServerModal} disabled={saving} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-50">
                                    {t('common.cancel')}
                                </button>
                                <button type="submit" disabled={saving} className="px-5 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                                    {saving && <Loader2 className="w-4 h-4 animate-spin" />}
                                    {editId ? t('common.save') : t('settings.add')}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            </ModalPortal>
        )}
        </>
    )
}
