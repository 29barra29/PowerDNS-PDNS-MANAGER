import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { History } from 'lucide-react'
import ZoneHistoryPanel from '../../components/zoneHistory/ZoneHistoryPanel'
import { rollbackOutcome } from '../../components/audit/historyModel.js'
import { fanoutSummary, formatFanoutErrors, formatFanoutWarnings } from '../../lib/fanout.js'

// Tab "Verlauf" der Zonenansicht (F7 §2.1-2.4, §6.4; Plan B.14). URL: ?tab=history[&hname=&htype=][&entry=].
// Fuer jeden Nutzer mit Zugriff auf die Seite; Rollback nur mit Schreibrecht (Backend prueft).
// eslint-disable-next-line react-refresh/only-export-components -- Slot-Metadaten (Plan B.14)
export const tab = { id: 'history', order: 20, labelKey: 'zoneDetail.tabHistory', icon: History }

export default function HistoryTab({ ctx }) {
    const { t } = useTranslation()
    const {
        server, zoneId, zoneKey, canEdit, searchParams, setSearchParams, setSuccess, setError, setWarning, loadZone,
    } = ctx
    const hname = searchParams.get('hname') || ''
    const htype = searchParams.get('htype') || ''
    const entry = searchParams.get('entry') || ''
    const recordFilter = hname ? { name: hname, type: htype } : null

    // entfernt einzelne Parameter, der Tab bleibt aktiv
    const dropParams = useCallback((...keys) => {
        setSearchParams((prev) => {
            const next = new URLSearchParams(prev)
            for (const k of keys) next.delete(k)
            next.set('tab', 'history')
            return next
        }, { replace: true })
    }, [setSearchParams])

    const onRolledBack = useCallback((res, rolledEntry) => {
        const outcome = rollbackOutcome(res)
        if (outcome.noop) {
            setSuccess(t('history.rollbackNothing'))
        } else {
            setSuccess(t('history.rollbackDone', { id: rolledEntry?.id ?? '?', newId: outcome.revertId ?? '?' }))
        }
        const summary = fanoutSummary(res?.details)
        if (summary.hasErrors) {
            setError(t('history.rollbackPartial', { servers: formatFanoutErrors(summary.errors) }))
        }
        const warnings = []
        if (summary.hasWarnings) warnings.push(t('zoneDetail.fanoutNotLoaded', { servers: formatFanoutWarnings(summary.warnings) }))
        if (outcome.primaryOutcome) warnings.push(t(`history.outcome.${outcome.primaryOutcome}`))
        if (warnings.length) setWarning(warnings.join('\n'))
        // Records still nachladen, damit der Ganzseiten-Spinner das Panel nicht unmountet (F7 §6.4)
        loadZone({ silent: true })
    }, [t, setSuccess, setError, setWarning, loadZone])

    return (
        <ZoneHistoryPanel
            server={server}
            zoneId={zoneId}
            zoneKey={zoneKey}
            recordFilter={recordFilter}
            onClearRecordFilter={() => dropParams('hname', 'htype')}
            focusEntryId={entry}
            onClearFocus={() => dropParams('entry')}
            onRolledBack={onRolledBack}
            canEdit={canEdit}
            t={t}
        />
    )
}
