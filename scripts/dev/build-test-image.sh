#!/usr/bin/env bash
# build-test-image.sh – baut das Backend-Testimage pdnsmgr-test:w<n> fuer eine Welle.
#
# Inhalt: python:3.12-slim + backend/requirements.lock + pytest pytest-asyncio pytest-cov
# (wie CI, .github/workflows/test.yml). Der App-Code ist NICHT im Image: Tests kopieren
# den backend-Ordner zur Laufzeit nach /work (siehe scripts/dev/README.md).
#
# Aufruf:  scripts/dev/build-test-image.sh <wellennummer>     z. B. 0  -> pdnsmgr-test:w0
#          scripts/dev/build-test-image.sh 0b                 (Buchstaben/Ziffern erlaubt)
#
# Der Build laeuft automatisch ueber scripts/dev/with-slot.sh (Kapazitaetsregel). Der Build-
# Kontext ist ein temporaeres Verzeichnis, das NUR requirements.lock enthaelt (.env o. ae.
# kann nicht ins Image gelangen).
#
# Umgebung:
#   PDNSMGR_TEST_BASE    Basisimage (Default python:3.12-slim)
#   PDNSMGR_TEST_EXTRA   zusaetzliche pip-Pakete (Default "pytest pytest-asyncio pytest-cov")
set -euo pipefail

if [ "$#" -ne 1 ] || ! [[ "$1" =~ ^[0-9][0-9a-z]{0,7}$ ]]; then
  echo "Aufruf: $0 <wellennummer>   (z. B. 0, 1, 2 ...)" >&2
  exit 2
fi
WAVE="$1"
TAG="pdnsmgr-test:w${WAVE}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOCK="$REPO_ROOT/backend/requirements.lock"
BASE="${PDNSMGR_TEST_BASE:-python:3.12-slim}"
EXTRA="${PDNSMGR_TEST_EXTRA:-pytest pytest-asyncio pytest-cov}"

[ -f "$LOCK" ] || { echo "Lockfile fehlt: $LOCK" >&2; exit 1; }

CTX="$(mktemp -d "${TMPDIR:-/tmp}/pdnsmgr-test-ctx.XXXXXX")"
trap 'rm -rf "$CTX"' EXIT
cp "$LOCK" "$CTX/requirements.lock"
LOCK_SHA="$(sha256sum "$LOCK" | cut -d' ' -f1)"
GIT_REV="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unbekannt)"

cat > "$CTX/Dockerfile" <<DOCKERFILE
FROM ${BASE}
ENV PYTHONDONTWRITEBYTECODE=1 \\
    PYTHONUNBUFFERED=1 \\
    PIP_DISABLE_PIP_VERSION_CHECK=1 \\
    PIP_ROOT_USER_ACTION=ignore \\
    DB_POOL_SIZE=0
COPY requirements.lock /opt/pdnsmgr/requirements.lock
RUN pip install --no-cache-dir -r /opt/pdnsmgr/requirements.lock \\
 && pip install --no-cache-dir ${EXTRA} \\
 && pip check \\
 && pip freeze > /opt/pdnsmgr/pip-freeze.txt
WORKDIR /work
LABEL org.opencontainers.image.title="pdnsmgr-test" \\
      pdnsmgr.wave="${WAVE}" \\
      pdnsmgr.lock-sha256="${LOCK_SHA}" \\
      pdnsmgr.git-rev="${GIT_REV}"
CMD ["python", "--version"]
DOCKERFILE

echo "[build-test-image] baue $TAG (Basis $BASE, Lock $LOCK_SHA, Stand $GIT_REV)" >&2
DOCKER_BUILDKIT=1 "$SCRIPT_DIR/with-slot.sh" docker build --pull=false -t "$TAG" "$CTX"

echo "[build-test-image] fertig: $TAG" >&2
docker run --rm "$TAG" python -c "import sys, pytest, pytest_asyncio; print('python', sys.version.split()[0], '| pytest', pytest.__version__, '| pytest-asyncio', pytest_asyncio.__version__)"
