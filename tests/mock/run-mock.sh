#!/usr/bin/env bash
# run-mock.sh - runs the test suite against the mock gateway, on any Linux or macOS machine.
# For developing run_tests.py without AWS. Results prove nothing about the real gateway.
#
# Usage:  tests/mock/run-mock.sh [run_tests.py options, e.g. --only 4,17]
# Needs:  python3; creates a venv in tests/mock/.venv with mcp, uvicorn (first run only)
set -euo pipefail
cd "$(dirname "$0")"
MOCK=$PWD
REPO=$(cd ../.. && pwd)
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
  .venv/bin/pip install --quiet "mcp>=2,<3" uvicorn
fi
HOME_DIR=$(mktemp -d)
POC=$HOME_DIR/poc
mkdir -p "$POC/.venv/bin"
ln -s "$MOCK/.venv/bin/python" "$POC/.venv/bin/python"
cp "$REPO/kit/ticketdesk_mcp.py" "$POC/"
echo "TICKETDESK_TOKEN=mock$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')" > "$POC/ticketdesk.env"
cat > "$POC/poc_agent.py" <<'PY'
import sys
print("[stage 1] (mock agent: no model is called)")
print("\nAnswer: mock agent reply to:", sys.argv[1][:60])
PY
printf '#!/bin/sh\necho "mock gateway: no container log"\n' > "$POC/gw"; chmod +x "$POC/gw"

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT
set -a
# shellcheck disable=SC1091
source "$POC/ticketdesk.env"
set +a
TICKETDESK_LOG=$POC/ticketdesk.log .venv/bin/python -I "$POC/ticketdesk_mcp.py" > "$HOME_DIR/ticketdesk.out" 2>&1 & pids+=($!)
ADMIN=gk_mockadminkey00000000 TD_LOG=$POC/ticketdesk.log KEYS_OUT=$POC/keys.env \
  .venv/bin/python -I mockgw.py > "$HOME_DIR/mockgw.out" 2>&1 & pids+=($!)
for _ in $(seq 1 60); do
  [[ -s $POC/keys.env ]] && curl -s localhost:8081/api/v1/health >/dev/null \
    && [[ $(curl -s -o /dev/null -w '%{http_code}' localhost:9000/mcp) != 000 ]] && break
  sleep 0.5
done

HOME=$HOME_DIR ADMIN=gk_mockadminkey00000000 .venv/bin/python -I "$REPO/tests/run_tests.py" "$@"
echo "Mock run files: $HOME_DIR"
