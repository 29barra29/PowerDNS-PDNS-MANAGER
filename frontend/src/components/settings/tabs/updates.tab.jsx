import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { RefreshCw, Lock, Download, GitCommit, Code, Copy, Check, AlertTriangle } from 'lucide-react'
import { useUpdateAvailability } from '../../../hooks/useUpdateAvailability'
import { GITHUB_REPO } from '../../../lib/githubLatestVersion'
import { commitErrorKind } from '../../../lib/settingsForms.js'
import { useDateFormat } from '../../../lib/useDateFormat'
import { compareSemver } from '../../../utils/semverCompare'
import { useSettings } from '../settingsContext'

// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'updates', order: 90, labelKey: 'settings.updates', icon: Download, adminOnly: true }

// Tab "Updates".
// F8 (Welle 1): Versionsbanner mit Fehlerzustand und "Erneut pruefen" (B07); Commits einmal pro Seitenaufruf mit
// Fehlerart 404/Limit/sonstig statt pauschal "privat" (D08); Datum in der UI-Sprache (A12).
export default function UpdatesTab({ active }) {
    const { t } = useTranslation()
    const { fmtDateTime } = useDateFormat()
    const { adminInfo, isAdmin } = useSettings()
    const { updateAvailable, dismissUpdate, latestVersion, currentVersion, checked, checkError, recheck } = useUpdateAvailability()
    const [commits, setCommits] = useState([])
    const [loadingCommits, setLoadingCommits] = useState(false)
    // { kind: 'notFound'|'rateLimit'|'other', message } oder null
    const [commitError, setCommitError] = useState(null)
    // Commits nur einmal pro Seitenaufruf automatisch laden (nicht bei jedem Tab-Wechsel)
    const [commitsLoaded, setCommitsLoaded] = useState(false)
    const [rechecking, setRechecking] = useState(false)
    // "Befehl kopiert"-Toast für den Update-Tab
    const [updateCmdCopied, setUpdateCmdCopied] = useState(false)


    // "Befehl kopiert"-Toast nach 2 s zurücksetzen
    useEffect(() => {
        if (!updateCmdCopied) return
        const t = setTimeout(() => setUpdateCmdCopied(false), 2000)
        return () => clearTimeout(t)
    }, [updateCmdCopied])

    // Roter Punkt entfernen, sobald der Reiter „Updates“ geöffnet wurde
    useEffect(() => {
        if (active && updateAvailable) {
            dismissUpdate()
        }
    }, [active, updateAvailable, dismissUpdate])

    async function loadCommits() {
        setLoadingCommits(true)
        setCommitError(null)
        try {
            const res = await fetch(`https://api.github.com/repos/${GITHUB_REPO}/commits?per_page=5`, {
                headers: { Accept: 'application/vnd.github+json' },
            })
            if (!res.ok) throw Object.assign(new Error(`HTTP ${res.status}`), { status: res.status })
            const data = await res.json()
            setCommits(Array.isArray(data) ? data : [])
        } catch (err) {
            setCommitError({ kind: commitErrorKind(err?.status), message: err?.message || String(err) })
        } finally {
            setLoadingCommits(false)
            setCommitsLoaded(true)
        }
    }

    async function handleRecheck() {
        setRechecking(true)
        try { await recheck() } finally { setRechecking(false) }
    }

    // Commits beim ersten Oeffnen laden (einmal pro Seitenaufruf, F8-D08); "Neu laden" laedt erneut
    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect -- Laden beim Aktivieren wie 2.4.1
        if (active && isAdmin && !commitsLoaded && !loadingCommits) loadCommits()
    }, [active]) // eslint-disable-line react-hooks/exhaustive-deps -- nur beim Aktivieren laden (wie 2.4.1)

    // Zusaetzliches Render-Gate (F8 6.7): der Tab ist adminOnly
    if (!isAdmin) return null

    return (
        <div className="space-y-6">
            {(() => {
                const isUpToDate = !!(latestVersion && currentVersion && compareSemver(latestVersion, currentVersion) <= 0)
                const hasUpdate = !!(latestVersion && currentVersion && compareSemver(latestVersion, currentVersion) > 0)
                const checkFailed = !!checkError && !latestVersion
                return (
                    <div className={`p-4 rounded-xl border text-sm ${
                        hasUpdate
                            ? 'border-amber-500/40 bg-amber-500/10 text-text-secondary'
                            : isUpToDate
                                ? 'border-success/40 bg-success/10 text-text-secondary'
                                : checkFailed
                                    ? 'border-warning/40 bg-warning/10 text-text-secondary'
                                    : 'border-border bg-bg-hover/40 text-text-muted'
                    }`}>
                        <div className="flex items-center justify-between gap-4 flex-wrap">
                            <div className="flex-1 min-w-0">
                                {hasUpdate ? (
                                    <>
                                        <p className="font-medium text-amber-200 mb-1">{t('settingsMore.newVersionBannerTitle')}</p>
                                        <p>{t('settingsMore.newVersionBannerBody', { current: currentVersion, latest: latestVersion })}</p>
                                    </>
                                ) : isUpToDate ? (
                                    <>
                                        <p className="font-medium text-success mb-1">{t('settingsMore.upToDateTitle')}</p>
                                        <p>{t('settingsMore.upToDateBody', { current: currentVersion })}</p>
                                    </>
                                ) : checkFailed ? (
                                    <div className="flex items-start gap-2">
                                        <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5 text-warning" />
                                        <div className="space-y-2">
                                            <p className="text-warning">{t('settingsMore.versionCheckFailed')}</p>
                                            <button
                                                type="button"
                                                onClick={handleRecheck}
                                                disabled={rechecking}
                                                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md border border-border bg-bg-primary text-text-secondary hover:text-text-primary disabled:opacity-50"
                                            >
                                                <RefreshCw className={`w-3.5 h-3.5 ${rechecking ? 'animate-spin' : ''}`} />
                                                {t('settingsMore.versionRecheck')}
                                            </button>
                                        </div>
                                    </div>
                                ) : !checked ? (
                                    <p>{t('settingsMore.versionUnknown')}</p>
                                ) : null}
                            </div>
                            <div className="flex items-center gap-3 text-xs">
                                <div className="flex items-center gap-1.5">
                                    <span className="text-text-muted">{t('settingsMore.versionInstalled')}:</span>
                                    <code className="font-mono text-text-primary">{currentVersion || '–'}</code>
                                </div>
                                <div className="flex items-center gap-1.5">
                                    <span className="text-text-muted">{t('settingsMore.versionLatest')}:</span>
                                    <code className="font-mono text-text-primary">{latestVersion || '–'}</code>
                                </div>
                            </div>
                        </div>
                    </div>
                )
            })()}

            <div className="glass-card p-6">
                <div className="flex items-center gap-3 mb-6">
                    <div className="w-10 h-10 rounded-xl bg-success/20 flex items-center justify-center">
                        <Download className="w-5 h-5 text-success" />
                    </div>
                    <div>
                        <h2 className="text-lg font-semibold text-text-primary">{t('settingsMore.updateTitle')}</h2>
                        <p className="text-sm text-text-muted">{t('settingsMore.updateSubtitle')}</p>
                    </div>
                </div>

                <div className="p-5 rounded-xl bg-bg-primary border border-border mb-8">
                    <h3 className="text-sm font-semibold text-text-primary mb-3 text-accent-light">{t('settingsMore.updateHowTitle')}</h3>
                    <p className="text-sm text-text-secondary mb-5 leading-relaxed">
                        {t('settingsMore.updateSecurityPara')}
                        <br /><br />
                        {t('settingsMore.updateTerminalPara')}
                    </p>

                    {(() => {
                        const installPath = adminInfo?.install_path || ''
                        const updateCmd = `cd ${installPath || t('settingsMore.updatePathPlaceholder')} && ./update.sh`
                        const onCopy = async () => {
                            try {
                                if (navigator.clipboard?.writeText) {
                                    await navigator.clipboard.writeText(updateCmd)
                                } else {
                                    const ta = document.createElement('textarea')
                                    ta.value = updateCmd
                                    ta.style.position = 'fixed'
                                    ta.style.opacity = '0'
                                    document.body.appendChild(ta)
                                    ta.select()
                                    document.execCommand('copy')
                                    document.body.removeChild(ta)
                                }
                                setUpdateCmdCopied(true)
                            } catch { /* clipboard blockiert – egal */ }
                        }
                        return (
                            <div className="relative">
                                <div className="absolute inset-y-0 left-0 bg-accent w-1 rounded-l-lg"></div>
                                <pre className="bg-bg-hover text-text-primary p-4 pr-28 rounded-r-lg rounded-l-sm text-sm font-mono overflow-x-auto pl-6 border border-border/50 border-l-0">
                                    <span className="text-text-muted"># {t('settingsMore.updateStep1Comment')}</span>{'\n'}
                                    <span className="text-accent-light font-medium">cd</span> {installPath || t('settingsMore.updatePathPlaceholder')}{'\n\n'}
                                    <span className="text-text-muted"># {t('settingsMore.updateStep2Comment')}</span>{'\n'}
                                    <span className="text-accent-light font-medium">./update.sh</span>
                                </pre>
                                <button
                                    type="button"
                                    onClick={onCopy}
                                    className="absolute top-2 right-2 px-2.5 py-1.5 text-xs rounded-md bg-bg-primary border border-border text-text-secondary hover:text-text-primary hover:border-accent/50 flex items-center gap-1.5 transition-colors"
                                    title={t('common.copy')}
                                >
                                    {updateCmdCopied ? (
                                        <>
                                            <Check className="w-3.5 h-3.5 text-success" />
                                            {t('common.copied')}
                                        </>
                                    ) : (
                                        <>
                                            <Copy className="w-3.5 h-3.5" />
                                            {t('common.copy')}
                                        </>
                                    )}
                                </button>
                            </div>
                        )
                    })()}

                    <p className="text-xs text-text-muted mt-4">
                        💡 {t('settingsMore.updatesScriptHint')}
                    </p>
                    {!adminInfo?.install_path && (
                        <p className="text-xs text-amber-400/90 mt-2">
                            {t('settingsMore.updatePathMissingHint')}
                        </p>
                    )}
                </div>

                {/* Commits von GitHub */}
                <div>
                    <div className="flex items-center justify-between mb-4">
                        <h3 className="text-sm font-semibold text-text-primary flex items-center gap-2">
                            <GitCommit className="w-4 h-4" /> {t('settingsMore.lastChanges')}
                        </h3>
                        <button onClick={loadCommits} disabled={loadingCommits} className="text-xs text-text-muted hover:text-text-primary flex items-center gap-1">
                            <RefreshCw className={`w-3 h-3 ${loadingCommits ? 'animate-spin' : ''}`} /> {loadingCommits ? t('settings.loadingDot') : t('settings.reload')}
                        </button>
                    </div>

                    {commitError ? (
                        <div className="p-4 rounded-lg bg-bg-hover border border-border text-text-muted text-sm text-center flex flex-col items-center gap-2" role="status">
                            {commitError.kind === 'notFound'
                                ? <Lock className="w-5 h-5 opacity-50" />
                                : <AlertTriangle className="w-5 h-5 opacity-60" />}
                            <span>
                                {commitError.kind === 'notFound'
                                    ? t('settings.updatesRepoNotFound')
                                    : commitError.kind === 'rateLimit'
                                        ? t('settings.updatesRateLimited')
                                        : t('settings.updatesLoadFailed', { error: commitError.message })}
                            </span>
                            <button
                                type="button"
                                onClick={loadCommits}
                                disabled={loadingCommits}
                                className="text-xs text-accent-light hover:underline disabled:opacity-50"
                            >
                                {t('settings.reload')}
                            </button>
                        </div>
                    ) : commits.length === 0 && !loadingCommits ? (
                        <p className="text-sm text-text-muted">{t('settingsMore.noChangesFound')}</p>
                    ) : (
                        <div className="space-y-3">
                            {commits.map((c) => (
                                <div key={c.sha} className="p-4 rounded-lg bg-bg-primary border border-border flex gap-4 items-start">
                                    <div className="w-8 h-8 rounded-full bg-accent/10 flex items-center justify-center shrink-0 mt-1">
                                        <Code className="w-4 h-4 text-accent-light" />
                                    </div>
                                    <div className="min-w-0 flex-1">
                                        <div className="flex items-center gap-2 mb-1">
                                            <span className="text-sm font-medium text-text-primary truncate">{String(c.commit?.message || '').split('\n')[0]}</span>
                                        </div>
                                        <div className="flex items-center gap-3 text-xs text-text-muted">
                                            <span>{fmtDateTime(c.commit?.author?.date)}</span>
                                            <span className="font-mono bg-bg-hover px-1.5 py-0.5 rounded border border-border">{c.sha.substring(0, 7)}</span>
                                            <span>{t('settingsMore.by')} <strong className="text-text-secondary">{c.commit?.author?.name || '–'}</strong></span>
                                        </div>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </div>
        </div>
    )
}
