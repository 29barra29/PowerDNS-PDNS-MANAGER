import { useTranslation } from 'react-i18next'
import { statusLabel, statusStyle } from './webhookUi'

// Badge je Zustell-Status (queued, in_progress, succeeded, failed, dead, cancelled), F6 6.1.
export default function WebhookStatusBadge({ status, className = '' }) {
    const { t } = useTranslation()
    return (
        <span
            className={`inline-flex items-center px-2 py-0.5 rounded-full border text-[11px] font-medium whitespace-nowrap ${statusStyle(status)} ${className}`}
        >
            {statusLabel(t, status)}
        </span>
    )
}
