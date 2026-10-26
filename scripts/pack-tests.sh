#!/usr/bin/env bash
# pack-tests.sh - builds dist/poc-tests.tgz, the governance test kit for the VM.
# On the VM it unpacks to ~/poc/poc-tests (see docs/TESTS.md).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p dist
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir "$tmp/poc-tests"
cp tests/run_tests.py tests/probe_mcp.py tests/redirect_server.py tests/README.txt "$tmp/poc-tests/"
tar -czf dist/poc-tests.tgz -C "$tmp" poc-tests
echo "Built dist/poc-tests.tgz ($(wc -c < dist/poc-tests.tgz) bytes)"
