import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Key, Loader2, Shield, User, Globe, X, Check } from 'lucide-react'
import api from '../api'

export default function UsersPage() {
    const { t } = useTranslation()
    const [users, setUsers] = useState([])
    const [allZones, setAllZones] = useState([])
    const [loading, setLoading] = useState(true)
    const [showCreate, setShowCreate] = useState(false)
    const [editZonesUser, setEditZonesUser] = useState(null)
    const [editPasswordUser, setEditPasswordUser] = useState(null)
    const [newPassword, setNewPassword] = useState('')
    const [selectedZones, setSelectedZones] = useState([])
    const [form, setForm] = useState({ username: '', password: '', display_name: '', role: 'user' })
    const [error, setError] = useState('')
    const [success, setSuccess] = useState('')
    const [saving, setSaving] = useState(false)

    useEffect(() => { loadData() }, [])

    async function loadData() {
        try {
            const [userData, serverData] = await Promise.all([
                api.listUsers(),
                api.getServers(),
            ])
            setUsers(userData.users || [])

            // Alle Zonen von allen Servern laden
            const zones = []
            for (const s of (serverData.servers || [])) {
                if (!s.is_reachable) continue
                try {
                    const zData = await api.listZones(s.name)
                        ; (zData.zones || []).forEach(z => {
                            if (!zones.find(existing => existing.name === z.name)) {
                                zones.push({ name: z.name, server: s.name })
                            }
                        })
                } catch { }
            }
            setAllZones(zones)
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }

    async function handleCreate(e) {
        e.preventDefault()
        setSaving(true)
        setError('')
        try {
            await api.createUser(form)
            setShowCreate(false)
            setForm({ username: '', password: '', display_name: '', role: 'user' })
            loadData()
        } catch (err) {
            setError(err.message)
        } finally {
            setSaving(false)
        }
    }

    async function handleDelete(id, name) {
        if (!confirm(t('users.deleteConfirm', { name }))) return
        try {
            await api.deleteUser(id)
            loadData()
        } catch (err) {
            setError(err.message)
        }
    }

    async function handleSetPassword(e) {
        e.preventDefault()
        setSaving(true)
        setError('')
        setSuccess('')
        try {
            await api.updateUser(editPasswordUser.id, { password: newPassword })
            setSuccess(t('users.passwordChangedSuccess', { name: editPasswordUser.username }))
            setEditPasswordUser(null)
            setNewPassword('')
        } catch (err) {
            setError(err.message)
        } finally {
            setSaving(false)
        }
    }

    async function toggleRole(user) {
        const newRole = user.role === 'admin' ? 'user' : 'admin'
        try {
            await api.updateUser(user.id, { role: newRole })
            loadData()
        } catch (err) {
            setError(err.message)
        }
    }

    function openZoneEditor(user) {
        setEditZonesUser(user)
        setSelectedZones(user.zones || [])
    }

    function toggleZone(zoneName) {
        setSelectedZones(prev =>
            prev.includes(zoneName)
                ? prev.filter(z => z !== zoneName)
                : [...prev, zoneName]
        )
    }

    async function saveZones() {
        if (!editZonesUser) return
        setSaving(true)
        try {
            await api.updateUserZones(editZonesUser.id, selectedZones)
            setEditZonesUser(null)
            loadData()
        } catch (err) {
            setError(err.message)
        } finally {
            setSaving(false)
        }
    }

    if (loading) return <div className="flex items-center justify-center h-64"><Loader2 className="w-8 h-8 text-accent animate-spin" /></div>

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-text-primary">{t('users.title')}</h1>
                    <p className="text-text-muted text-sm mt-1">{t('users.usersCount', { count: users.length })}</p>
                </div>
                <button
                    onClick={() => setShowCreate(true)}
                    className="flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all"
                >
                    <Plus className="w-4 h-4" /> {t('users.newUser')}
                </button>
            </div>

            {error && (
                <div className="p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger text-sm flex justify-between items-center">
                    <span>{error}</span>
                    <button onClick={() => setError('')} className="text-xs hover:underline">×</button>
                </div>
            )}
            {success && (
                <div className="p-4 rounded-xl bg-success/10 border border-success/30 text-success text-sm flex justify-between items-center">
                    <span>{success}</span>
                    <button onClick={() => setSuccess('')} className="text-xs hover:underline">×</button>
                </div>
            )}

            <div className="grid gap-4">
                {users.map(u => (
                    <div key={u.id} className="glass-card p-5">
                        <div className="flex items-start gap-4">
                            {/* Avatar */}
                            <div className={`w-12 h-12 rounded-xl flex items-center justify-center text-lg font-bold shrink-0 ${u.role === 'admin' ? 'bg-accent/20 text-accent-light' : 'bg-bg-hover text-text-muted'
                                }`}>
                                {u.role === 'admin' ? <Shield className="w-6 h-6" /> : <User className="w-6 h-6" />}
                            </div>

                            {/* Info */}
                            <div className="flex-1 min-w-0">
                                <div className="flex items-center gap-2 flex-wrap">
                                    <h3 className="font-semibold text-text-primary">{u.display_name || u.username}</h3>
                                    <span className={`text-xs px-2 py-0.5 rounded-full border ${u.role === 'admin'
                                        ? 'bg-accent/10 text-accent-light border-accent/30'
                                        : 'bg-bg-hover text-text-muted border-border'
                                        }`}>
                                        {u.role === 'admin' ? t('layout.admin') : t('layout.user')}
                                    </span>
                                    {!u.is_active && (
                                        <span className="text-xs px-2 py-0.5 rounded-full bg-danger/10 text-danger border border-danger/30">{t('users.deactivated')}</span>
                                    )}
                                </div>
                                <p className="text-sm text-text-muted">@{u.username}</p>

                                {/* Zugewiesene Zonen */}
                                {u.role !== 'admin' && (
                                    <div className="mt-2">
                                        <div className="flex items-center gap-2 flex-wrap">
                                            <span className="text-xs text-text-muted">{t('users.zonesLabel')}</span>
                                            {(u.zones || []).length > 0 ? (
                                                (u.zones || []).map(z => (
                                                    <span key={z} className="text-xs px-2 py-0.5 rounded-full bg-purple-500/10 text-purple-400 border border-purple-500/30 flex items-center gap-1">
                                                        <Globe className="w-3 h-3" />
                                                        {z.replace(/\.$/, '')}
                                                    </span>
                                                ))
                                            ) : (
                                                <span className="text-xs text-text-muted italic">{t('settings.noZonesAssigned')}</span>
                                            )}
                                        </div>
                                    </div>
                                )}
                                {u.role === 'admin' && (
                                    <p className="text-xs text-accent-light mt-2 flex items-center gap-1">
                                        <Globe className="w-3 h-3" /> {t('users.accessAllZones')}
                                    </p>
                                )}
                            </div>

                            {/* Actions */}
                            <div className="flex items-center gap-1 shrink-0">
                                {u.role !== 'admin' && (
                                    <button
                                        onClick={() => openZoneEditor(u)}
                                        className="p-2 rounded-lg text-text-muted hover:text-purple-400 hover:bg-purple-500/10 transition-colors"
                                        title={t('users.assignZones')}
                                    >
                                        <Globe className="w-4 h-4" />
                                    </button>
                                )}
                                <button
                                    onClick={() => toggleRole(u)}
                                    className="p-2 rounded-lg text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors"
                                    title={u.role === 'admin' ? t('users.demoteToUser') : t('users.promoteToAdmin')}
                                >
                                    <Shield className="w-4 h-4" />
                                </button>
                                <button
                                    onClick={() => { setEditPasswordUser(u); setNewPassword(''); setError(''); setSuccess(''); }}
                                    className="p-2 rounded-lg text-text-muted hover:text-warning hover:bg-warning/10 transition-colors"
                                    title={t('users.changePassword')}
                                >
                                    <Key className="w-4 h-4" />
                                </button>
                                <button
                                    onClick={() => handleDelete(u.id, u.username)}
                                    className="p-2 rounded-lg text-text-muted hover:text-danger hover:bg-danger/10 transition-colors"
                                    title={t('users.delete')}
                                >
                                    <Trash2 className="w-4 h-4" />
                                </button>
                            </div>
                        </div>
                    </div>
                ))}
            </div>

            {/* ===== Create User Modal ===== */}
            {showCreate && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setShowCreate(false)}>
                    <div className="glass-card p-6 w-full max-w-md" onClick={e => e.stopPropagation()}>
                        <h2 className="text-lg font-bold text-text-primary mb-4">{t('users.createUser')}</h2>
                        <form onSubmit={handleCreate} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('users.username')}</label>
                                <input type="text" value={form.username} onChange={e => setForm({ ...form, username: e.target.value })}
                                    placeholder="benutzername" className="w-full px-3 py-2 text-sm" required minLength={3} />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('users.displayName')}</label>
                                <input type="text" value={form.display_name} onChange={e => setForm({ ...form, display_name: e.target.value })}
                                    placeholder="Max Mustermann" className="w-full px-3 py-2 text-sm" />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('users.password')}</label>
                                <input type="password" value={form.password} onChange={e => setForm({ ...form, password: e.target.value })}
                                    placeholder="••••••" className="w-full px-3 py-2 text-sm" required minLength={4} />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.role')}</label>
                                <select value={form.role} onChange={e => setForm({ ...form, role: e.target.value })} className="w-full px-3 py-2 text-sm">
                                    <option value="user">{t('layout.user')}</option>
                                    <option value="admin">{t('users.administrator')}</option>
                                </select>
                            </div>
                            <div className="flex justify-end gap-3 pt-2">
                                <button type="button" onClick={() => setShowCreate(false)} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary">{t('common.cancel')}</button>
                                <button type="submit" disabled={saving} className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                                    {saving && <Loader2 className="w-4 h-4 animate-spin" />} {t('settings.create')}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* ===== Zone Assignment Modal ===== */}
            {editZonesUser && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setEditZonesUser(null)}>
                    <div className="glass-card p-6 w-full max-w-lg max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
                        <div className="flex items-center justify-between mb-4">
                            <div>
                                <h2 className="text-lg font-bold text-text-primary">{t('users.assignZones')}</h2>
                                <p className="text-sm text-text-muted">{t('users.forUser')} <span className="text-text-primary font-medium">{editZonesUser.display_name || editZonesUser.username}</span></p>
                            </div>
                            <button onClick={() => setEditZonesUser(null)} className="p-1 rounded-lg hover:bg-bg-hover text-text-muted">
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        <p className="text-xs text-text-muted mb-3">
                            {t('users.zonesSelectHint')}
                        </p>

                        <div className="space-y-2">
                            {allZones.length === 0 ? (
                                <p className="text-sm text-text-muted text-center py-4">{t('users.noZonesAvailable')}</p>
                            ) : (
                                allZones.map(z => {
                                    const active = selectedZones.includes(z.name)
                                    return (
                                        <button
                                            key={z.name}
                                            onClick={() => toggleZone(z.name)}
                                            className={`w-full flex items-center gap-3 p-3 rounded-lg border transition-all text-left ${active
                                                ? 'bg-accent/10 border-accent/40 text-text-primary'
                                                : 'bg-bg-primary border-border text-text-secondary hover:border-border hover:bg-bg-hover'
                                                }`}
                                        >
                                            <div className={`w-5 h-5 rounded shrink-0 flex items-center justify-center border ${active ? 'bg-accent border-accent' : 'border-border'
                                                }`}>
                                                {active && <Check className="w-3 h-3 text-white" />}
                                            </div>
                                            <Globe className={`w-4 h-4 shrink-0 ${active ? 'text-accent-light' : 'text-text-muted'}`} />
                                            <span className="font-mono text-sm">{z.name.replace(/\.$/, '')}</span>
                                            <span className="text-xs text-text-muted ml-auto">({z.server})</span>
                                        </button>
                                    )
                                })
                            )}
                        </div>

                        <div className="flex justify-between items-center mt-4 pt-4 border-t border-border">
                            <p className="text-xs text-text-muted">{t('users.zonesSelected', { count: selectedZones.length })}</p>
                            <div className="flex gap-3">
                                <button onClick={() => setEditZonesUser(null)} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary">{t('common.cancel')}</button>
                                <button
                                    onClick={saveZones}
                                    disabled={saving}
                                    className="px-4 py-2 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2"
                                >
                                    {saving && <Loader2 className="w-4 h-4 animate-spin" />} {t('common.save')}
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {/* ===== Change Password Modal ===== */}
            {editPasswordUser && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={() => setEditPasswordUser(null)}>
                    <div className="glass-card p-6 w-full max-w-md" onClick={e => e.stopPropagation()}>
                        <h2 className="text-lg font-bold text-text-primary mb-4">{t('users.changePassword')}</h2>
                        <p className="text-sm text-text-muted mb-4">{t('users.newPasswordFor', { name: editPasswordUser.display_name || editPasswordUser.username })}</p>

                        <form onSubmit={handleSetPassword} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.newPassword')}</label>
                                <input
                                    type="password"
                                    value={newPassword}
                                    onChange={e => setNewPassword(e.target.value)}
                                    placeholder="••••••"
                                    className="w-full px-3 py-2 text-sm"
                                    required
                                    minLength={4}
                                />
                            </div>
                            <div className="flex justify-end gap-3 pt-4 mt-4 border-t border-border">
                                <button type="button" onClick={() => setEditPasswordUser(null)} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary">{t('common.cancel')}</button>
                                <button type="submit" disabled={saving || !newPassword} className="px-4 py-2 bg-gradient-to-r from-warning/80 to-orange-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
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
