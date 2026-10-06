import { useCallback, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Loader2, RefreshCw, ShieldCheck, ShieldAlert, Copy, Check, AlertTriangle, AlertCircle, CheckCircle2, ExternalLink } from 'lucide-react'
import api from '../api'
import {
    SECRET_FIELD_LABEL_KEYS, secretColumnsForDisplay, secretsTotals, settingTabForField, unreadableCount,
} from '../api/secrets'
import { useDateFormat } from '../lib/useDateFormat'

// Statuskarte "Verschluesselung gespeicherter Geheimnisse" (F5 §2 C/D/I, §6.2) im Admin-Tab "Sicherheit".
// Zeigt Modus, Schluesselquelle, Fingerprint, Zusatzschluessel, Sicherungshinweis, Zaehler je Feld und die
// nicht entschluesselbaren Eintraege mit Sprung zur Stelle, an der sie neu eingetragen werden.
// Nur lesend: Schluessel/Recovery gibt es bewusst nur per CLI im Container. Ladefehler bleiben in der Karte.
//
// Abweichung von F5 §6.2 [S7]: Als Sicherungsbefehl steht `./update.sh --backup-key-only` (legt die Kopie in
// PDNSMGR_KEY_BACKUP_DIR, Standard ~/.pdnsmgr-keys, ab) statt `docker compose exec ... > secret_key_backup.key`,
// das den Schluessel in den Stack-Ordner neben den DB-Dump schreiben wuerde.

const PLAINTEXT_FIX_COMMAND = 'docker compose exec -u root backend chown -R 1001:1001 /app/data && docker compose restart backend'
const BACKUP_COMMAND = './update.sh --backup-key-only'
const DOCS_URL_DE = 'https://pdns-manager.gemtecgames.com/docs/features/verschluesselung/'
const DOCS_URL_EN = 'https://pdns-manager.gemtecgames.com/en/docs/features/verschluesselung/'

const KEY_SOURCE_LABELS = Object.freeze({
    env: 'settings.secrets.keySourceEnv',
    file: 'settings.secrets.keySourceFile',
    generated: 'settings.secrets.keySourceGenerated',
})

const FALLBACK_REASON_LABELS = Object.freeze({
    key_file_unwritable: 'settings.secrets.fallbackKeyFileUnwritable',
    key_file_unreadable: 'settings.secrets.fallbackKeyFileUnreadable',
    key_file_invalid: 'settings.secrets.fallbackKeyFileInvalid',
})

function CommandBox({ command, label }) {
    const { t } = useTranslation()
    const [state, setState] = useState('idle') // idle | copied | failed
    const timer = useRef(null)

    useEffect(() => () => clearTimeout(timer.current), [])

    async function copy() {
        clearTimeout(timer.current)
        try {
            if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable')
            await navigator.clipboard.writeText(command)
            setState('copied')
        } catch {
            setState('failed')
        }
        timer.current = setTimeout(() => setState('idle'), 2500)
    }

    return (
        <div className="mt-2">
            <div className="relative">
                <pre className="p-2 pr-10 rounded-lg bg-bg-primary border border-border text-xs font-mono text-text-primary overflow-x-auto whitespace-pre-wrap break-all" aria-label={label}>
                    {command}
                </pre>
                <button type="button" onClick={copy}
                    className="absolute right-2 top-2 p-1 rounded-md text-text-muted hover:text-text-primary hover:bg-bg-hover"
                    title={t('common.copy')} aria-label={t('common.copy')}>
                    {state === 'copied' ? <Check className="w-4 h-4 text-success" /> : <Copy className="w-4 h-4" />}
                </button>
            </div>
            <span className="sr-only" aria-live="polite">{state === 'copied' ? t('common.copied') : ''}</span>
            {state === 'failed' && <p className="mt-1 text-xs text-warning">{t('settings.secrets.copyFailed')}</p>}
        </div>
    )
}

function Notice({ tone, children }) {
    const cls = tone === 'error'
        ? 'bg-danger/10 border-danger/30 text-danger'
        : tone === 'warning'
            ? 'bg-warning/10 border-warning/30 text-warning'
            : tone === 'ok'
                ? 'bg-success/10 border-success/30 text-success'
                : 'bg-accent/5 border-accent/20 text-text-secondary'
    const Icon = tone === 'error' ? AlertCircle : tone === 'warning' ? AlertTriangle : tone === 'ok' ? CheckCircle2 : null
    return (
        <div className={`p-3 rounded-lg border text-sm flex items-start gap-2 ${cls}`}>
            {Icon && <Icon className="w-4 h-4 shrink-0 mt-0.5" aria-hidden="true" />}
            <div className="flex-1 min-w-0 break-words">{children}</div>
        </div>
    )
}

