// Fixture: definiert newX ein zweites Mal
const methods = {
    async newX(params = {}) {
        const { a } = params
        return this.request('GET', `/x/${a}`)
    },
    'quoted-name': () => null,
}
export default methods
