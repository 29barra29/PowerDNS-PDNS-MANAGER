import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { useParams, useNavigate, useSearchParams } from 'react-router-dom'
import { ArrowLeft, AlertCircle, AlertTriangle, CheckCircle } from 'lucide-react'
import PageSpinner from '../components/PageSpinner'
import { slotApplies } from '../lib/slots.js'
import ZoneDetailTabs from '../zoneDetail/ZoneDetailTabs'
import RecordFormModal from '../zoneDetail/RecordFormModal'
import { ZoneDnssecHost } from '../zoneDetail/ZoneDnssecSection'
import useZoneData from '../zoneDetail/useZoneData'
import { ZoneDetailContext } from '../zoneDetail/zoneDetailContext'
import { DEFAULT_TAB_ID, ZONE_DETAIL_SLOTS } from '../zoneDetail/ZoneDetailSlots'

// Zonenansicht - Shell (Plan B.14): Laden, Kopf, Banner, Tab-Leiste, Modals. Daten und Handler kommen aus
// useZoneData (zoneDetail/), Inhalte aus den Slot-Verzeichnissen (zoneDetail/ZoneDetailSlots.js).
// Features docken ueber neue Slot-Dateien an, nicht ueber Aenderungen an dieser Datei.
export default function ZoneDetailPage() {
    const { server, zoneId } = useParams()
    // Zonen- bzw. Serverwechsel ("Wechsel zu <Server>") startet mit frischem Zustand
    return <ZoneDetailView key={`${server}\n${zoneId}`} server={server} zoneId={zoneId} />
}

function ZoneDetailView({ server, zoneId }) {
    const { t } = useTranslation()
    const navigate = useNavigate()
    const [searchParams, setSearchParams] = useSearchParams()
    const data = useZoneData(server, zoneId)

    // Tabs: ?tab=<id>, Standard-Tab ohne Parameter (F12 §6.3); unbekannte Werte fallen auf den Standard zurueck.
    const tabs = ZONE_DETAIL_SLOTS.tabs.filter((tab) => slotApplies(tab, [data]))
    const defaultTab = tabs.some((tab) => tab.id === DEFAULT_TAB_ID) ? DEFAULT_TAB_ID : (tabs[0]?.id || null)
    const requestedTab = searchParams.get('tab')
    const activeTab = tabs.some((tab) => tab.id === requestedTab) ? requestedTab : defaultTab
    const setTab = useCallback((id, params = {}, { replace = true } = {}) => {
        setSearchParams(id === defaultTab ? { ...params } : { tab: id, ...params }, { replace })
    }, [defaultTab, setSearchParams])

    const ctx = {
        ...data,
        tabs, activeTab, setTab, searchParams, setSearchParams,
        slots: ZONE_DETAIL_SLOTS,
    }

    return (
        <ZoneDetailContext.Provider value={ctx}>
            <ZoneDnssecHost />
            {data.loading ? <PageSpinner /> : <ZoneDetailBody ctx={ctx} t={t} navigate={navigate} />}
        </ZoneDetailContext.Provider>
    )
}

