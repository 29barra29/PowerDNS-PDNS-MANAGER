import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
    AlertTriangle, Check, Globe, Key, Loader2, Plus, Power, PowerOff, Shield, Trash2, User, X,
} from 'lucide-react'
import api from '../api'
import ModalErrorBanner from '../components/ModalErrorBanner'
import UserBadges from '../components/users/UserBadges'
import UserSecurityModal from '../components/userSecurity/UserSecurityModal'
import { createUserPayload, orphanZones } from '../components/userSecurity/userSecurityModel'

// Benutzerverwaltung (Admin). F3 §2.3/§2.5/§6.5: Rollenwechsel mit Rueckfrage, Aktivieren/Deaktivieren,
// Dialog "Passwort & Sicherheit", E-Mail und erzwungener Passwortwechsel beim Anlegen; eigenes Konto gesperrt.
// F8-I02 (f152): verwaiste Zonenrechte im Zonen-Editor sichtbar und abwaehlbar; F8-C05: Modal-Fehler im Modal.
const EMPTY_FORM = { username: '', password: '', display_name: '', email: '', role: 'user', must_change_password: true }

export default function UsersPage() {
    const { t } = useTranslation()
    const me = api.getUser()
    const [users, setUsers] = useState([])
    const [allZones, setAllZones] = useState([])
    const [zonesLoadErrors, setZonesLoadErrors] = useState([])
    const [resetMailAvailable, setResetMailAvailable] = useState(false)
    const [loading, setLoading] = useState(true)
    const [showCreate, setShowCreate] = useState(false)
    const [editZonesUser, setEditZonesUser] = useState(null)
    const [securityUser, setSecurityUser] = useState(null)
    const [selectedZones, setSelectedZones] = useState([])
    /** Pro Zonenname: 'manage' | 'read' */
    const [zonePerm, setZonePerm] = useState({})
    const [form, setForm] = useState(EMPTY_FORM)
    const [error, setError] = useState('')
    const [success, setSuccess] = useState('')
    const [saving, setSaving] = useState(false)
    const [busyUserId, setBusyUserId] = useState(null)
    // Eigene Modal-Fehler-States, damit man die Ursache nicht hinter dem Overlay verliert (F8-C05)
    const [createError, setCreateError] = useState('')
    const [zonesError, setZonesError] = useState('')

    // Erfolgsmeldung nach 4 Sekunden ausblenden, damit sie die Sicht nicht versperrt
    useEffect(() => {
        if (!success) return
        const timer = setTimeout(() => setSuccess(''), 4000)
        return () => clearTimeout(timer)
    }, [success])

    // Laedt Benutzer, Mail-Flag und alle Zonen (fehlgeschlagene Server merken, F8-I02); setzt keinen Zustand.
    const fetchData = useCallback(async () => {
        const [userData, serverData] = await Promise.all([
            api.listUsers(),
            api.getServers(),
        ])
        const zones = []
        const failed = []
        for (const s of (serverData.servers || [])) {
            if (!s.is_reachable) {
                failed.push(s.name)
                continue
            }
            try {
                const zData = await api.listZones(s.name)
                for (const z of (zData.zones || [])) {
                    if (!zones.find((existing) => existing.name === z.name)) {
                        zones.push({ name: z.name, server: s.name })
                    }
                }
            } catch {
                failed.push(s.name)
            }
        }
        return { userData, zones, failed }
    }, [])

    const applyData = useCallback(({ userData, zones, failed }) => {
        setUsers(userData.users || [])
        setResetMailAvailable(!!userData.password_reset_mail_available)
        setAllZones(zones)
        setZonesLoadErrors(failed)
    }, [])

    const loadData = useCallback(() => fetchData()
        .then(applyData)
        .catch((err) => setError(err.message))
        .finally(() => setLoading(false)), [fetchData, applyData])

    useEffect(() => {
        let alive = true
        fetchData()
            .then((data) => { if (alive) applyData(data) })
            .catch((err) => { if (alive) setError(err.message) })
            .finally(() => { if (alive) setLoading(false) })
        return () => { alive = false }
    }, [fetchData, applyData])

    const displayName = (u) => u.display_name || u.username
    const isSelf = (u) => !!me && u.id === me.id

    async function handleCreate(e) {
        e.preventDefault()
        setSaving(true)
        setCreateError('')
        try {
            const payload = createUserPayload(form)
            await api.createUser(payload)
            setShowCreate(false)
            setForm(EMPTY_FORM)
            setSuccess(t('users.userCreated', { name: payload.username }))
            loadData()
        } catch (err) {
            setCreateError(err.message)
        } finally {
            setSaving(false)
        }
    }

    async function handleDelete(id, name) {
        if (!confirm(t('users.deleteConfirm', { name }))) return
        try {
            await api.deleteUser(id)
            setSuccess(t('users.userDeleted', { name }))
            loadData()
        } catch (err) {
            setError(err.message)
        }
    }

    async function toggleRole(user) {
        if (isSelf(user) || busyUserId) return
        const newRole = user.role === 'admin' ? 'user' : 'admin'
        const roleLabel = t(newRole === 'admin' ? 'users.administrator' : 'layout.user')
        if (!confirm(t('users.roleChangeConfirm', { name: displayName(user), role: roleLabel }))) return
        setBusyUserId(user.id)
        try {
            await api.updateUser(user.id, { role: newRole })
            await loadData()
        } catch (err) {
            setError(err.message)
        } finally {
            setBusyUserId(null)
        }
    }

    async function toggleActive(user) {
        if (isSelf(user) || busyUserId) return
        const activate = !user.is_active
        if (!activate && !confirm(t('users.deactivateConfirm', { name: displayName(user) }))) return
        setBusyUserId(user.id)
        try {
            await api.updateUser(user.id, { is_active: activate })
            setSuccess(t(activate ? 'users.userActivated' : 'users.userDeactivated', { name: displayName(user) }))
            await loadData()
        } catch (err) {
            setError(err.message)
        } finally {
            setBusyUserId(null)
        }
    }

    function openCreateModal() {
        setForm(EMPTY_FORM)
        setCreateError('')
        setShowCreate(true)
    }

    function closeCreateModal() {
        setShowCreate(false)
        setCreateError('')
    }

    function openZoneEditor(user) {
        setEditZonesUser(user)
        setSelectedZones([...(user.zones || [])])
        const zp = user.zone_permissions || {}
        const next = {}
        for (const z of user.zones || []) {
            next[z] = zp[z] === 'read' ? 'read' : 'manage'
        }
        setZonePerm(next)
        setZonesError('')
    }

    function closeZoneEditor() {
        setEditZonesUser(null)
        setZonesError('')
    }

    function toggleZone(zoneName) {
        setSelectedZones((prev) => {
            if (prev.includes(zoneName)) {
                setZonePerm((p) => {
                    const n = { ...p }
                    delete n[zoneName]
                    return n
                })
                return prev.filter((z) => z !== zoneName)
            }
            setZonePerm((p) => ({ ...p, [zoneName]: p[zoneName] || 'manage' }))
            return [...prev, zoneName]
        })
    }

    function setZonePermission(zoneName, perm) {
        setZonePerm((p) => ({ ...p, [zoneName]: perm === 'read' ? 'read' : 'manage' }))
    }

    async function saveZones() {
        if (!editZonesUser) return
        setSaving(true)
        setZonesError('')
        try {
            const zperm = {}
            for (const z of selectedZones) {
                zperm[z] = zonePerm[z] === 'read' ? 'read' : 'manage'
            }
            await api.updateUserZones(editZonesUser.id, { zones: selectedZones, zone_permissions: zperm })
            const name = editZonesUser.username
            setEditZonesUser(null)
            setSuccess(t('users.zonesAssigned', { name }))
            loadData()
        } catch (err) {
            setZonesError(err.message)
        } finally {
            setSaving(false)
        }
    }

    const handleSecurityChanged = useCallback((message) => {
        if (message) setSuccess(message)
        loadData()
    }, [loadData])
    const closeSecurity = useCallback(() => setSecurityUser(null), [])

    if (loading) return <div className="flex items-center justify-center h-64"><Loader2 className="w-8 h-8 text-accent animate-spin" /></div>

    // Zonen-Editor: verwaiste Zuordnungen (Zone geloescht oder Server offline) zuerst (F8-I02). Grundlage sind die
    // gespeicherten Zonen des Nutzers, damit eine abgewaehlte Zeile stehen bleibt und wieder angehakt werden kann.
    const orphans = editZonesUser ? orphanZones(editZonesUser.zones || [], allZones) : []
    const zoneRows = [
        ...orphans.map((name) => ({ name, server: null, orphan: true })),
        ...allZones.map((z) => ({ ...z, orphan: false })),
    ]

    return (
        <div className="space-y-6">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <h1 className="text-2xl font-bold text-text-primary">{t('users.title')}</h1>
                    <p className="text-text-muted text-sm mt-1">{t('users.usersCount', { count: users.length })}</p>
                </div>
                <button
                    onClick={openCreateModal}
                    className="self-start sm:self-auto flex items-center gap-2 px-4 py-2.5 bg-gradient-to-r from-accent to-purple-600 hover:from-accent-hover hover:to-purple-700 text-white rounded-lg font-medium text-sm transition-all"
                >
                    <Plus className="w-4 h-4" /> {t('users.newUser')}
                </button>
            </div>

            {error && (
                <div className="p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger text-sm flex justify-between items-center gap-3">
                    <span className="break-words">{error}</span>
                    <button onClick={() => setError('')} className="text-xs hover:underline" aria-label={t('common.close')}>×</button>
                </div>
            )}
            {success && (
                <div className="p-4 rounded-xl bg-success/10 border border-success/30 text-success text-sm flex justify-between items-center gap-3">
                    <span className="break-words">{success}</span>
                    <button onClick={() => setSuccess('')} className="text-xs hover:underline" aria-label={t('common.close')}>×</button>
                </div>
            )}

            <div className="grid gap-4">
                {users.map((u) => {
                    const self = isSelf(u)
                    const rowBusy = busyUserId === u.id
                    return (
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
                                        <h3 className="font-semibold text-text-primary">{displayName(u)}</h3>
                                        <span className={`text-xs px-2 py-0.5 rounded-full border ${u.role === 'admin'
                                            ? 'bg-accent/10 text-accent-light border-accent/30'
                                            : 'bg-bg-hover text-text-muted border-border'
                                            }`}>
                                            {u.role === 'admin' ? t('layout.admin') : t('layout.user')}
                                        </span>
                                        {!u.is_active && (
                                            <span className="text-xs px-2 py-0.5 rounded-full bg-danger/10 text-danger border border-danger/30">{t('users.deactivated')}</span>
                                        )}
                                        <UserBadges user={u} context={{ meId: me?.id ?? null }} />
                                    </div>
                                    <p className="text-sm text-text-muted break-all">@{u.username}{u.email ? ` · ${u.email}` : ''}</p>

                                    {/* Zugewiesene Zonen */}
                                    {u.role !== 'admin' && (
                                        <div className="mt-2">
                                            <div className="flex items-center gap-2 flex-wrap">
                                                <span className="text-xs text-text-muted">{t('users.zonesLabel')}</span>
                                                {(u.zones || []).length > 0 ? (
                                                    (u.zones || []).map((z) => (
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
                                <div className="flex items-center gap-1 shrink-0 flex-wrap justify-end">
                                    {rowBusy && <Loader2 className="w-4 h-4 text-text-muted animate-spin" aria-hidden="true" />}
                                    {u.role !== 'admin' && (
                                        <button
                                            onClick={() => openZoneEditor(u)}
                                            className="p-2 rounded-lg text-text-muted hover:text-purple-400 hover:bg-purple-500/10 transition-colors"
                                            title={t('users.assignZones')}
                                            aria-label={t('users.assignZones')}
                                        >
                                            <Globe className="w-4 h-4" />
                                        </button>
                                    )}
                                    <span title={self ? t('users.ownAccountRoleLocked') : (u.role === 'admin' ? t('users.demoteToUser') : t('users.promoteToAdmin'))}>
                                        <button
                                            onClick={() => toggleRole(u)}
                                            disabled={self || !!busyUserId}
                                            className="p-2 rounded-lg text-text-muted hover:text-accent-light hover:bg-accent/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                                            aria-label={u.role === 'admin' ? t('users.demoteToUser') : t('users.promoteToAdmin')}
                                        >
                                            <Shield className="w-4 h-4" />
                                        </button>
                                    </span>
                                    {!self && (
                                        <button
                                            onClick={() => toggleActive(u)}
                                            disabled={!!busyUserId}
                                            className={`p-2 rounded-lg text-text-muted transition-colors disabled:opacity-40 ${u.is_active ? 'hover:text-danger hover:bg-danger/10' : 'hover:text-success hover:bg-success/10'}`}
                                            title={u.is_active ? t('users.deactivate') : t('users.activate')}
                                            aria-label={u.is_active ? t('users.deactivate') : t('users.activate')}
                                        >
                                            {u.is_active ? <PowerOff className="w-4 h-4" /> : <Power className="w-4 h-4" />}
                                        </button>
                                    )}
                                    <span title={self ? t('users.ownAccountHint') : t('users.securityTitle')}>
                                        <button
                                            onClick={() => setSecurityUser(u)}
                                            disabled={self}
                                            className="p-2 rounded-lg text-text-muted hover:text-warning hover:bg-warning/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                                            aria-label={t('users.securityTitle')}
                                        >
                                            <Key className="w-4 h-4" />
                                        </button>
                                    </span>
                                    <button
                                        onClick={() => handleDelete(u.id, u.username)}
                                        disabled={self}
                                        className="p-2 rounded-lg text-text-muted hover:text-danger hover:bg-danger/10 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
                                        title={t('users.delete')}
                                        aria-label={t('users.delete')}
                                    >
                                        <Trash2 className="w-4 h-4" />
                                    </button>
                                </div>
                            </div>
                        </div>
                    )
                })}
            </div>

            {/* ===== Create User Modal ===== */}
            {showCreate && (
                <div
                    className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
                    onClick={() => { if (!saving) closeCreateModal() }}
                >
                    <div className="glass-card p-6 w-full max-w-md max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
                        <h2 className="text-lg font-bold text-text-primary mb-4">{t('users.createUser')}</h2>

                        <ModalErrorBanner message={createError} onClose={() => setCreateError('')} />

                        <form onSubmit={handleCreate} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('users.username')}</label>
                                <input type="text" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })}
                                    placeholder={t('users.usernamePlaceholder')} className="w-full px-3 py-2 text-sm" required minLength={3} pattern="[A-Za-z0-9._\-]+" title={t('users.usernamePattern')} />
                                <p className="text-xs text-text-muted mt-1">{t('users.usernameHint')}</p>
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('users.displayName')}</label>
                                <input type="text" value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                                    placeholder={t('users.displayNamePlaceholder')} className="w-full px-3 py-2 text-sm" />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('users.email')}</label>
                                <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })}
                                    className="w-full px-3 py-2 text-sm" autoComplete="off" maxLength={255} />
                                <p className="text-xs text-text-muted mt-1">{t('users.emailHint')}</p>
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('users.password')}</label>
                                <input type="password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })}
                                    placeholder="••••••••" className="w-full px-3 py-2 text-sm" required minLength={8} maxLength={128} autoComplete="new-password" />
                                <p className="text-xs text-text-muted mt-1">{t('users.passwordPolicy')}</p>
                            </div>
                            <label className="flex items-center gap-2 text-sm text-text-secondary">
                                <input type="checkbox" checked={form.must_change_password}
                                    onChange={(e) => setForm({ ...form, must_change_password: e.target.checked })} />
                                {t('users.mustChangeOnFirstLogin')}
                            </label>
                            <div>
                                <label className="block text-sm font-medium text-text-secondary mb-1">{t('settings.role')}</label>
                                <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })} className="w-full px-3 py-2 text-sm">
                                    <option value="user">{t('layout.user')}</option>
                                    <option value="admin">{t('users.administrator')}</option>
                                </select>
                            </div>
                            <div className="flex justify-end gap-3 pt-2">
                                <button type="button" onClick={closeCreateModal} disabled={saving} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-50">{t('common.cancel')}</button>
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
                <div
                    className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
                    onClick={() => { if (!saving) closeZoneEditor() }}
                >
                    <div className="glass-card p-6 w-full max-w-lg max-h-[80vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-between mb-4">
                            <div>
                                <h2 className="text-lg font-bold text-text-primary">{t('users.assignZones')}</h2>
                                <p className="text-sm text-text-muted">{t('users.forUser')} <span className="text-text-primary font-medium">{displayName(editZonesUser)}</span></p>
                            </div>
                            <button onClick={closeZoneEditor} className="p-1 rounded-lg hover:bg-bg-hover text-text-muted" aria-label={t('common.close')}>
                                <X className="w-5 h-5" />
                            </button>
                        </div>

                        <ModalErrorBanner message={zonesError} onClose={() => setZonesError('')} />

                        {zonesLoadErrors.length > 0 && (
                            <div className="mb-3 p-3 rounded-lg bg-warning/10 border border-warning/30 text-warning text-xs flex items-start gap-2">
                                <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden="true" />
                                <span>{t('users.zonesLoadPartial', { servers: zonesLoadErrors.join(', ') })}</span>
                            </div>
                        )}

                        <p className="text-xs text-text-muted mb-3">
                            {t('users.zonesSelectHint')}
                        </p>

                        {orphans.length > 0 && (
                            <p className="text-xs text-warning mb-2">{t('users.zonesHiddenHint', { count: orphans.length })}</p>
                        )}

                        <div className="space-y-2">
                            {zoneRows.length === 0 ? (
                                <p className="text-sm text-text-muted text-center py-4">{t('users.noZonesAvailable')}</p>
                            ) : (
                                zoneRows.map((z) => {
                                    const active = selectedZones.includes(z.name)
                                    return (
                                        <div
                                            key={`${z.orphan ? 'o' : 'z'}:${z.name}`}
                                            className={`flex items-stretch gap-2 rounded-lg border transition-all ${active
                                                ? (z.orphan ? 'bg-warning/10 border-warning/40' : 'bg-accent/10 border-accent/40')
                                                : 'bg-bg-primary border-border text-text-secondary hover:border-border hover:bg-bg-hover'
                                                }`}
                                        >
                                            <button
                                                type="button"
                                                onClick={() => toggleZone(z.name)}
                                                className="flex-1 min-w-0 flex items-center gap-3 p-3 text-left"
                                            >
                                                <div className={`w-5 h-5 rounded shrink-0 flex items-center justify-center border ${active ? 'bg-accent border-accent' : 'border-border'}`}>
                                                    {active && <Check className="w-3 h-3 text-white" />}
                                                </div>
                                                <Globe className={`w-4 h-4 shrink-0 ${active ? 'text-accent-light' : 'text-text-muted'}`} />
                                                <span className="font-mono text-sm text-text-primary break-all">{z.name.replace(/\.$/, '')}</span>
                                                {z.orphan ? (
                                                    <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-warning/10 text-warning border border-warning/30 ml-auto shrink-0">{t('users.zoneUnavailable')}</span>
                                                ) : (
                                                    <span className="text-xs text-text-muted ml-auto shrink-0">({z.server})</span>
                                                )}
                                            </button>
                                            {active && (
                                                <label className="flex items-center gap-1.5 pr-2 shrink-0 self-center">
                                                    <span className="text-[10px] text-text-muted sr-only sm:not-sr-only sm:inline">{t('users.zonePermColumn')}</span>
                                                    <select
                                                        value={zonePerm[z.name] || 'manage'}
                                                        onClick={(e) => e.stopPropagation()}
                                                        onChange={(e) => setZonePermission(z.name, e.target.value)}
                                                        className="text-xs bg-bg-primary border border-border rounded-md px-2 py-1.5 max-w-[7.5rem] text-text-primary"
                                                    >
                                                        <option value="manage">{t('users.zonePermManage')}</option>
                                                        <option value="read">{t('users.zonePermRead')}</option>
                                                    </select>
                                                </label>
                                            )}
                                        </div>
                                    )
                                })
                            )}
                        </div>

                        <div className="flex justify-between items-center mt-4 pt-4 border-t border-border">
                            <p className="text-xs text-text-muted">{t('users.zonesSelected', { count: selectedZones.length })}</p>
                            <div className="flex gap-3">
                                <button onClick={closeZoneEditor} disabled={saving} className="px-4 py-2 text-sm text-text-secondary hover:text-text-primary disabled:opacity-50">{t('common.cancel')}</button>
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

            {/* ===== Passwort & Sicherheit ===== */}
            {securityUser && (
                <UserSecurityModal
                    key={securityUser.id}
                    user={securityUser}
                    resetMailAvailable={resetMailAvailable}
                    onClose={closeSecurity}
                    onChanged={handleSecurityChanged}
                />
            )}
        </div>
    )
}
