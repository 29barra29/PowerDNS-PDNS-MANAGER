import { useState, useEffect } from 'react'
import { Search as SearchIcon, Loader2, Globe } from 'lucide-react'
import api from '../api'

export default function SearchPage() {
    const [query, setQuery] = useState('')
    const [results, setResults] = useState([])
    const [loading, setLoading] = useState(false)
    const [servers, setServers] = useState([])
    const [searched, setSearched] = useState(false)

    useEffect(() => {
        api.getServers().then(d => setServers(d.servers || []))
    }, [])

    async function handleSearch(e) {
        e.preventDefault()
        if (!query.trim()) return
        setLoading(true)
        setSearched(true)
        const all = []
        for (const s of servers) {
            if (!s.is_reachable) continue
            try {
                const data = await api.search(s.name, query)
                    ; (data.results || []).forEach(r => all.push({ ...r, _server: s.name }))
            } catch { }
        }
        setResults(all)
        setLoading(false)
    }

    return (
        <div className="space-y-6">
            <div>
                <h1 className="text-2xl font-bold text-text-primary">Suche</h1>
                <p className="text-text-muted text-sm mt-1">Durchsuche Domains, Einträge und IPs</p>
            </div>

            <form onSubmit={handleSearch} className="flex gap-3">
                <div className="relative flex-1">
                    <SearchIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-muted" />
                    <input
                        type="text"
                        value={query}
                        onChange={e => setQuery(e.target.value)}
                        placeholder="Domain, IP oder Eintrag suchen..."
                        className="w-full pl-10 pr-4 py-2.5 text-sm"
                        autoFocus
                    />
                </div>
                <button type="submit" disabled={loading} className="px-6 py-2.5 bg-gradient-to-r from-accent to-purple-600 text-white rounded-lg text-sm font-medium disabled:opacity-50 flex items-center gap-2">
                    {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <SearchIcon className="w-4 h-4" />}
                    Suchen
                </button>
            </form>

            {loading ? (
                <div className="flex items-center justify-center h-32"><Loader2 className="w-6 h-6 text-accent animate-spin" /></div>
            ) : results.length > 0 ? (
                <div className="glass-card overflow-hidden">
                    <table className="w-full text-sm">
                        <thead>
                            <tr className="border-b border-border">
                                <th className="text-left p-3 text-text-muted font-medium text-xs">Name</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">Typ</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">Wert</th>
                                <th className="text-left p-3 text-text-muted font-medium text-xs">Server</th>
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
                    <p>Keine Ergebnisse für "{query}"</p>
                </div>
            ) : null}
        </div>
    )
}
