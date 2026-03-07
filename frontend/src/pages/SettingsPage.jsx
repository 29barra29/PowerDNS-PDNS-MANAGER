import { useState, useEffect } from 'react'
import { Settings, Server, Database, Plus, Trash2, Pencil, Loader2, AlertCircle, CheckCircle2, RefreshCw, Wifi, WifiOff, Eye, EyeOff, X, Zap } from 'lucide-react'
import api from '../api'

export default function SettingsPage() {
    const [activeTab, setActiveTab] = useState('servers')
    const [servers, setServers] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState('')
    const [success, setSuccess] = useState('')

    // Add/Edit Server
    const [showForm, setShowForm] = useState(false)
    const [editId, setEditId] = useState(null)
    const [form, setForm] = useState({ name: '', display_name: '', url: '', api_key: '', description: '' })
    const [saving, setSaving] = useState(false)
    const [showApiKey, setShowApiKey] = useState(false)

    // Test connection
    const [testing, setTesting] = useState(false)
    const [testResult, setTestResult] = useState(null)

    useEffect(() => { loadServers() }, [])

    async function loadServers() {
        setLoading(true)
        try {
            const data = await api.getServerConfigs()
            setServers(data.servers || [])
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    function openAdd() {
        setEditId(null)
        setForm({ name: '', display_name: '', url: '', api_key: '', description: '' })
        setTestResult(null)
        setShowApiKey(false)
        setShowForm(true)
    }

    function openEdit(s) {
        setEditId(s.id)
        setForm({
            name: s.name,
            display_name: s.display_name || '',
            url: s.url,
            api_key: s.api_key_full || '',
            description: s.description || '',
        })
        setTestResult(null)
        setShowApiKey(false)
        setShowForm(true)
    }

    async function handleTest() {
        if (!form.url || !form.api_key) {
            setTestResult({ success: false, error: 'URL und API-Key eingeben!' })
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
        setSaving(true)
        setError('')
        try {
            if (editId) {
                await api.updateServerConfig(editId, {
                    display_name: form.display_name,
                    url: form.url,
                    api_key: form.api_key,
                    description: form.description,
                })
                setSuccess('Server aktualisiert!')
            } else {
                await api.addServerConfig(form)
                setSuccess('Server hinzugefügt!')
            }
            setShowForm(false)
            loadServers()
        } catch (err) {
            setError(err.message)
        } finally {
            setSaving(false)
        }
    }

    async function handleDelete(id, name) {
        if (!confirm(`Server "${name}" wirklich löschen? Alle Verbindungsdaten gehen verloren!`)) return
        try {
            await api.deleteServerConfig(id)
            setSuccess(`Server "${name}" gelöscht`)
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

    const tabs = [
        { id: 'servers', label: 'DNS-Server', icon: Server },
        { id: 'about', label: 'Über', icon: Database },
    ]

    if (loading) return <div className="flex items-center justify-center h-64"><Loader2 className="w-8 h-8 text-accent animate-spin" /></div>

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-text-primary">Einstellungen</h1>
                <p className="text-text-muted text-sm mt-1">Systemkonfiguration und Server-Verwaltung</p>
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
                    <CheckCircle2 className="w-5 h-5 shrink-0" />
                    <p className="text-sm">{success}</p>
                    <button onClick={() => setSuccess('')} className="ml-auto text-xs hover:underline">×</button>
                </div>
            )}

            {/* Tabs */}
            <div className="flex gap-1 p-1 bg-bg-secondary rounded-xl border border-border">
                {tabs.map(tab => (
                    <button
                        key={tab.id}
                        onClick={() => setActiveTab(tab.id)}
                        className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition-all ${activeTab === tab.id
                                ? 'bg-accent/20 text-accent-light'
                                : 'text-text-muted hover:text-text-primary hover:bg-bg-hover'
                            }`}
                    >
                        <tab.icon className="w-4 h-4" />
                        {tab.label}
                    </button>
                ))}
            </div>

            {/* =================== SERVERS TAB =================== */}
            {activeTab === 'servers' && (
                <div className="space-y-4">
                    <div className="flex items-center justify-between">
                        <h2 className="text-lg font-semibold text-text-primary">PowerDNS Server</h2>
                        <div className="flex gap-2">
                            <button onClick={loadServers} className="flex items-center gap-2 px-3 py-2 text-sm text-text-muted hover:text-text-primary hover:bg-bg-hover rounded-lg transition-colors border border-border">
                                <RefreshCw className="w-4 h-4" /> Aktualisieren
                            </button>
                            <button onClick={openAdd} className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all">
                                <Plus className="w-4 h-4" /> Server hinzufügen
                            </button>
                        </div>
                    </div>

                    {servers.length === 0 ? (
                        <div className="glass-card p-12 text-center">
                            <Server className="w-16 h-16 mx-auto mb-4 text-text-muted opacity-30" />
                            <h3 className="text-lg font-semibold text-text-primary mb-2">Kein Server konfiguriert</h3>
                            <p className="text-sm text-text-muted mb-4">
                                Füge deinen ersten PowerDNS-Server hinzu, um loszulegen.<br />
                                Du brauchst die URL und den API-Key deines PowerDNS-Servers.
                            </p>
                            <button onClick={openAdd} className="px-6 py-2.5 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg font-medium text-sm">
                                <Plus className="w-4 h-4 inline mr-2" /> Ersten Server hinzufügen
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
                                                    {s.is_online ? 'Online' : 'Offline'}
                                                </span>
                                                {!s.is_active && (
                                                    <span className="text-xs px-2 py-0.5 rounded-full bg-warning/10 text-warning border border-warning/30">Deaktiviert</span>
                                                )}
                                            </div>
                                            <p className="text-sm text-text-muted font-mono mt-1">{s.url}</p>
                                            <div className="flex items-center gap-4 mt-2 text-xs text-text-muted">
                                                {s.version && <span>Version: <span className="text-text-secondary">{s.version}</span></span>}
                                                {s.zone_count != null && <span>Zonen: <span className="text-text-secondary">{s.zone_count}</span></span>}
                                                {s.description && <span className="italic">{s.description}</span>}
                                            </div>
                                        </div>

                                        {/* Actions */}
                                        <div className="flex items-center gap-1 shrink-0">
                                            <button onClick={() => openEdit(s)} className="p-2 rounded-lg text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors" title="Bearbeiten">
                                                <Pencil className="w-4 h-4" />
                                            </button>
                                            <button onClick={() => toggleActive(s)} className={`p-2 rounded-lg transition-colors ${s.is_active ? 'text-text-muted hover:text-warning hover:bg-warning/10' : 'text-success hover:bg-success/10'
                                                }`} title={s.is_active ? 'Deaktivieren' : 'Aktivieren'}>
                                                {s.is_active ? <WifiOff className="w-4 h-4" /> : <Wifi className="w-4 h-4" />}
                                            </button>
                                            <button onClick={() => handleDelete(s.id, s.name)} className="p-2 rounded-lg text-text-muted hover:text-danger hover:bg-danger/10 transition-colors" title="Löschen">
                                                <Trash2 className="w-4 h-4" />
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            )}

            {/* =================== ABOUT TAB =================== */}
            {activeTab === 'about' && (
                <div className="glass-card p-5">
                    <h2 className="text-lg font-semibold text-text-primary mb-4">Über DNS Manager</h2>
                    <div className="space-y-3">
                        {[
                            ['Version', '2.0.0'],
                            ['Frontend', 'React + Tailwind CSS'],
                            ['Backend', 'Python FastAPI'],
                            ['Datenbank', 'MariaDB 11'],
                            ['DNS-Engine', 'PowerDNS Authoritative'],
                            ['Auth', 'JWT (Bearer Token)'],
                        ].map(([k, v]) => (
                            <div key={k} className="flex justify-between p-3 bg-bg-primary rounded-lg border border-border">
                                <span className="text-sm text-text-muted">{k}</span>
                                <span className="text-sm font-medium text-text-primary">{v}</span>
                            </div>
                        ))}
                    </div>
                    <div className="mt-4 p-4 bg-accent/5 rounded-xl border border-accent/20">
                        <p className="text-sm text-text-secondary">
                            DNS Manager ist ein selbst gehostetes Admin-Panel für PowerDNS.
                            Open Source und kostenlos. 🚀
                        </p>
                    </div>
                </div>
            )}

            {/* =================== ADD/EDIT SERVER MODAL =================== */}
            {showForm && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setShowForm(false)}>
                    <div className="glass-card p-6 w-full max-w-xl max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center justify-between mb-5">
                            <h2 className="text-lg font-bold text-text-primary">
                                {editId ? 'Server bearbeiten' : 'Neuen Server hinzufügen'}
                            </h2>
                            <button onClick={() => setShowForm(false)} className="p-1 rounded-lg hover:bg-bg-hover text-text-muted">
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        <form onSubmit={handleSave} className="space-y-4">
                            <div className="grid grid-cols-2 gap-4">
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">Server-Name *</label>
                                    <input
                                        type="text" value={form.name}
                                        onChange={e => setForm({ ...form, name: e.target.value })}
                                        placeholder="ns1" className="w-full px-3 py-2 text-sm"
                                        required disabled={!!editId} minLength={1}
                                    />
                                    <p className="text-xs text-text-muted mt-0.5">Eindeutig, z.B. ns1, ns2</p>
                                </div>
                                <div>
                                    <label className="block text-sm font-medium text-text-secondary mb-1">Anzeigename</label>
                                    <input
                                        type="text" value={form.display_name}
                                        onChange={e => setForm({ ...form, display_name: e.target.value })}
                                        placeholder="Nameserver 1" className="w-full px-3 py-2 text-sm"
                                    />
                                </div>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">PowerDNS API URL *</label>
                                <input
                                    type="url" value={form.url}
                                    onChange={e => setForm({ ...form, url: e.target.value })}
                                    placeholder="http://192.168.1.10:8081" className="w-full px-3 py-2 text-sm"
                                    required
                                />
                                <p className="text-xs text-text-muted mt-0.5">Die URL zum PowerDNS-Webserver (Standard-Port: 8081)</p>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">API Key *</label>
                                <div className="relative">
                                    <input
                                        type={showApiKey ? 'text' : 'password'} value={form.api_key}
                                        onChange={e => setForm({ ...form, api_key: e.target.value })}
                                        placeholder="dein-api-key" className="w-full px-3 py-2 pr-10 text-sm"
                                        required
                                    />
                                    <button type="button" onClick={() => setShowApiKey(!showApiKey)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-text-muted hover:text-text-primary">
                                        {showApiKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                                    </button>
                                </div>
                                <p className="text-xs text-text-muted mt-0.5">Aus der PowerDNS-Konfiguration (api-key in pdns.conf)</p>
                            </div>

                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">Beschreibung</label>
                                <input
                                    type="text" value={form.description}
                                    onChange={e => setForm({ ...form, description: e.target.value })}
                                    placeholder="Hetzner Server DE, Hauptserver..." className="w-full px-3 py-2 text-sm"
                                />
                            </div>

                            {/* Test Connection */}
                            <div className="border-t border-border pt-4">
                                <button
                                    type="button" onClick={handleTest} disabled={testing || !form.url || !form.api_key}
                                    className="flex items-center gap-2 px-4 py-2 text-sm font-medium border border-accent/40 text-accent-light rounded-lg hover:bg-accent/10 disabled:opacity-50 transition-all"
                                >
                                    {testing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Zap className="w-4 h-4" />}
                                    Verbindung testen
                                </button>

                                {testResult && (
                                    <div className={`mt-3 p-3 rounded-lg text-sm ${testResult.success
                                            ? 'bg-success/10 border border-success/30 text-success'
                                            : 'bg-danger/10 border border-danger/30 text-danger'
                                        }`}>
                                        {testResult.success ? (
                                            <div>
                                                <div className="flex items-center gap-2 font-medium mb-1">
                                                    <CheckCircle2 className="w-4 h-4" /> Verbindung erfolgreich!
                                                </div>
                                                <div className="text-xs space-y-0.5 text-text-secondary">
                                                    <p>Version: {testResult.server_info.version}</p>
                                                    <p>Typ: {testResult.server_info.daemon_type}</p>
                                                    <p>Zonen: {testResult.server_info.zone_count}</p>
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
                                <button type="button" onClick={() => setShowForm(false)} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary">
                                    Abbrechen
                                </button>
                                <button type="submit" disabled={saving} className="px-5 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                                    {saving && <Loader2 className="w-4 h-4 animate-spin" />}
                                    {editId ? 'Speichern' : 'Hinzufügen'}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    )
}
