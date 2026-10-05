import { useState, useEffect, useCallback, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Search as SearchIcon, Loader2, Globe, AlertCircle, AlertTriangle, ExternalLink } from 'lucide-react'
import api from '../api'
import { SEARCH_MAX_RESULTS, mergeSearchOutcomes, searchResultLink, searchResultZone } from '../lib/searchResults.js'

const MIN_QUERY_LENGTH = 3  // Ab 3 Zeichen: Teil-Suche (z. B. "exam" findet "example.de")
const DEBOUNCE_MS = 400     // Kurz warten nach Tippen, dann automatisch suchen

// Suche ueber alle erreichbaren Server (F8 2.2, E01–E05):
// - ein Lauf pro Begriff (Debounce ohne setState im Effekt), aeltere Laeufe werden abgebrochen bzw. verworfen
// - Server parallel (Promise.allSettled); Fehler, uebersprungene und gekappte Server erscheinen als gelber Hinweis
// - Spalte "Zone" mit Link in die Zonenansicht
export default function SearchPage() {
    const { t } = useTranslation()
    const [query, setQuery] = useState('')
    const [servers, setServers] = useState([])
    const [serversError, setServersError] = useState('')
    const [loading, setLoading] = useState(false)
    // letzte abgeschlossene Suche: { term, results, serverErrors, skipped, truncatedServers }
    const [outcome, setOutcome] = useState(null)
    // per Enter/Button abgeschickter Begriff (zeigt Ergebnisse auch unter MIN_QUERY_LENGTH)
    const [submittedTerm, setSubmittedTerm] = useState('')

    const abortRef = useRef(null)
    const runIdRef = useRef(0)
    // Begriff des laufenden bzw. zuletzt gestarteten Laufs (ein Debounce mit demselben Begriff startet nichts)
    const startedTermRef = useRef('')

    useEffect(() => {
        api.getServers()
            .then((d) => setServers(d.servers || []))
            .catch((err) => setServersError(err.message || String(err)))
    }, [])

    // Laufende Suche beim Verlassen der Seite abbrechen
    useEffect(() => () => abortRef.current?.abort(), [])

    const runSearch = useCallback(async (term, { force = false } = {}) => {
        const q = term.trim()
        if (!q || servers.length === 0) return
        if (!force && startedTermRef.current === q) return
        abortRef.current?.abort()
        const controller = new AbortController()
        abortRef.current = controller
        const runId = ++runIdRef.current
        startedTermRef.current = q
        setLoading(true)

        const reachable = servers.filter((s) => s.is_reachable)
        const skipped = servers.filter((s) => !s.is_reachable).map((s) => s.name)
        const settled = await Promise.allSettled(
            reachable.map((s) => api.search(s.name, q, { signal: controller.signal })),
        )
        // Ein neuerer Lauf hat uebernommen: Ergebnis verwerfen (dessen Spinner bleibt)
        if (runId !== runIdRef.current) return
        if (controller.signal.aborted) {
            // abgebrochen (Begriff zu kurz geworden): still beenden, derselbe Begriff darf spaeter neu suchen
            startedTermRef.current = ''
            setLoading(false)
            return
        }
        const merged = mergeSearchOutcomes(settled.map((r, i) => ({ server: reachable[i].name, ...r })))
        setOutcome({ term: q, skipped, ...merged })
        setLoading(false)
    }, [servers])

    // Live-Suche: ab 3 Zeichen nach kurzer Pause genau einmal; darunter laufende Suche abbrechen.
    useEffect(() => {
        const q = query.trim()
        if (q.length < MIN_QUERY_LENGTH) {
            abortRef.current?.abort()
            return
        }
        const timer = setTimeout(() => runSearch(q), DEBOUNCE_MS)
        return () => clearTimeout(timer)
    }, [query, runSearch])

    async function handleSearch(e) {
        e.preventDefault()
        const q = query.trim()
        if (!q) return
        setSubmittedTerm(q)
        await runSearch(q, { force: true })
    }

    const q = query.trim()
    const showResults = !!outcome && (q.length >= MIN_QUERY_LENGTH || (submittedTerm !== '' && submittedTerm === q))
    const results = showResults ? outcome.results : []
    const showNoResults = showResults && !loading && outcome.term === q && results.length === 0
    const hasNotice = showResults && (outcome.serverErrors.length > 0 || outcome.skipped.length > 0 || outcome.truncatedServers.length > 0)

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-text-primary">{t('search.title')}</h1>
                <p className="text-text-muted text-sm mt-1">{t('search.subtitle')}</p>
            </div>

            {serversError && (
                <div className="p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-center gap-3" role="alert">
                    <AlertCircle className="w-5 h-5 shrink-0" />
                    <p className="text-sm break-words min-w-0">{serversError}</p>
                    <button onClick={() => setServersError('')} className="ml-auto text-xs hover:underline" aria-label={t('common.close')}>×</button>
                </div>
            )}

            <form onSubmit={handleSearch} className="flex gap-3">
                <div className="relative flex-1">
                    <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
                    <input
                        type="text"
                        value={query}
                        onChange={e => setQuery(e.target.value)}
                        placeholder={t('search.placeholder')}
                        className="w-full pl-10 pr-4 py-2.5 text-sm"
                        autoFocus
                    />
                </div>
                <button type="submit" disabled={loading || !q} className="px-6 py-2.5 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                    {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <SearchIcon className="w-4 h-4" />}
                    {t('search.button')}
                </button>
            </form>

            {hasNotice && !loading && (
                <div className="p-4 rounded-xl bg-warning/10 border border-warning/30 text-warning flex items-start gap-3" role="status">
                    <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" />
                    <div className="text-sm space-y-1 min-w-0">
                        {outcome.serverErrors.length > 0 && (
                            <>
                                <p>{t('search.serverErrors')}</p>
                                <ul className="list-disc list-inside break-words">
                                    {outcome.serverErrors.map((e) => <li key={e.server}>{e.server}: {e.message}</li>)}
                                </ul>
                            </>
                        )}
                        {outcome.skipped.map((server) => <p key={server}>{t('search.serverSkipped', { server })}</p>)}
                        {outcome.truncatedServers.length > 0 && <p>{t('search.truncated', { max: SEARCH_MAX_RESULTS })}</p>}
                    </div>
                </div>
            )}

            {loading ? (
                <div className="flex items-center justify-center h-32"><Loader2 className="w-6 h-6 text-accent animate-spin" /></div>
            ) : results.length > 0 ? (
                <div className="glass-card overflow-hidden">
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm min-w-[760px]">
                        <thead>
                            <tr className="border-b border-border">
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('search.name')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('search.type')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('search.value')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('search.zone')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('search.server')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {results.map((r) => {
                                const srvList = r._servers && r._servers.length ? r._servers : [r._server]
                                const zone = searchResultZone(r)
                                const link = searchResultLink(r)
                                return (
                                <tr key={`${r.name}|${r.type}|${r.content}`} className="border-b border-border/30 hover:bg-bg-hover/30">
                                    <td className="p-3 font-mono text-xs text-text-primary">{r.name}</td>
                                    <td className="p-3"><span className="text-xs px-1.5 py-0.5 bg-accent/10 text-accent-light rounded">{r.type}</span></td>
                                    <td className="p-3 font-mono text-xs text-text-secondary break-all">{r.content}</td>
                                    <td className="p-3 font-mono text-xs">
                                        {link ? (
                                            <Link to={link} className="inline-flex items-center gap-1 text-accent-light hover:underline break-all" title={t('search.openZone')}>
                                                {zone} <ExternalLink className="w-3 h-3 shrink-0" aria-hidden="true" />
                                            </Link>
                                        ) : (zone || '–')}
                                    </td>
                                    <td className="p-3 text-text-muted text-xs">
                                        <div className="flex flex-wrap gap-1">
                                            {srvList.map(srv => (
                                                <span key={srv} className="text-xs px-1.5 py-0.5 rounded bg-bg-secondary border border-border">{srv}</span>
                                            ))}
                                        </div>
                                    </td>
                                </tr>
                                )
                            })}
                        </tbody>
                    </table>
                  </div>
                </div>
            ) : showNoResults ? (
                <div className="text-center py-12 text-text-muted">
                    <Globe className="w-12 h-12 mx-auto mb-3 opacity-30" />
                    <p>{t('search.noResults', { query: q })}</p>
                </div>
            ) : null}
        </div>
    )
}
