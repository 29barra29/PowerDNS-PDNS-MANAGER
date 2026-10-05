#!/usr/bin/env bash
# static-checks.sh – statische Pruefungen des Bauplans 3.0 (grep/AST, keine Container, keine DB).
#
# Aufruf (aus beliebigem Verzeichnis):
#   scripts/e2e/static-checks.sh [--legacy-ok] [--wave-end] [--base <git-ref>] [--only regel1,regel2]
#
#   --legacy-ok  Regeln, die erst ab Welle 0b erfuellt sein koennen (2.4.1-Altlasten), melden WARN
#                statt FAIL. Nur fuer Staende vor dem Ende von Welle 0b gedacht.
#   --wave-end   zusaetzlich Wellenende-Regeln (Fragment-Verzeichnis leer, nach dem Locale-Merge)
#   --base REF   Startpunkt fuer die Commit-Regel "locales-json" (Default: merge-base mit main)
#   --only LISTE nur diese Regeln
#
# Regeln (Phase "0b" = ab Wellenende 0b Pflicht, vorher mit --legacy-ok nur WARN):
#   shell-syntax           bash -n fuer alle *.sh des Repos (+ shellcheck -S error, falls installiert)
#   e2e-checks             scripts/e2e/checks/*.py kompilieren und definieren check_fresh/check_upgrade
#   webhook-legacy   (0b)  kein deliver_webhooks_background in backend/app (B.8)
#   role-admin       (0b)  kein Inline-`.role == "admin"`/`!= "admin"` ausserhalb core/auth.py (Regel 6);
#                          (SQL-Ausdruecke wie `User.role == "admin"` zaehlen nicht); begruendete
#                          Ausnahme: Kommentar "static-ok: role-admin" in derselben Zeile
#   audit-raw        (0b)  kein rohes `AuditLog(` ausserhalb services/audit.py und models (B.7)
#   router-order     (0b)  jedes Modul in backend/app/routers hat ROUTER_ORDER oder einen
#                          LEGACY_ROUTER_ORDER-Eintrag in main.py, keine doppelten Ordnungen (B.13)
#   route-policy     (0b)  backend/tests/route_policy/ + test_route_policy.py vorhanden, Slot-Dateien kompilieren
#   dbwrite-test     (0b)  backend/tests/test_db_dependency_scope.py vorhanden (Pruefung selbst: pytest)
#   locales-json           kein Commit seit --base aendert frontend/src/locales/<lang>.json, ausser
#                          Merge-Commits der Wellenintegration (Betreff beginnt mit "locales:")
#   fragments-empty  (--wave-end) frontend/src/locales/fragments/ enthaelt keine *.json mehr
#
# Exit-Code: 0 = kein FAIL, 1 = mindestens ein FAIL, 2 = Aufruffehler.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LEGACY_OK=0 WAVE_END=0 BASE="" ONLY=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --legacy-ok) LEGACY_OK=1 ;;
    --wave-end) WAVE_END=1 ;;
    --base) shift; BASE="${1:-}" ;;
    --base=*) BASE="${1#--base=}" ;;
    --only) shift; ONLY="${1:-}" ;;
    --only=*) ONLY="${1#--only=}" ;;
    -h|--help) sed -n '2,33p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unbekanntes Argument: $1" >&2; exit 2 ;;
  esac
  shift
done

APP="$ROOT/backend/app"
FAILS=0 WARNS=0
declare -a SUMMARY=()

want() { [ -z "$ONLY" ] || [[ ",$ONLY," == *",$1,"* ]]; }

# result <regel> <phase: always|0b> <PASS|FAIL|SKIP> <text> [details]
result() {
  local rule="$1" phase="$2" status="$3" text="$4" details="${5:-}"
  if [ "$status" = "FAIL" ] && [ "$phase" = "0b" ] && [ "$LEGACY_OK" -eq 1 ]; then
    status="WARN"
  fi
  case "$status" in
    FAIL) FAILS=$((FAILS + 1)) ;;
    WARN) WARNS=$((WARNS + 1)) ;;
  esac
  printf '%-5s %-15s %s\n' "$status" "$rule" "$text"
  if [ -n "$details" ] && [ "$status" != "PASS" ]; then
    printf '%s\n' "$details" | head -n 40 | sed 's/^/        /'
  fi
  SUMMARY+=("$status $rule")
}

rel() { sed "s#^$ROOT/##"; }

