import { useState, useEffect, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Search as SearchIcon, Loader2, Globe } from 'lucide-react'
import api from '../api'

const MIN_QUERY_LENGTH = 3  // Ab 3 Zeichen: Teil-Suche (z. B. "exam" findet "example.de")
const DEBOUNCE_MS = 400     // Kurz warten nach Tippen, dann automatisch suchen

export default function SearchPage() {
    const { t } = useTranslation()
    const [query, setQuery] = useState('')
    const [results, setResults] = useState([])
    const [loading, setLoading] = useState(false)
    const [servers, setServers] = useState([])
    const [searched, setSearched] = useState(false)

    useEffect(() => {
        api.getServers().then(d => setServers(d.servers || []))
    }, [])

    const runSearch = useCallback(async (searchTerm) => {
        if (!searchTerm.trim() || servers.length === 0) return
        setLoading(true)
        setSearched(true)
        const all = []
        for (const s of servers) {
            if (!s.is_reachable) continue
            try {
                const data = await api.search(s.name, searchTerm)
                ;(data.results || []).forEach(r => all.push({ ...r, _server: s.name }))
            } catch { /* ignore */ }
        }
        setResults(all)
        setLoading(false)
    }, [servers])

    // Live-Suche: ab 3 Zeichen nach kurzer Pause automatisch suchen
    /* eslint-disable react-hooks/set-state-in-effect -- debounced search + clear when query short */
    useEffect(() => {
        const t = query.trim()
        if (t.length < MIN_QUERY_LENGTH) {
            if (searched) setResults([])
            return
        }
        const timer = setTimeout(() => runSearch(t), DEBOUNCE_MS)
        return () => clearTimeout(timer)
    }, [query, runSearch]) // eslint-disable-line react-hooks/exhaustive-deps -- searched intentionally omitted
    /* eslint-enable react-hooks/set-state-in-effect */

    async function handleSearch(e) {
        e.preventDefault()
        if (!query.trim()) return
        await runSearch(query.trim())
    }

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-text-primary">{t('search.title')}</h1>
                <p className="text-text-muted text-sm mt-1">{t('search.subtitle')}</p>
            </div>

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
                <button type="submit" disabled={loading} className="px-6 py-2.5 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                    {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <SearchIcon className="w-4 h-4" />}
                    {t('search.button')}
                </button>
            </form>

            {loading ? (
                <div className="flex items-center justify-center h-32"><Loader2 className="w-6 h-6 text-accent animate-spin" /></div>
            ) : results.length > 0 ? (
                <div className="glass-card overflow-hidden">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-border">
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('search.name')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('search.type')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('search.value')}</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">{t('search.server')}</th>
                            </tr>
                        </thead>
                        <tbody>
                            {results.map((r, i) => (
                                <tr key={i} className="border-b border-border/30 hover:bg-bg-hover/30">
                                    <td className="p-3 font-mono text-xs text-text-primary">{r.name}</td>
                                    <td className="p-3"><span className="text-xs px-1.5 py-0.5 bg-accent/10 text-accent-light rounded">{r.type}</span></td>
                                    <td className="p-3 font-mono text-xs text-text-secondary break-all">{r.content}</td>
                                    <td className="p-3 text-text-muted text-xs">{r._server}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            ) : searched ? (
                <div className="text-center py-12 text-text-muted">
                    <Globe className="w-12 h-12 mx-auto mb-3 opacity-30" />
                    <p>{t('search.noResults', { query })}</p>
                </div>
            ) : null}
        </div>
    )
}
