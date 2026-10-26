# Reading the gateway console

Open it with `scripts/port-forward.sh` and http://localhost:8081.

**Navigation:**
- **Dashboards:** Overview, Token Monitoring.
- **Configure:** MCPs, Models, Model catalog, Profiles, Skills, Users.
- **Observe:** Access Logs, LLM Logs, Session Timeline.
- **Operations:** Tool Search, Cache.
- **Access:** API Keys, Credentials.

## What one agent run looks like

A run that searches, comments and answers produces these rows in this order:

| # | Agent action | Page |
|---|---|---|
| 1 | `server/discover` → `-32601` (the MCP library tries a newer handshake; harmless) | Access Logs |
| 2 | `initialize` → 200 | Access Logs |
| 3 | `notifications/initialized` → 204 | Access Logs |
| 4 | Blank method → 404 (the library's optional server-push stream; harmless) | Access Logs |
| 5 | `tools/list` → 200 | Access Logs |
| 6 | Model call 1: the model chooses a tool | LLM Logs |
| 7 | `tools/call ticketdesk__search_tickets` | Access Logs |
| 8 | Model call 2 | LLM Logs |
| 9 | `tools/call ticketdesk__add_comment` | Access Logs |
| 10 | Model call 3: the answer | LLM Logs |
| 11 | Blank method → 204 (the agent closes its session) | Access Logs |

## Observe → Access Logs (tool side)

- **Columns:** Timestamp, Method, MCP, Tool, Key, User, Duration, Status, Request and Response size.
- **Tool names:** these appear as `<connector>__<tool>`, so one agent can use several MCP servers without name clashes.
- **What to open:** click a `tools/call` row. It shows:
  - **API key and Profile:** who made the call and which policy allowed it.
  - **Error code:** empty when allowed; `-32003` when the profile refused it.
  - **Session ID:** links to the Session Timeline.
  - **Request body:** what the agent sent. It has no `project`, because pinning happens after this point.
  - **Response body:** what came back.
  - **Headers:** sensitive values are masked. The gateway key itself is never stored.

## Observe → LLM Logs (model side)

- **Columns:** Timestamp, Model, Provider, Key, Client, User, Path, Status, Duration, Tokens in/out, Cache read/write.
  - The **Stop reason** column is hidden by default; turn it on with the column icon.
- **Input tokens grow each call.** Bedrock's Converse API is stateless, so every call resends the whole conversation.
- **Client = "other":** a boto3 script isn't one of the clients the gateway recognises.
- **User = "—":** the call was made with an agent key, not a console login.
- **What to open:** click a row. It shows:
  - **Tools:** exactly what the model was offered. Here, 3 tools with no `project` input.
  - **System and Messages:** the full prompt.
  - **Response body:** the model's reply, including any `toolUse` request.
  - **Upstream host:** `bedrock-runtime.<region>.amazonaws.com`.
  - **Error:** for refused calls, for example AWS's Marketplace or IAM message, or the gateway's own budget or token-cap reason.

## Session Timeline

Open it from a Session ID in Access Logs. It shows one MCP session's events in order.

Model calls appear here only when the agent sends a session ID on them. `poc_agent.py` doesn't, so its model calls are matched to tool calls by key and time instead.

## Dashboards

- **Overview:** agent calls, tool calls, success rate, total cost and tokens, plus the top agents and tools.
- **Token Monitoring:** tokens and estimated cost by key and model. Cost comes from the gateway's price list; a model missing from that list costs $0 there and is therefore not limited by budgets.

## Configure and Access pages

These tie each log row back to the policy:
- **MCPs:** the ticketdesk connector and its health.
- **Profiles:** granted tools per profile.
- **API Keys:** each key's bound profile and limits.
- **Credentials:** `ticketdesk-token`. Its value is never shown.

## Outside the console

- **The tool's view:** `tail ~/poc/ticketdesk.log | jq -c .` shows `token_ok: true` and `project: APPSEC` on each call. The gateway added both; the agent sent neither.
- **AWS's view:** CloudTrail → Event history, event source `bedrock.amazonaws.com`. The caller is always the gateway's role, so CloudTrail can't tell agents apart. The gateway's own logs supply that.