function ZoneDetailBody({ ctx, t, navigate }) {
    const {
        server, zoneId, zoneName, records, error, setError, success, setSuccess, warning, setWarning,
        userCanEdit, serverCanWrite, otherWritableServers, tabs, activeTab, setTab, recordForm, slots,
    } = ctx
    const headerActions = slots.headerActions.filter((entry) => slotApplies(entry, [ctx]))
    const activeEntry = tabs.find((tab) => tab.id === activeTab) || null
    const ActiveTab = activeEntry?.Component || null
    const showTabBar = tabs.length > 1

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex items-center gap-3 min-w-0">
                    <button onClick={() => navigate('/zones')} className="p-2 rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-primary transition-colors shrink-0">
                        <ArrowLeft className="w-5 h-5" />
                    </button>
                    <div className="min-w-0">
                        <h1 className="text-2xl font-bold text-text-primary break-all">{zoneName}</h1>
                        <p className="text-text-muted text-sm">{t('zoneDetail.recordsCount', { server, count: records.length })}</p>
                    </div>
                </div>
                <div className="flex flex-col sm:flex-row flex-wrap items-stretch sm:items-center gap-2 w-full sm:w-auto">
                    {headerActions.map((entry) => {
                        const HeaderAction = entry.Component
                        return <HeaderAction key={entry.id} ctx={ctx} />
                    })}
                </div>
            </div>

            {error && (
                <div className="p-4 rounded-xl bg-danger/10 border border-danger/30 text-danger flex items-center gap-3">
                    <AlertCircle className="w-5 h-5 shrink-0" />
                    <p className="text-sm">{error}</p>
                    <button onClick={() => setError('')} className="ml-auto text-xs hover:underline">×</button>
                </div>
            )}

            {success && (
                <div className="p-4 rounded-xl bg-success/10 border border-success/30 text-success flex items-center gap-3">
                    <CheckCircle className="w-5 h-5 shrink-0" />
                    <p className="text-sm flex-1">{success}</p>
                    <button onClick={() => setSuccess('')} className="text-xs hover:underline" aria-label={t('common.close')}>×</button>
                </div>
            )}

            {/* Warnungen (z. B. nicht geladene Server im Fan-out [D4]); bleiben stehen, bis sie geschlossen werden */}
            {warning && (
                <div className="p-4 rounded-xl bg-warning/10 border border-warning/30 text-warning flex items-start gap-3" role="status">
                    <AlertTriangle className="w-5 h-5 shrink-0 mt-0.5" />
                    <p className="text-sm flex-1 whitespace-pre-line">{warning}</p>
                    <button onClick={() => setWarning('')} className="text-xs hover:underline" aria-label={t('common.close')}>×</button>
                </div>
            )}

            {!userCanEdit && (
                <div className="p-4 rounded-xl bg-bg-secondary/80 border border-border text-text-secondary text-sm">
                    {t('zoneDetail.readOnlyZone')}
                </div>
            )}

            {userCanEdit && !serverCanWrite && (
                <div className="p-4 rounded-xl bg-warning/10 border border-warning/30 text-warning flex items-start gap-3">
                    <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
                    <div className="text-sm">
                        <div className="font-medium">
                            {t('zoneDetail.serverReadOnlyTitle', { server })}
                        </div>
                        <div className="mt-1 text-text-secondary">
                            {t('zoneDetail.serverReadOnlyBody')}
                        </div>
                        {otherWritableServers.length > 0 && (
                            <div className="mt-2 flex flex-wrap gap-1.5">
                                {otherWritableServers.map((s) => (
                                    <button
                                        key={s.name}
                                        type="button"
                                        onClick={() => navigate(`/zones/${encodeURIComponent(s.name)}/${encodeURIComponent(zoneId)}`)}
                                        className="text-xs px-2 py-0.5 rounded-full bg-success/10 border border-success/30 text-success hover:bg-success/20 transition-colors"
                                    >
                                        {t('zoneDetail.switchToServer', { server: s.name, defaultValue: 'Wechsel zu {{server}}' })}
                                    </button>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            )}

            {userCanEdit && serverCanWrite && otherWritableServers.length > 0 && (
                <div className="p-3 rounded-xl bg-bg-secondary/60 border border-border/60 text-text-secondary text-xs flex items-center gap-2">
                    <CheckCircle className="w-4 h-4 text-success shrink-0" />
                    <span>
                        {t('zoneDetail.fanoutInfo', {
                            primary: server,
                            peers: otherWritableServers.map((s) => s.name).join(', '),
                        })}
                    </span>
                </div>
            )}

            {/* Tab-Leiste erst ab zwei Tabs (mit nur "Records" sieht die Seite aus wie 2.4.1) */}
            {showTabBar && <ZoneDetailTabs tabs={tabs} active={activeTab} onChange={(id) => setTab(id)} t={t} />}

            {ActiveTab && (
                <div
                    role={showTabBar ? 'tabpanel' : undefined}
                    aria-labelledby={showTabBar ? `zone-tab-${activeTab}` : undefined}
                >
                    <ActiveTab ctx={ctx} />
                </div>
            )}

            {/* Add / Edit Record Modal (bei jedem Oeffnen frisch gemountet) */}
            {recordForm && <RecordFormModal key={recordForm.seq} request={recordForm} />}
        </div>
    )
}
