#!/bin/sh
# AC-22 egress-blocked run (TRD 07 §12-5, TRD 08 §5). Linux, root (or user namespaces).
#
# Runs the whole browser E2E (backend + worker + Vite + Chromium) inside a new network namespace that has its own
# loopback and one private "LAN" interface (veth, 10.77.0.10/24) with no route anywhere else, like an isolated
# intranet machine (Chromium needs a non-loopback interface for ICE host candidates). Any attempt to reach
# another host fails, and every attempt is also recorded —
#   backend/worker: Python audit hook (name lookups, connects)  -> $OUT/backend-egress.log
#   browser:        Chromium net-log                            -> $OUT/chromium-netlog.json
# e2e/global-teardown.ts fails the run if either shows a host other than loopback, and the offline spec checks
# ICE gathering with iceServers=[] finishes in under a second.
#
# PostgreSQL stays outside and is reached over its Unix socket (/tmp/.s.PGSQL.55432).
# Needs iproute2 (`ip`). Usage: tools/offline/run.sh   (uses .venv/bin/python unless E2E_PYTHON is set)
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
OUT=${OUT:-$ROOT/frontend/e2e/.fixtures/offline}  # not test-results/: Playwright empties it at start
mkdir -p "$OUT"
rm -f "$OUT/backend-egress.log" "$OUT/chromium-netlog.json"
command -v ip >/dev/null || { echo "iproute2 (ip) is required" >&2; exit 2; }
[ -S /tmp/.s.PGSQL.55432 ] || { echo "PostgreSQL Unix socket /tmp/.s.PGSQL.55432 not found" >&2; exit 2; }

exec unshare -n -- sh -c "
  ip link set lo up &&
  ip link add lan0 type veth peer name lan1 && ip addr add 10.77.0.10/24 dev lan0 &&
  ip link set lan0 up && ip link set lan1 up &&
  echo 'network: loopback + 10.77.0.10/24, no default route' &&
  cd '$ROOT/frontend' &&
  E2E_OFFLINE=1 E2E_PG_HOST=/tmp E2E_EGRESS_LOG='$OUT/backend-egress.log' E2E_NETLOG='$OUT/chromium-netlog.json' \
  E2E_PYTHON=\${E2E_PYTHON:-'$ROOT/.venv/bin/python'} \
  npx playwright test --project=chromium --reporter=line
"
