import { useTranslation } from 'react-i18next'
import { Server } from 'lucide-react'
import { PEER_STATE_KEYS, PEER_STATE_LEVEL, visiblePeers } from '../../zoneDetail/dnssecModel.js'

// "Andere Server mit dieser Zone" (F4 §2.11): Zustand je Peer als Badge, zone_missing ausgeblendet,
// Badge "Speichern: Nein" bei allow_writes=false, Key-Tags des Peers.
const BADGE = {
    ok: 'bg-success/10 border-success/30 text-success',
    warning: 'bg-warning/10 border-warning/30 text-warning',
    danger: 'bg-danger/10 border-danger/30 text-danger',
    neutral: 'bg-bg-secondary border-border text-text-secondary',
}

export default function DnssecPeersList({ peers }) {
    const { t } = useTranslation()
    const list = visiblePeers(peers)
    if (!list.length) return null
    return (
        <div>
            <p className="text-xs font-medium text-text-muted mb-2">{t('dnssec.peersTitle')}</p>
            <ul className="space-y-1.5">
                {list.map((p) => {
                    const level = PEER_STATE_LEVEL[p.state] || 'neutral'
                    const key = PEER_STATE_KEYS[p.state]
                    return (
                        <li key={p.server} className="flex flex-wrap items-center gap-2 text-sm">
                            <Server className="w-4 h-4 text-text-muted shrink-0" aria-hidden="true" />
                            <span className="text-text-primary font-medium">{p.server}</span>
                            <span className={`text-xs px-2 py-0.5 rounded-full border ${BADGE[level]}`}>
                                {key ? t(key) : p.state}
                            </span>
                            {p.allow_writes === false && (
                                <span className="text-xs px-2 py-0.5 rounded-full border border-border text-text-muted">
                                    {t('dnssec.peerReadOnly')}
                                </span>
                            )}
                            {Array.isArray(p.key_tags) && p.key_tags.length > 0 && (
                                <span className="text-xs text-text-muted">{t('dnssec.peerKeyTags', { tags: p.key_tags.join(', ') })}</span>
                            )}
                        </li>
                    )
                })}
            </ul>
        </div>
    )
}