// Props: onNavigateTab(tabId) – Sprung zu einem Einstellungs-Tab; active – Tab sichtbar (laedt beim Aktivieren);
// reloadKey – Aenderung erzwingt ein Neuladen (z. B. nach dem Speichern eines neuen Secrets im selben Tab).
export default function SecretsStatusCard({ onNavigateTab, active = true, reloadKey = 0 }) {
    const { t, i18n } = useTranslation()
    const { fmtDateTime } = useDateFormat()
    const [status, setStatus] = useState(null)
    const [loading, setLoading] = useState(true)
    const [loadErr, setLoadErr] = useState('')
    const ctrlRef = useRef(null)

    const load = useCallback(async () => {
        ctrlRef.current?.abort()
        const ctrl = new AbortController()
        ctrlRef.current = ctrl
        setLoading(true)
        setLoadErr('')
        try {
            const data = await api.getSecretsStatus({ signal: ctrl.signal })
            if (ctrl.signal.aborted) return
            setStatus(data && typeof data === 'object' ? data : null)
        } catch (err) {
            if (err?.name === 'AbortError') return
            setLoadErr(err?.message || t('settings.secrets.loadError'))
        } finally {
            if (ctrlRef.current === ctrl) setLoading(false)
        }
    }, [t])

    // Beim (erneuten) Aktivieren des Tabs frisch laden – der Status ist ein Live-Scan.
    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- Laden beim Aktivieren (wie die anderen Tabs)
        if (active) load()
    }, [active, load, reloadKey])

    useEffect(() => () => ctrlRef.current?.abort(), [])

    const lang = (i18n.resolvedLanguage || i18n.language || 'de').toLowerCase()
    const docsUrl = lang.startsWith('de') ? DOCS_URL_DE : DOCS_URL_EN
    const issues = new Set(Array.isArray(status?.issues) ? status.issues : [])
    const totals = secretsTotals(status)
    const unreadable = Array.isArray(status?.unreadable) ? status.unreadable : []
    const columns = secretColumnsForDisplay(status)
    const isFallback = status?.mode === 'plaintext_fallback'
    const health = status?.health
    const HeadIcon = !status || health === 'ok' ? ShieldCheck : ShieldAlert
    const headTone = !status ? 'bg-accent/20 text-accent-light'
        : health === 'error' ? 'bg-danger/20 text-danger'
            : health === 'warning' ? 'bg-warning/20 text-warning'
                : 'bg-success/20 text-success'

    function goTab(tab) {
        if (tab && typeof onNavigateTab === 'function') onNavigateTab(tab)
    }

    function fieldLabel(id) {
        const key = SECRET_FIELD_LABEL_KEYS[id]
        return key ? t(key) : id
    }

    function renderUnreadableItem(item, idx) {
        const key = `${item.kind}:${item.field}:${item.id ?? item.name ?? idx}`
        let text
        let action = null
        if (item.kind === 'server') {
            text = t('settings.secrets.unreadableServer', { name: item.name || `#${item.id}` })
            action = (
                <button type="button" onClick={() => goTab('servers')} className="text-xs text-accent-light hover:underline shrink-0">
                    {t('settings.secrets.goToServers')}
                </button>
            )
        } else if (item.kind === 'webhook') {
            const vars = { name: item.name || `#${item.id}`, owner: item.owner || '—' }
            text = item.field === 'webhooks.url'
                ? t('settings.secrets.unreadableWebhookUrl', vars)
                : t('settings.secrets.unreadableWebhook', vars)
        } else if (item.kind === 'user_totp') {
            const vars = { username: item.name || `#${item.id}` }
            text = item.field === 'users.totp_pending_secret'
                ? t('settings.secrets.unreadableTotpPending', vars)
                : t('settings.secrets.unreadableTotp', vars)
            if (item.field !== 'users.totp_pending_secret') {
                action = (
                    <Link to="/users" className="text-xs text-accent-light hover:underline shrink-0">
                        {t('settings.secrets.goToUsers')}
                    </Link>
                )
            }
        } else {
            text = t('settings.secrets.unreadableSetting', { field: fieldLabel(item.field) })
            const tab = settingTabForField(item.field)
            if (tab) {
                action = (
                    <button type="button" onClick={() => goTab(tab)} className="text-xs text-accent-light hover:underline shrink-0">
                        {t('settings.secrets.goToSettingsTab')}
                    </button>
                )
            }
        }
        return (
            <li key={key} className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between p-2 rounded-lg border border-danger/20 bg-danger/5">
                <span className="text-sm text-text-primary break-words">{text}</span>
                {action}
            </li>
        )
    }

    return (
        <div className="glass-card p-6" aria-busy={loading}>
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between mb-5">
                <div className="flex items-center gap-3">
                    <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${headTone}`}>
                        <HeadIcon className="w-5 h-5" aria-hidden="true" />
                    </div>
                    <div>
                        <h2 className="text-lg font-semibold text-text-primary">{t('settings.secrets.title')}</h2>
                        <p className="text-sm text-text-muted">{t('settings.secrets.subtitle')}</p>
                    </div>
                </div>
                <button type="button" onClick={load} disabled={loading}
                    className="flex items-center gap-2 px-3 py-2 text-sm text-text-muted hover:text-text-primary hover:bg-bg-hover rounded-lg transition-colors border border-border disabled:opacity-50 self-start">
                    {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
                    {t('settingsMore.refresh')}
                </button>
            </div>

            {loading && !status && !loadErr ? (
                <div className="flex justify-center py-8"><Loader2 className="w-6 h-6 text-accent animate-spin" /></div>
            ) : loadErr && !status ? (
                <Notice tone="error">{t('settings.secrets.loadError')} {loadErr !== t('settings.secrets.loadError') && <span className="block text-xs mt-1">{loadErr}</span>}</Notice>
            ) : status ? (
                <div className="space-y-5">
                    {loadErr && <Notice tone="error">{loadErr}</Notice>}

                    {/* Statuszeile */}
                    <div className="space-y-2">
                        {isFallback && (
                            <Notice tone="error">
                                <p>{t('settings.secrets.statusPlaintext')}</p>
                                {status.fallback_reason && FALLBACK_REASON_LABELS[status.fallback_reason] && (
                                    <p className="text-xs mt-1">{t(FALLBACK_REASON_LABELS[status.fallback_reason])}</p>
                                )}
                                {/* Rechte-Problem (F5 §2 I): chown + Neustart; bei ungueltiger Datei hilft das nicht */}
                                {status.fallback_reason !== 'key_file_invalid' && (
                                    <>
                                        <p className="text-xs mt-2">{t('settings.secrets.fallbackFixCommand')}</p>
                                        <CommandBox command={PLAINTEXT_FIX_COMMAND} label={t('settings.secrets.fallbackFixCommand')} />
                                    </>
                                )}
                            </Notice>
                        )}
                        {issues.has('unreadable_values') && (
                            <Notice tone="error">{t('settings.secrets.statusUnreadable', { count: unreadableCount(status) })}</Notice>
                        )}
                        {issues.has('plaintext_values') && (
                            <Notice tone="warning">{t('settings.secrets.statusPlaintextValues')}</Notice>
                        )}
                        {issues.has('key_file_permissions') && (
                            <Notice tone="warning">{t('settings.secrets.keyFilePermissions')}</Notice>
                        )}
                        {issues.has('key_file_obsolete') && (
                            <Notice tone="info">{t('settings.secrets.keyFileObsolete')}</Notice>
                        )}
                        {health === 'ok' && (
                            <Notice tone="ok">{totals.stored === 0 ? t('settings.secrets.noSecretsYet') : t('settings.secrets.statusOk')}</Notice>
                        )}
                        {health === 'warning' && !issues.has('plaintext_values') && !issues.has('key_file_permissions') && (
                            <Notice tone="warning">{t('settings.secrets.statusWarning')}</Notice>
                        )}
                    </div>

                    {/* Schluessel */}
                    <dl className="grid grid-cols-1 sm:grid-cols-[minmax(0,14rem)_1fr] gap-x-4 gap-y-2 text-sm">
                        <dt className="text-text-muted">{t('settings.secrets.keySource')}</dt>
                        <dd className="text-text-primary">
                            {status.key_source && KEY_SOURCE_LABELS[status.key_source]
                                ? t(KEY_SOURCE_LABELS[status.key_source])
                                : t('settings.secrets.keySourceNone')}
                        </dd>

                        {status.key_source !== 'env' && (
                            <>
                                <dt className="text-text-muted">{t('settings.secrets.keyFile')}</dt>
                                <dd className="text-text-primary font-mono break-all">
                                    {status.key_file || '–'}
                                    {!status.key_file_exists && <span className="ml-2 font-sans text-warning">{t('settings.secrets.keyFileMissing')}</span>}
                                </dd>
                            </>
                        )}

                        <dt className="text-text-muted">{t('settings.secrets.fingerprint')}</dt>
                        <dd className="text-text-primary">
                            <span className="font-mono">{status.key_fingerprint || '–'}</span>
                            <p className="text-xs text-text-muted mt-0.5">{t('settings.secrets.fingerprintHint')}</p>
                        </dd>

                        <dt className="text-text-muted">{t('settings.secrets.previousKeys')}</dt>
                        <dd className="text-text-primary">
                            {Number(status.decrypt_only_keys) || 0}
                            {issues.has('previous_keys_still_needed') && (
                                <span className="block text-xs text-warning mt-0.5">{t('settings.secrets.previousKeysStillNeeded')}</span>
                            )}
                            {issues.has('previous_keys_unused') && (
                                <span className="block text-xs text-success mt-0.5">{t('settings.secrets.previousKeysNotNeeded')}</span>
                            )}
                        </dd>
                    </dl>

                    {/* Sicherung */}
                    {!isFallback && status.key_source && (
                        status.key_source === 'env' ? (
                            <Notice tone="info">{t('settings.secrets.backupHintEnv')}</Notice>
                        ) : (
                            <Notice tone={issues.has('key_only_in_volume') ? 'warning' : 'info'}>
                                <p>{t('settings.secrets.backupHintFile')}</p>
                                <p className="mt-2 text-xs text-text-secondary">{t('settings.secrets.backupCommand')}</p>
                                <CommandBox command={BACKUP_COMMAND} label={t('settings.secrets.backupCommand')} />
                                <p className="mt-1 text-xs text-text-muted">{t('settings.secrets.backupCommandHint')}</p>
                            </Notice>
                        )
                    )}

                    {status.last_startup?.key_generated && (
                        <Notice tone="warning">{t('settings.secrets.keyGenerated')}</Notice>
                    )}

                    {/* Nicht entschluesselbare Eintraege */}
                    {unreadable.length > 0 && (
                        <div>
                            <h3 className="text-sm font-semibold text-text-primary mb-2">{t('settings.secrets.unreadableTitle')}</h3>
                            <ul className="space-y-2">{unreadable.map(renderUnreadableItem)}</ul>
                        </div>
                    )}

                    {/* Zaehler je Feld */}
                    <div>
                        <h3 className="text-sm font-semibold text-text-primary mb-2">{t('settings.secrets.columnsTitle')}</h3>
                        <div className="overflow-x-auto rounded-lg border border-border">
                            <table className="w-full text-sm">
                                <thead className="bg-bg-tertiary text-text-muted text-xs">
                                    <tr>
                                        <th scope="col" className="text-left font-medium px-3 py-2">{t('settings.secrets.colField')}</th>
                                        <th scope="col" className="text-right font-medium px-3 py-2">{t('settings.secrets.colEncrypted')}</th>
                                        <th scope="col" className="text-right font-medium px-3 py-2">{t('settings.secrets.colPlaintext')}</th>
                                        <th scope="col" className="text-right font-medium px-3 py-2">{t('settings.secrets.colUnreadable')}</th>
                                        <th scope="col" className="text-right font-medium px-3 py-2">{t('settings.secrets.colEmpty')}</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {columns.map((c) => {
                                        const enc = (Number(c.encrypted) || 0) + (Number(c.encrypted_old) || 0)
                                        const plain = Number(c.plaintext) || 0
                                        const bad = Number(c.unreadable) || 0
                                        return (
                                            <tr key={c.id} className="border-t border-border">
                                                <th scope="row" className="text-left font-normal px-3 py-2 text-text-primary">{fieldLabel(c.id)}</th>
                                                <td className="text-right px-3 py-2 tabular-nums text-text-secondary">{enc}</td>
                                                <td className={`text-right px-3 py-2 tabular-nums ${plain > 0 && !isFallback ? 'text-warning font-medium' : 'text-text-secondary'}`}>{plain}</td>
                                                <td className={`text-right px-3 py-2 tabular-nums ${bad > 0 ? 'text-danger font-medium' : 'text-text-secondary'}`}>{bad}</td>
                                                <td className="text-right px-3 py-2 tabular-nums text-text-muted">{Number(c.empty) || 0}</td>
                                            </tr>
                                        )
                                    })}
                                </tbody>
                            </table>
                        </div>
                    </div>

                    {/* Letzter Start + Doku */}
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between text-xs text-text-muted">
                        {status.last_startup ? (
                            <span>
                                {t('settings.secrets.lastStartup', {
                                    migrated: Number(status.last_startup.migrated) || 0,
                                    rotated: Number(status.last_startup.rotated) || 0,
                                })}
                                {status.last_startup.at && <> ({fmtDateTime(status.last_startup.at)})</>}
                            </span>
                        ) : <span />}
                        <a href={docsUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-accent-light hover:underline">
                            <ExternalLink className="w-3.5 h-3.5" aria-hidden="true" /> {t('settings.secrets.docsLink')}
                        </a>
                    </div>
                </div>
            ) : null}
        </div>
    )
}
