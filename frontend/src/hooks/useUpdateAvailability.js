import { useContext } from 'react'
import { UpdateAvailabilityContext } from '../context/updateAvailabilityContext'

export function useUpdateAvailability() {
    const ctx = useContext(UpdateAvailabilityContext)
    if (!ctx) {
        throw new Error('useUpdateAvailability must be used within UpdateAvailabilityProvider')
    }
    return ctx
}

export function useUpdateAvailabilitySafe() {
    return useContext(UpdateAvailabilityContext)
}