# --- shell-syntax ------------------------------------------------------------------------------
if want shell-syntax; then
  out=""
  while IFS= read -r f; do
    msg="$(bash -n "$f" 2>&1)" || out+="$(printf '%s: %s' "${f#"$ROOT"/}" "$msg")"$'\n'
  done < <(find "$ROOT" -name '*.sh' -not -path '*/node_modules/*' -not -path '*/.git/*' -type f | sort)
  if command -v shellcheck >/dev/null 2>&1; then
    sc="$(cd "$ROOT" && find . -name '*.sh' -not -path '*/node_modules/*' -not -path './.git/*' -type f -print0 \
          | xargs -0 shellcheck -S error -x 2>&1)" || out+="$sc"$'\n'
    note="bash -n + shellcheck -S error"
  else
    note="bash -n (shellcheck nicht installiert)"
  fi
  if [ -z "$out" ]; then result shell-syntax always PASS "$note"; else result shell-syntax always FAIL "$note" "$out"; fi
fi

# --- e2e-checks --------------------------------------------------------------------------------
if want e2e-checks; then
  out="$(python3 - "$ROOT/scripts/e2e/checks" <<'PY'
import ast, pathlib, sys
d = pathlib.Path(sys.argv[1])
bad = []
for p in sorted(d.glob("*.py")):
    try:
        tree = ast.parse(p.read_text(encoding="utf-8"), str(p))
    except SyntaxError as e:
        bad.append(f"{p.name}: Syntaxfehler {e}")
        continue
    if p.name.startswith("_"):
        continue
    funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    if not funcs & {"check_fresh", "check_upgrade", "check_upgrade_restart"}:
        bad.append(f"{p.name}: weder check_fresh noch check_upgrade definiert")
print("\n".join(bad))
PY
)"
  if [ -z "$out" ]; then result e2e-checks always PASS "Check-Module ok"; else result e2e-checks always FAIL "Check-Module fehlerhaft" "$out"; fi
fi

# --- webhook-legacy ----------------------------------------------------------------------------
if want webhook-legacy; then
  out="$(grep -rn --include='*.py' 'deliver_webhooks_background' "$APP" | rel)"
  if [ -z "$out" ]; then result webhook-legacy 0b PASS "kein deliver_webhooks_background"
  else result webhook-legacy 0b FAIL "deliver_webhooks_background noch vorhanden ($(wc -l <<<"$out") Stellen)" "$out"; fi
fi

# --- role-admin --------------------------------------------------------------------------------
if want role-admin; then
  out="$(grep -rnE --include='*.py' "(^|[^A-Za-z0-9_])[a-z_][A-Za-z0-9_]*\.role[[:space:]]*(==|!=)[[:space:]]*['\"]admin['\"]" "$APP" \
        | grep -v "^$APP/core/auth.py:" | grep -v 'static-ok: role-admin' | rel)"
  if [ -z "$out" ]; then result role-admin 0b PASS "keine Inline-Rollenpruefung"
  else result role-admin 0b FAIL "Inline-Rollenpruefung statt is_effective_admin ($(wc -l <<<"$out") Stellen)" "$out"; fi
fi

# --- audit-raw ---------------------------------------------------------------------------------
if want audit-raw; then
  out="$(grep -rnE --include='*.py' '(^|[^A-Za-z0-9_])AuditLog\(' "$APP" \
        | grep -v "^$APP/services/audit.py:" | grep -vE "^$APP/models/[^:]*:[0-9]+:class AuditLog\(" | rel)"
  if [ -z "$out" ]; then result audit-raw 0b PASS "kein rohes AuditLog("
  else result audit-raw 0b FAIL "rohe AuditLog(-Konstruktion statt write_audit ($(wc -l <<<"$out") Stellen)" "$out"; fi
fi

# --- router-order ------------------------------------------------------------------------------
if want router-order; then
  out="$(python3 - "$APP" <<'PY'
import ast, pathlib, sys
app = pathlib.Path(sys.argv[1])
main = ast.parse((app / "main.py").read_text(encoding="utf-8"))
legacy = None
for node in main.body:
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(t, ast.Name) and t.id == "LEGACY_ROUTER_ORDER" for t in targets):
            legacy = ast.literal_eval(node.value)
if legacy is None:
    print("MISSING")
    sys.exit(0)
problems, orders = [], {}
mods = sorted(p.stem for p in (app / "routers").glob("*.py") if p.stem != "__init__")
for m in mods:
    tree = ast.parse((app / "routers" / f"{m}.py").read_text(encoding="utf-8"))
    own = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ROUTER_ORDER" for t in node.targets):
            try:
                own = ast.literal_eval(node.value)
            except ValueError:
                problems.append(f"{m}: ROUTER_ORDER ist kein Literal")
    if own is not None and m in legacy:
        problems.append(f"{m}: ROUTER_ORDER und LEGACY-Eintrag gleichzeitig")
    order = own if own is not None else legacy.get(m)
    if order is None:
        problems.append(f"{m}: weder ROUTER_ORDER noch LEGACY_ROUTER_ORDER-Eintrag")
        continue
    if not isinstance(order, int):
        problems.append(f"{m}: Ordnung {order!r} ist keine Ganzzahl")
    orders.setdefault(order, []).append(m)
