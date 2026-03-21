/** GitHub-Repo für Releases / Tags */
export const GITHUB_REPO = '29barra29/dns-manager'

export async function fetchLatestRemoteVersion() {
    try {
        const r = await fetch(`https://api.github.com/repos/${GITHUB_REPO}/releases/latest`, {
            headers: { Accept: 'application/vnd.github+json' },
        })
        if (r.ok) {
            const j = await r.json()
            const tag = j.tag_name
            if (tag) return String(tag).replace(/^v/i, '')
        }
    } catch {
        /* ignore */
    }
    try {
        const r2 = await fetch(`https://api.github.com/repos/${GITHUB_REPO}/tags?per_page=1`, {
            headers: { Accept: 'application/vnd.github+json' },
        })
        if (r2.ok) {
            const arr = await r2.json()
            const name = arr[0]?.name
            if (name) return String(name).replace(/^v/i, '')
        }
    } catch {
        /* ignore */
    }
    return null
}
