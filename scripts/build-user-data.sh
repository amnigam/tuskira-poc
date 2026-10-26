#!/usr/bin/env bash
# build-user-data.sh - builds dist/poc-user-data.sh, the EC2 user data that sets up the VM.
#
# It takes kit/bootstrap.tmpl.sh and embeds the files in kit/ (dummy MCP server, agent,
# policy script, compose overlay) as a base64 tarball, so the VM needs nothing else from
# this repo. Run it again after changing anything in kit/.
#
# Usage: scripts/build-user-data.sh        (from anywhere; needs bash, tar, base64, awk)
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p dist
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

tar -czf "$tmp/kit.tgz" -C kit ticketdesk_mcp.py poc_agent.py setup_policy.sh docker-compose.poc.yml
base64 < "$tmp/kit.tgz" > "$tmp/kit.b64"
awk -v f="$tmp/kit.b64" '$0 == "__KIT_B64__" { while ((getline line < f) > 0) print line; next } { print }' \
  kit/bootstrap.tmpl.sh > dist/poc-user-data.sh

size=$(wc -c < dist/poc-user-data.sh)
if (( size > 16384 )); then
  echo "dist/poc-user-data.sh is $size bytes; EC2 user data is limited to 16384." >&2
  exit 1
fi
echo "Built dist/poc-user-data.sh ($size bytes, limit 16384)"
