import { useTranslation } from 'react-i18next'
import { Key } from 'lucide-react'

// Badge "aktive API-Tokens" der Benutzerliste (F14 §2.7, §3.9): Schluessel-Symbol + Anzahl der benutzbaren
// Panel-Tokens (nicht widerrufen, nicht pausiert, nicht abgelaufen; GET /auth/users -> panel_token_count).
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const badge = { id: 'token-count', order: 30, when: (user) => Number(user?.panel_token_count || 0) > 0 }

export default function TokenCountBadge({ user }) {
    const { t } = useTranslation()
    const count = Number(user?.panel_token_count || 0)
    const title = t('users.apiTokenCountTitle')
    return (
        <span
            className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full border bg-sky-500/10 text-sky-300 border-sky-500/30"
            title={title}
            aria-label={`${title}: ${count}`}
        >
            <Key className="w-3 h-3" aria-hidden="true" />
            {count}
        </span>
    )
}
