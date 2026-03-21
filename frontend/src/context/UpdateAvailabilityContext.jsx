import { useCallback, useEffect, useMemo, useState } from 'react'
import api from '../api'
import { compareSemver } from '../utils/semverCompare'
import { fetchLatestRemoteVersion } from '../lib/githubLatestVersion'
import { UpdateAvailabilityContext } from './updateAvailabilityContext'

const STORAGE_DISMISSED = 'dns_manager_dismissed_release_version'
const POLL_MS = 30 * 60 * 1000 // 30 Minuten

export function UpdateAvailabilityProvider({ children }) {
    const [currentVersion, setCurrentVersion] = useState(null)
    const [latestVersion, setLatestVersion] = useState(null)
    const [updateAvailable, setUpdateAvailable] = useState(false)
    const [checkError, setCheckError] = useState(null)

    const runCheck = useCallback(async () => {
        setCheckError(null)
        try {
            const app = await api.getAppInfo()
            const current = app?.app_version ? String(app.app_version).replace(/^v/i, '') : '0'
            setCurrentVersion(current)

            const remote = await fetchLatestRemoteVersion()
            setLatestVersion(remote)

            if (!remote) {
                setUpdateAvailable(false)
                return
            }

            const dismissed = localStorage.getItem(STORAGE_DISMISSED) || ''
            const isNewer = compareSemver(remote, current) > 0
            const shouldShow = isNewer && dismissed !== remote
            setUpdateAvailable(shouldShow)
        } catch (e) {
            setCheckError(e?.message || 'check failed')
            setUpdateAvailable(false)
        }
    }, [])

    const dismissUpdate = useCallback(() => {
        if (latestVersion) {
            localStorage.setItem(STORAGE_DISMISSED, latestVersion)
        }
        setUpdateAvailable(false)
    }, [latestVersion])

    useEffect(() => {
        const id = setInterval(runCheck, POLL_MS)
        return () => clearInterval(id)
    }, [runCheck])

    /* Erster Check nach Mount (außerhalb synchroner setState-in-effect-Regel) */
    useEffect(() => {
        const tid = setTimeout(() => {
            runCheck()
        }, 0)
        return () => clearTimeout(tid)
    }, [runCheck])

    const value = useMemo(
        () => ({
            updateAvailable,
            latestVersion,
            currentVersion,
            dismissUpdate,
            recheck: runCheck,
            checkError,
        }),
        [updateAvailable, latestVersion, currentVersion, dismissUpdate, runCheck, checkError],
    )

    return <UpdateAvailabilityContext.Provider value={value}>{children}</UpdateAvailabilityContext.Provider>
}
