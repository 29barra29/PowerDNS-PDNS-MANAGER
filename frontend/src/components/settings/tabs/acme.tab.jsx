import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Plus, Trash2, Loader2, AlertCircle, CheckCircle2, X, Copy, Check, Key } from 'lucide-react'
import api from '../../../api'
import { useSettings } from '../settingsContext'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'acme', order: 80, labelKey: 'settings.acme.tab', icon: Key, adminOnly: true }

// Tab "ACME / Auto-TLS" – mechanisch aus SettingsPage.jsx 2.4.1 übernommen.
export default function AcmeTab({ active }) {
    const { t } = useTranslation()
    const { adminInfo, notify } = useSettings()
    const setError = notify.error
    const setSuccess = notify.success
    const [acmeTokens, setAcmeTokens] = useState([])
    const [acmeAvailableZones, setAcmeAvailableZones] = useState([])  // [{name, server}]
    const [loadingAcme, setLoadingAcme] = useState(false)
    const [acmeForm, setAcmeForm] = useState({ name: '', allowed_zones: [] })
    const [savingAcmeToken, setSavingAcmeToken] = useState(false)
    // Wenn !== null -> Modal mit dem frischen Plaintext-Token einblenden.
    // Plaintext sehen wir nur EINMAL nach create - danach gibt es ihn nicht mehr.
    const [acmeNewToken, setAcmeNewToken] = useState(null)
    const [acmeCopied, setAcmeCopied] = useState(false)
    const [acmeShowForm, setAcmeShowForm] = useState(false)


    async function loadAcmeTokens() {
        setLoadingAcme(true)
        try {
            const data = await api.getAcmeTokens()
            setAcmeTokens(data.tokens || [])
        } catch (err) {
            setError(err.message)
        } finally {
            setLoadingAcme(false)
        }
    }

    async function loadAcmeZones() {
        // Selbe Logik wie UsersPage: alle Zonen aller aktiven Server holen, deduplicaten.
        try {
            const sData = await api.getServers()
            const zones = []
            for (const s of (sData.servers || [])) {
                try {
                    const zData = await api.listZones(s.name)
                    for (const z of (zData.zones || [])) {
                        if (!zones.find(existing => existing.name === z.name)) {
                            zones.push({ name: z.name, server: s.name })
                        }
                    }
                } catch { /* server offline -> skip */ }
            }
            zones.sort((a, b) => a.name.localeCompare(b.name))
            setAcmeAvailableZones(zones)
        } catch { /* ignore - keine Server konfiguriert */ }
    }

    function toggleAcmeZone(zoneName) {
        setAcmeForm((f) => {
            const has = f.allowed_zones.includes(zoneName)
            return {
                ...f,
                allowed_zones: has ? f.allowed_zones.filter(z => z !== zoneName) : [...f.allowed_zones, zoneName],
            }
        })
    }

    async function handleCreateAcmeToken(e) {
        e.preventDefault()
        if (!acmeForm.name.trim() || acmeForm.allowed_zones.length === 0) return
        setSavingAcmeToken(true)
        setError('')
        try {
            const res = await api.createAcmeToken({
                name: acmeForm.name.trim(),
                allowed_zones: acmeForm.allowed_zones,
            })
            // plaintext_token wird HIER aus dem Response in den State uebernommen
            // und sofort im Modal angezeigt. Im naechsten loadAcmeTokens() ist er weg.
            setAcmeNewToken({
                plaintext: res.plaintext_token,
                name: res.token?.name,
                allowed_zones: res.token?.allowed_zones || [],
            })
            setAcmeForm({ name: '', allowed_zones: [] })
            setAcmeShowForm(false)
            await loadAcmeTokens()
        } catch (err) {
            setError(err.message)
        } finally {
            setSavingAcmeToken(false)
        }
    }

    async function handleDeleteAcmeToken(id, name) {
        if (!window.confirm(t('settings.acme.confirmDelete', { name }))) return
        try {
            await api.deleteAcmeToken(id)
            setSuccess(t('settings.acme.deleted'))
            await loadAcmeTokens()
        } catch (err) {
            setError(err.message)
        }
    }

    async function handleCopyAcmeToken() {
        if (!acmeNewToken?.plaintext) return
        try {
            await navigator.clipboard.writeText(acmeNewToken.plaintext)
            setAcmeCopied(true)
            setTimeout(() => setAcmeCopied(false), 2000)
        } catch { /* clipboard kann blockiert sein - kein Drama */ }
    }

    // Wie 2.4.1: bei jedem Öffnen des Tabs neu laden
    useEffect(() => {
        if (!active) return
        // eslint-disable-next-line react-hooks/set-state-in-effect -- Laden beim Aktivieren wie 2.4.1
        loadAcmeTokens()
        loadAcmeZones()
    }, [active]) // eslint-disable-line react-hooks/exhaustive-deps -- nur beim Aktivieren laden (wie 2.4.1)

    return (
        <div className="space-y-6">
            {/* Header / Hinweis */}
            <div className="glass-card p-6">
                <div className="flex items-center gap-3 mb-4">
                    <div className="w-10 h-10 rounded-xl bg-accent/20 flex items-center justify-center">
                        <Key className="w-5 h-5 text-accent" />
                    </div>
                    <div>
                        <h2 className="text-lg font-semibold text-text-primary">{t('settings.acme.title')}</h2>
                        <p className="text-sm text-text-muted">{t('settings.acme.subtitle')}</p>
                    </div>
                </div>
                <p className="text-sm text-text-muted leading-relaxed">{t('settings.acme.intro')}</p>
            </div>

            {/* Token-Liste + "Neuen Token erstellen" */}
            <div className="glass-card p-6">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between mb-4">
                    <div>
                        <h3 className="text-base font-semibold text-text-primary">{t('settings.acme.tokensTitle')}</h3>
                        <p className="text-sm text-text-muted">{t('settings.acme.tokensSubtitle')}</p>
                    </div>
                    <button
                        type="button"
                        onClick={() => { setAcmeShowForm(true); setAcmeForm({ name: '', allowed_zones: [] }) }}
                        className="self-start sm:self-auto px-4 py-2 bg-gradient-to-r from-accent to-accent-light hover:from-accent-light hover:to-accent text-white rounded-lg text-sm font-medium flex items-center gap-2"
                    >
                        <Plus className="w-4 h-4" />
                        {t('settings.acme.newToken')}
                    </button>
                </div>

                {loadingAcme ? (
                    <div className="flex items-center justify-center py-8">
                        <Loader2 className="w-6 h-6 text-accent animate-spin" />
                    </div>
                ) : acmeTokens.length === 0 ? (
                    <div className="text-center py-8 text-sm text-text-muted">
                        {t('settings.acme.empty')}
                    </div>
                ) : (
                    <div className="space-y-3">
                        {acmeTokens.map((tok) => (
                            <div key={tok.id} className="p-4 rounded-lg border border-border bg-bg-secondary/40 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                                <div className="min-w-0 flex-1">
                                    <div className="flex items-center gap-2 flex-wrap">
                                        <span className="font-medium text-text-primary">{tok.name}</span>
                                        <code className="text-xs text-text-muted bg-bg-secondary px-2 py-0.5 rounded">{tok.token_prefix}…</code>
                                    </div>
                                    <div className="text-xs text-text-muted mt-1 break-all">
                                        {t('settings.acme.allowedZones')}: {(tok.allowed_zones || []).join(', ') || '-'}
                                    </div>
                                    <div className="text-xs text-text-muted mt-0.5">
                                        {tok.last_used_at
                                            ? t('settings.acme.lastUsedAt', { date: new Date(tok.last_used_at).toLocaleString(), ip: tok.last_used_ip || '-' })
                                            : t('settings.acme.neverUsed')}
                                    </div>
                                </div>
                                <button
                                    type="button"
                                    onClick={() => handleDeleteAcmeToken(tok.id, tok.name)}
                                    className="self-start sm:self-auto px-3 py-1.5 bg-danger/20 hover:bg-danger/30 text-danger rounded-lg text-sm font-medium flex items-center gap-1.5"
                                >
                                    <Trash2 className="w-3.5 h-3.5" />
                                    {t('settings.acme.revoke')}
                                </button>
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Anleitung / certbot-Hook */}
            <div className="glass-card p-6">
                <h3 className="text-base font-semibold text-text-primary mb-3">{t('settings.acme.howtoTitle')}</h3>
                <p className="text-sm text-text-muted mb-4">{t('settings.acme.howtoIntro')}</p>
                <ol className="text-sm text-text-muted space-y-2 list-decimal list-inside">
                    <li>{t('settings.acme.howtoStep1')}</li>
                    <li>{t('settings.acme.howtoStep2')}</li>
                    <li>{t('settings.acme.howtoStep3')}</li>
                </ol>
                {(() => {
                    // Trailing-Slash am Ende der App-Base-URL fuer das Snippet abschneiden,
                    // damit ".../api/v1/..." spaeter sauber zusammengeklebt werden kann.
                    const rawUrl = adminInfo?.app_base_url || window.location.origin
                    const baseUrl = rawUrl.endsWith('/') ? rawUrl.slice(0, -1) : rawUrl
                    const snippet = `DNSMGR_URL="${baseUrl}"
DNSMGR_TOKEN="dnsmgr_acme_…"

certbot certonly \\
  --manual --preferred-challenges=dns \\
  --manual-auth-hook    /usr/local/bin/certbot-dns-dnsmanager.sh \\
  --manual-cleanup-hook /usr/local/bin/certbot-dns-dnsmanager.sh \\
  -d smtp.example.com`
                    return (
                        <div className="mt-4 p-3 rounded-lg bg-bg-secondary border border-border overflow-x-auto">
                            <code className="text-xs text-text-primary whitespace-pre">{snippet}</code>
                        </div>
                    )
                })()}
                <p className="text-xs text-text-muted mt-3">
                    {t('settings.acme.scriptHint')} <code className="bg-bg-secondary px-1.5 py-0.5 rounded">scripts/certbot-dns-dnsmanager.sh</code>
                </p>
            </div>

            {/* Create-Modal */}
            {acmeShowForm && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={(e) => { if (e.target === e.currentTarget) setAcmeShowForm(false) }}>
                    <div className="glass-card p-6 w-full max-w-lg max-h-[90vh] overflow-y-auto">
                        <div className="flex items-center justify-between mb-4">
                            <h3 className="text-lg font-semibold text-text-primary">{t('settings.acme.newToken')}</h3>
                            <button type="button" onClick={() => setAcmeShowForm(false)} className="p-1 text-text-muted hover:text-text-primary">
                                <X className="w-5 h-5" />
                            </button>
                        </div>
                        <form onSubmit={handleCreateAcmeToken} className="space-y-4">
                            <div>
                                <label className="block text-sm font-medium text-text-primary mb-1">{t('settings.acme.formName')}</label>
                                <input
                                    type="text"
                                    value={acmeForm.name}
                                    onChange={(e) => setAcmeForm(f => ({ ...f, name: e.target.value }))}
                                    placeholder={t('settings.acme.formNamePlaceholder')}
                                    required
                                    maxLength={100}
                                    className="w-full px-3 py-2 bg-bg-secondary border border-border rounded-lg text-sm text-text-primary"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium text-text-primary mb-1">{t('settings.acme.formZones')}</label>
                                <p className="text-xs text-text-muted mb-2">{t('settings.acme.formZonesHint')}</p>
                                {acmeAvailableZones.length === 0 ? (
                                    <div className="text-sm text-text-muted italic p-3 border border-border rounded-lg bg-bg-secondary/40">
                                        {t('settings.acme.noZones')}
                                    </div>
                                ) : (
                                    <div className="max-h-48 overflow-y-auto border border-border rounded-lg bg-bg-secondary/40 divide-y divide-border">
                                        {acmeAvailableZones.map((z) => {
                                            const checked = acmeForm.allowed_zones.includes(z.name)
                                            return (
                                                <label key={`${z.server}:${z.name}`} className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-bg-hover">
                                                    <input
                                                        type="checkbox"
                                                        checked={checked}
                                                        onChange={() => toggleAcmeZone(z.name)}
                                                        className="rounded border-border"
                                                    />
                                                    <span className="text-sm text-text-primary break-all">{z.name}</span>
                                                    <span className="ml-auto text-xs text-text-muted shrink-0">{z.server}</span>
                                                </label>
                                            )
                                        })}
                                    </div>
                                )}
                            </div>
                            <div className="flex flex-col sm:flex-row gap-2 sm:justify-end">
                                <button
                                    type="button"
                                    onClick={() => setAcmeShowForm(false)}
                                    className="px-4 py-2 bg-bg-secondary hover:bg-bg-hover text-text-primary rounded-lg text-sm font-medium"
                                >
                                    {t('common.cancel')}
                                </button>
                                <button
                                    type="submit"
                                    disabled={savingAcmeToken || !acmeForm.name.trim() || acmeForm.allowed_zones.length === 0}
                                    className="px-4 py-2 bg-gradient-to-r from-accent to-accent-light hover:from-accent-light hover:to-accent text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center justify-center gap-2"
                                >
                                    {savingAcmeToken ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                                    {t('settings.acme.create')}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}

            {/* Frisch erstellter Token - einmaliger Plaintext-Modal.
                WARNUNG: nicht ueber X / Backdrop schliessbar - nur expliziter Button,
                damit der Admin den Token wirklich kopiert hat. */}
            {acmeNewToken && (
                <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
                    <div className="glass-card p-6 w-full max-w-xl">
                        <div className="flex items-center gap-3 mb-4">
                            <div className="w-10 h-10 rounded-xl bg-success/20 flex items-center justify-center">
                                <CheckCircle2 className="w-5 h-5 text-success" />
                            </div>
                            <div>
                                <h3 className="text-lg font-semibold text-text-primary">{t('settings.acme.createdTitle')}</h3>
                                <p className="text-sm text-text-muted">{acmeNewToken.name}</p>
                            </div>
                        </div>
                        <div className="p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-400 text-sm mb-4 flex items-start gap-2">
                            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
                            <span>{t('settings.acme.createdWarning')}</span>
                        </div>
                        <label className="block text-xs font-medium text-text-muted mb-1">{t('settings.acme.tokenLabel')}</label>
                        <div className="flex items-stretch gap-2 mb-4">
                            <code className="flex-1 px-3 py-2 bg-bg-secondary border border-border rounded-lg text-xs text-text-primary break-all font-mono">
                                {acmeNewToken.plaintext}
                            </code>
                            <button
                                type="button"
                                onClick={handleCopyAcmeToken}
                                className="shrink-0 px-3 py-2 bg-accent/20 hover:bg-accent/30 text-accent-light rounded-lg text-sm font-medium flex items-center gap-1.5"
                            >
                                {acmeCopied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
                                {acmeCopied ? t('common.copied') : t('common.copy')}
                            </button>
                        </div>
                        <p className="text-xs text-text-muted mb-4">{t('settings.acme.scopeIs')}: <span className="text-text-primary">{(acmeNewToken.allowed_zones || []).join(', ') || '-'}</span></p>
                        <div className="flex justify-end">
                            <button
                                type="button"
                                onClick={() => { setAcmeNewToken(null); setAcmeCopied(false) }}
                                className="px-4 py-2 bg-gradient-to-r from-accent to-accent-light hover:from-accent-light hover:to-accent text-white rounded-lg text-sm font-medium"
                            >
                                {t('settings.acme.confirmSaved')}
                            </button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    )
}
