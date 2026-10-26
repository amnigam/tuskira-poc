#!/usr/bin/env bash
# setup_policy.sh - Stage 0 of the POC: write the governance policy into the gateway.
# Run once, as the ubuntu user, after bootstrap:   ADMIN=gk_... ./setup_policy.sh
#
# What it creates (all through the gateway's REST API on :8081):
#   1. an encrypted credential holding the ticketdesk token (agents never see it)
#   2. the "ticketdesk" connector: token injected as a header, project pinned to
#      APPSEC on every tool, server-initiated requests off
#   3. tool discovery (the gateway asks ticketdesk which tools it has)
#   4. two profiles: appsec-helpdesk (read + comment) and appsec-readonly (read)
#   5. two agent keys, each bound to one profile, each with model limits
# The two agent keys are written to keys.env (readable only by you).
set -euo pipefail
cd "$(dirname "$0")"

API=http://localhost:8081/api/v1
: "${ADMIN:?first run: export ADMIN=<the admin key printed by bootstrap-key>}"
if [[ -f keys.env ]]; then
  echo "keys.env already exists: policy was set up before. Delete it (and the objects in the console) to redo." >&2
  exit 1
fi
# shellcheck disable=SC1091
source ./ticketdesk.env   # provides TICKETDESK_TOKEN, generated at bootstrap

api() { # api METHOD PATH [curl args...]
  local method=$1 path=$2; shift 2
  curl -sS --fail-with-body -X "$method" "$API$path" \
    -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" "$@"
}

echo "1/5 Storing the ticketdesk token in the gateway's encrypted credential store"
api POST /credentials \
  -d "$(jq -n --arg t "$TICKETDESK_TOKEN" '{name: "ticketdesk-token", type: "pat", payload: {token: $t}}')" \
  | jq -c '{name}'

echo "2/5 Registering the ticketdesk MCP server"
CID=$(api POST /connectors -d '{
  "name": "ticketdesk",
  "endpoint": "http://host.docker.internal:9000/mcp",
  "timeout_ms": 15000,
  "metadata": {
    "description": "Dummy ticketing system for the POC",
    "headers": {
      "Authorization": {"type": "external", "provider": "secret_store", "prefix": "Bearer ",
                        "config": {"credential": "ticketdesk-token", "field": "token"}}
    },
    "tool_arg_overrides": {"project": "APPSEC"},
    "server_requests": {"sampling": false, "elicitation": false, "roots": false}
  }}' | jq -r .id)
echo "    connector id: $CID"

echo "3/5 Discovering its tools (the gateway calls ticketdesk itself)"
api POST "/connectors/$CID/discover" | jq -c '[.items[].tool_name]'

make_profile() { # make_profile NAME TOOL...
  local name=$1; shift
  local pid tools
  pid=$(api POST /profiles -d "$(jq -n --arg n "$name" '{name: $n}')" | jq -r .id)
  tools=$(printf '%s\n' "$@" | jq -R --arg c "$CID" '{connector_id: $c, tool_name: .}' | jq -sc '{tools: .}')
  api PUT "/profiles/$pid/tools" -d "$tools" >/dev/null
  echo "$pid"
}
echo "4/5 Creating profiles"
P_HELPDESK=$(make_profile appsec-helpdesk search_tickets get_ticket add_comment)
P_READONLY=$(make_profile appsec-readonly search_tickets get_ticket)
echo "    appsec-helpdesk: search_tickets, get_ticket, add_comment"
echo "    appsec-readonly: search_tickets, get_ticket"
echo "    (delete_ticket and export_customers are granted to nobody)"

make_key() { # make_key NAME PROFILE_ID LIMITS_JSON
  api POST /api-keys -d "$(jq -n --arg n "$1" --arg p "$2" --argjson l "$3" \
    '{name: $n, role: "agent", profile_id: $p, limits: $l}')"
}
echo "5/5 Minting agent keys (bound to a profile, with model limits)"
K1=$(make_key helpdesk-bot "$P_HELPDESK" '{"daily_usd": 1, "rpm": 20, "max_tokens": 2048}')
K2=$(make_key readonly-bot "$P_READONLY" '{"daily_usd": 0.5, "rpm": 10, "max_tokens": 1024}')
umask 077
cat > keys.env <<KEYS
# Agent keys created by setup_policy.sh. Treat like passwords.
HELPDESK_KEY=$(jq -r .key <<<"$K1")
HELPDESK_KEY_ID=$(jq -r .id <<<"$K1")
READONLY_KEY=$(jq -r .key <<<"$K2")
READONLY_KEY_ID=$(jq -r .id <<<"$K2")
KEYS
echo "    helpdesk-bot: daily \$1, 20 req/min, max_tokens 2048"
echo "    readonly-bot: daily \$0.50, 10 req/min, max_tokens 1024"
echo "Done. Keys saved to $(pwd)/keys.env"
