import { useTranslation } from 'react-i18next'
import { securityBadges } from '../../userSecurity/userSecurityModel'

// Sicherheits-Badges der Benutzerkarte (F3 §2.3): "Passwortwechsel ausstehend", "2FA" (bzw. "2FA unlesbar",
// F5 §5.13) und "Passkeys: n". Rendert nichts, wenn keines zutrifft.
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const badge = { id: 'security', order: 10, when: (user) => securityBadges(user).length > 0 }

const TONES = {
    warning: 'bg-warning/10 text-warning border-warning/30',
    success: 'bg-success/10 text-success border-success/30',
    danger: 'bg-danger/10 text-danger border-danger/30',
    info: 'bg-sky-500/10 text-sky-300 border-sky-500/30',
}

const LABELS = {
    mustChange: 'users.badgeMustChange',
    '2fa': 'users.badge2fa',
    totpUnreadable: 'users.badgeTotpUnreadable',
    passkeys: 'users.badgePasskeys',
}

export default function SecurityBadge({ user }) {
    const { t } = useTranslation()
    return securityBadges(user).map((b) => (
        <span key={b.key} className={`text-xs px-2 py-0.5 rounded-full border ${TONES[b.tone] || TONES.info}`}>
            {t(LABELS[b.key], { count: b.count })}
        </span>
    ))
}
