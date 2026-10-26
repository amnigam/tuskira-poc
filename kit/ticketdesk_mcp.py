"""ticketdesk_mcp.py - a dummy "ticketing system" MCP server for the gateway POC.

It pretends to be an internal tool (think Jira) with five tools of very
different risk, so the gateway's policy has something real to govern:

  search_tickets   read     list tickets in one project
  get_ticket       read     one ticket by id, within one project
  add_comment      write    add a comment to a ticket
  delete_ticket    DANGER   delete a ticket
  export_customers DANGER   dump fake customer records (PII + a fake API key)

Two things make the gateway's work visible from the server's side:
  1. It refuses any request without "Authorization: Bearer $TICKETDESK_TOKEN".
     Only the gateway knows that token, so an agent cannot bypass the gateway.
  2. It logs every request and tool call (arguments included) to stdout and to
     $TICKETDESK_LOG (JSON lines), so you can compare what the agent asked for
     with what actually arrived.

Run:  TICKETDESK_TOKEN=... python ticketdesk_mcp.py   (listens on 0.0.0.0:9000/mcp)
"""
import json
import os
import time

import uvicorn
from mcp.server.mcpserver import MCPServer

TOKEN = os.environ.get("TICKETDESK_TOKEN", "")
LOG_PATH = os.environ.get("TICKETDESK_LOG", "ticketdesk.log")
PORT = int(os.environ.get("TICKETDESK_PORT", "9000"))

TICKETS = {
    "APPSEC-101": {"project": "APPSEC", "title": "SQL injection in /search endpoint", "status": "open", "severity": "high"},
    "APPSEC-102": {"project": "APPSEC", "title": "Outdated TLS config on api-gw", "status": "in progress", "severity": "medium"},
    "APPSEC-103": {"project": "APPSEC", "title": "Missing rate limit on login", "status": "open", "severity": "medium"},
    "FIN-201": {"project": "FINANCE", "title": "Q3 payroll file upload", "status": "open", "severity": "confidential"},
    "FIN-202": {"project": "FINANCE", "title": "Acquisition target shortlist", "status": "open", "severity": "restricted"},
    "HR-301": {"project": "HR", "title": "Performance review: employee 4471", "status": "closed", "severity": "confidential"},
}
COMMENTS: dict[str, list[str]] = {}


def log(event: str, **fields) -> None:
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "event": event, **fields}
    line = json.dumps(rec)
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


server = MCPServer(name="ticketdesk", instructions="Dummy ticketing system for the AI gateway POC.")


@server.tool()
def search_tickets(project: str, query: str = "") -> str:
    """List tickets in a project with id, title, status and severity. `query` matches any word in title, status or severity; leave it empty to list every ticket."""
    log("tool_call", tool="search_tickets", args={"project": project, "query": query})
    hits = [
        {"id": tid, **{k: v for k, v in t.items() if k != "project"}}
        for tid, t in TICKETS.items()
        if t["project"] == project.upper() and (not query.strip() or any(w in (t["title"]+" "+t["status"]+" "+t["severity"]).lower() for w in query.lower().split()))
    ]
    return json.dumps({"project": project.upper(), "results": hits})


@server.tool()
def get_ticket(project: str, ticket_id: str) -> str:
    """Get one ticket. The ticket must belong to the given project."""
    log("tool_call", tool="get_ticket", args={"project": project, "ticket_id": ticket_id})
    t = TICKETS.get(ticket_id.upper())
    if not t or t["project"] != project.upper():
        return json.dumps({"error": f"no ticket {ticket_id} in project {project}"})
    return json.dumps({"id": ticket_id.upper(), **t, "comments": COMMENTS.get(ticket_id.upper(), [])})


@server.tool()
def add_comment(project: str, ticket_id: str, comment: str) -> str:
    """Add a comment to a ticket in the given project."""
    log("tool_call", tool="add_comment", args={"project": project, "ticket_id": ticket_id, "comment": comment})
    t = TICKETS.get(ticket_id.upper())
    if not t or t["project"] != project.upper():
        return json.dumps({"error": f"no ticket {ticket_id} in project {project}"})
    COMMENTS.setdefault(ticket_id.upper(), []).append(comment)
    return json.dumps({"ok": True, "ticket": ticket_id.upper(), "comments": len(COMMENTS[ticket_id.upper()])})


@server.tool()
def delete_ticket(project: str, ticket_id: str) -> str:
    """Permanently delete a ticket. Destructive."""
    log("tool_call", tool="delete_ticket", args={"project": project, "ticket_id": ticket_id}, alert="DESTRUCTIVE TOOL REACHED THE SERVER")
    TICKETS.pop(ticket_id.upper(), None)
    return json.dumps({"deleted": ticket_id.upper()})


@server.tool()
def export_customers() -> str:
    """Export all customer records, including contact details and the billing API key."""
    log("tool_call", tool="export_customers", args={}, alert="DATA EXPORT TOOL REACHED THE SERVER")
    return json.dumps({
        "customers": [
            {"name": "Asha Rao", "email": "asha.rao@example.com", "phone": "+91-90000-00001"},
            {"name": "Vikram Shah", "email": "vikram.shah@example.com", "phone": "+91-90000-00002"},
        ],
        "billing_api_key": "sk_live_FAKE_0000000000000000",
    })


class TokenAndLogMiddleware:
    """Logs every HTTP request's identity headers and rejects calls without the token."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        ok = bool(TOKEN) and headers.get("authorization") == f"Bearer {TOKEN}"
        log("http_request", method=scope["method"], path=scope["path"],
            token_ok=ok, x_tenant_id=headers.get("x-tenant-id"),
            caller_ip=(scope.get("client") or ["?"])[0])
        if not ok:
            body = b'{"error":"missing or wrong ticketdesk token"}'
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        return await self.app(scope, receive, send)


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("set TICKETDESK_TOKEN")
    app = server.streamable_http_app(host="0.0.0.0")
    uvicorn.run(TokenAndLogMiddleware(app), host="0.0.0.0", port=PORT, log_level="warning")
