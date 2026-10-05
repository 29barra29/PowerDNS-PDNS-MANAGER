// Fixture: definiert newX und ueberschreibt getA ohne Deklaration
export default {
    newX() { return this.request('GET', '/x') },
    getA() { return this.request('GET', '/a2') },
}
