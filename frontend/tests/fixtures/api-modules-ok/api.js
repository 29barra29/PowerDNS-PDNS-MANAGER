// Mini-Kern fuer api-modules-Fixtures
class APIClient {
    constructor() {
        this.x = 1
    }

    async request(method, path, data = null, { signal } = {}) {
        if (signal) return null
        return { method, path, data }
    }

    getA() { return this.request('GET', '/a') }
    getB(id, { limit = 10 } = {}) { return this.request('GET', `/b/${id}?limit=${limit}`) }
    getAuditLog(limit = 100) { return this.request('GET', `/audit-log?limit=${limit}`) }
}

const api = new APIClient()
export default api