for k in sorted(set(legacy) - set(mods)):
    problems.append(f"LEGACY_ROUTER_ORDER[{k!r}] ohne Modul")
for order, ms in sorted(orders.items(), key=lambda kv: str(kv[0])):
    if len(ms) > 1:
        problems.append(f"Ordnung {order} doppelt: {', '.join(ms)}")
print("\n".join(problems))
PY
)"
  if [ "$out" = "MISSING" ]; then result router-order 0b FAIL "LEGACY_ROUTER_ORDER fehlt in backend/app/main.py (Discovery kommt mit W0-INT-BE2b)"
  elif [ -z "$out" ]; then result router-order 0b PASS "alle Router-Module haben eine eindeutige Ordnung"
  else result router-order 0b FAIL "Router-Ordnung unvollstaendig" "$out"; fi
fi

# --- route-policy ------------------------------------------------------------------------------
if want route-policy; then
  T="$ROOT/backend/tests"
  missing=""
  for f in route_policy/__init__.py route_policy/base.py test_route_policy.py; do
    [ -f "$T/$f" ] || missing+="backend/tests/$f fehlt"$'\n'
  done
  if [ -z "$missing" ]; then
    comp="$(python3 -m py_compile "$T"/route_policy/*.py 2>&1)" || missing+="$comp"$'\n'
  fi
  if [ -z "$missing" ]; then result route-policy 0b PASS "Route-Policy-Slots vorhanden (Vollstaendigkeit prueft pytest tests/test_route_policy.py)"
  else result route-policy 0b FAIL "Route-Policy unvollstaendig" "$missing"; fi
fi

# --- dbwrite-test ------------------------------------------------------------------------------
if want dbwrite-test; then
  if [ -f "$ROOT/backend/tests/test_db_dependency_scope.py" ]; then
    result dbwrite-test 0b PASS "test_db_dependency_scope.py vorhanden (Pruefung selbst: pytest)"
  else
    result dbwrite-test 0b FAIL "backend/tests/test_db_dependency_scope.py fehlt (kommt mit W0-INT-BE2b)"
  fi
fi

# --- locales-json ------------------------------------------------------------------------------
if want locales-json; then
  if ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    result locales-json always SKIP "kein Git-Checkout"
  else
    if [ -z "$BASE" ]; then
      BASE="$(git -C "$ROOT" merge-base HEAD main 2>/dev/null || git -C "$ROOT" merge-base HEAD origin/main 2>/dev/null || true)"
    fi
    if [ -z "$BASE" ]; then
      result locales-json always SKIP "kein Startpunkt (main/origin/main fehlt; --base angeben)"
    else
      out="$(git -C "$ROOT" log --no-merges --format='@@%h %s' --name-only "$BASE..HEAD" -- frontend/src/locales \
        | awk '/^@@/{c=substr($0,3); next}
               /^frontend\/src\/locales\/[^\/]+\.json$/ { split(c, a, " "); subj=substr(c, length(a[1])+2);
                 if (subj !~ /^locales:/) print c " -> " $0 }')"
      if [ -z "$out" ]; then result locales-json always PASS "keine direkten Aenderungen an locales/<lang>.json seit $(git -C "$ROOT" rev-parse --short "$BASE")"
      else result locales-json always FAIL "locales/<lang>.json ausserhalb eines 'locales:'-Merge-Commits geaendert" "$out"; fi
    fi
  fi
fi

# --- fragments-empty ---------------------------------------------------------------------------
if [ "$WAVE_END" -eq 1 ] && want fragments-empty; then
  out="$(find "$ROOT/frontend/src/locales/fragments" -maxdepth 1 -name '*.json' 2>/dev/null | rel | sort)"
  if [ -z "$out" ]; then result fragments-empty always PASS "Fragment-Verzeichnis leer"
  else result fragments-empty always FAIL "nicht gemergte Locale-Fragmente" "$out"; fi
fi

echo "---"
echo "static-checks: ${#SUMMARY[@]} Regeln, $FAILS FAIL, $WARNS WARN$([ "$LEGACY_OK" -eq 1 ] && echo ' (--legacy-ok)')"
[ "$FAILS" -eq 0 ]
