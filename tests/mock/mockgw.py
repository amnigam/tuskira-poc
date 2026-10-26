"""Mock of the gateway's three planes, close enough to v0.4.0 to exercise run_tests.py."""
import json, re, secrets, threading, time, uuid, urllib.request, urllib.error, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TD_LOG = os.environ["TD_LOG"]
ADMIN = os.environ["ADMIN"]
CID = "c-td"
TOOLS = ["search_tickets", "get_ticket", "add_comment", "delete_ticket", "export_customers"]
KEYS = {ADMIN: {"id": "k-admin", "role": "admin", "profile_id": "", "limits": {}}}
PROFILES = {"p-help": {"name": "appsec-helpdesk", "tools": {(CID, t) for t in TOOLS[:3]}},
            "p-ro": {"name": "appsec-readonly", "tools": {(CID, t) for t in TOOLS[:2]}}}
CONNS = {CID: {"id": CID, "name": "ticketdesk", "slug": "ticketdesk", "endpoint": "http://host.docker.internal:9000/mcp",
               "timeout_ms": 15000, "metadata": {"headers": {"Authorization": {"type": "external", "config": {"credential": "***"}}},
                                                  "tool_arg_overrides": {"project": "APPSEC"}}, "tools": TOOLS}}
SESS = set()
FAILS = {}
TICKETS = {"APPSEC-101": "APPSEC", "APPSEC-102": "APPSEC", "APPSEC-103": "APPSEC", "FIN-201": "FINANCE"}
RPM = {}


def mkkey(name, profile_id, limits, role="agent"):
    k = "gk_" + secrets.token_hex(16)
    KEYS[k] = {"id": "k-" + uuid.uuid4().hex[:8], "name": name, "role": role, "profile_id": profile_id, "limits": limits}
    return k


HELP = mkkey("helpdesk-bot", "p-help", {"daily_usd": 1, "rpm": 20, "max_tokens": 2048})
RO = mkkey("readonly-bot", "p-ro", {"daily_usd": 0.5, "rpm": 10, "max_tokens": 1024})


def tdlog(rec):
    with open(TD_LOG, "a") as f:
        f.write(json.dumps({"ts": time.strftime("%H:%M:%S"), **rec}) + "\n")


def keyrec(k):
    r = KEYS.get(k)
    return r if r and not r.get("revoked") else None


def blocked(url):
    host = re.sub(r"^https?://", "", url).split("/")[0]
    return any(s in host for s in ("169.254", "2852039166", "0xa9fea9fe", "fd00:ec2"))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, obj=None, headers=None):
        body = b"" if obj is None else json.dumps(obj).encode()
        self.send_response(code)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"null")

    def bearer(self):
        a = self.headers.get("Authorization", "")
        return a[7:] if a.startswith("Bearer ") else ""

    # ---------------------------------------------------------------- dispatch
    def do_GET(self): self.route("GET")
    def do_POST(self): self.route("POST")
    def do_PUT(self): self.route("PUT")
    def do_PATCH(self): self.route("PATCH")
    def do_DELETE(self): self.route("DELETE")

    def route(self, m):
        port = self.server.server_address[1]
        if self.path == "/health" or self.path == "/api/v1/health":
            return self.send(200, {"status": "ok"})
        if port == PAPI: return self.api(m)
        if port == PMCP: return self.mcp(m)
        if port == PLLM: return self.llm(m)

    # -------------------------------------------------------------------- API
    def api(self, m):
        k = keyrec(self.bearer())
        if not k:
            return self.send(401, {"error": {"type": "unauthorized"}})
        p = self.path.split("?")[0][len("/api/v1"):]
        admin = k["role"] == "admin"
        readable = m == "GET" and re.match(r"^/(profiles|connectors|analytics/llm-logs$|analytics/logs$)", p)
        if not admin and not readable:
            return self.send(403, {"error": {"type": "forbidden", "message": "insufficient permissions"}})
        b = self.body() if m in ("POST", "PUT", "PATCH") else None
        if p == "/api-keys" and m == "GET":
            return self.send(200, {"items": [], "total": 0})
        if p == "/api-keys" and m == "POST":
            key = mkkey(b["name"], b.get("profile_id", ""), b.get("limits") or {}, b.get("role", "agent"))
            return self.send(201, {**KEYS[key], "key": key})
        mm = re.match(r"^/api-keys/([^/]+)$", p)
        if mm:
            rec = next((v for v in KEYS.values() if v["id"] == mm.group(1)), None)
            if m == "PATCH":
                rec["limits"] = b["limits"]
                return self.send(200, rec)
            if m == "DELETE":
                rec["revoked"] = True
                return self.send(204)
        if p == "/connectors" and m == "GET":
            return self.send(200, {"items": list(CONNS.values()), "total": len(CONNS)})
        if p == "/connectors" and m == "POST":
            if blocked(b["endpoint"]) or "nip.io" in b["endpoint"]:
                return self.send(400, {"error": {"type": "egress_blocked", "message": "egress_blocked: " + b["endpoint"]}})
            cid = "c-" + uuid.uuid4().hex[:6]
            CONNS[cid] = {"id": cid, "name": b["name"], "slug": b.get("slug") or b["name"], "endpoint": b["endpoint"],
                          "timeout_ms": 15000, "metadata": b.get("metadata") or {}, "tools": []}
            return self.send(201, CONNS[cid])
        mm = re.match(r"^/connectors/([^/]+)(/discover)?$", p)
        if mm:
            c = CONNS.get(mm.group(1))
            if mm.group(2):
                try:
                    req = urllib.request.Request(c["endpoint"].replace("host.docker.internal", "127.0.0.1"), data=b"{}", method="POST")
                    class NR(urllib.request.HTTPRedirectHandler):
                        def redirect_request(s, req, fp, code, msg, hdrs, newurl):
                            if blocked(newurl):
                                raise urllib.error.URLError("egress_blocked: dial 169.254.169.254:80")
                            return None
                    urllib.request.build_opener(urllib.request.ProxyHandler({}), NR).open(req, timeout=5)
                except Exception as e:
                    if "egress_blocked" in str(e):
                        return self.send(502, {"error": {"type": "upstream_error", "message": "discover: " + str(e)}})
                c["tools"] = ["probe_server_requests"] if "9001" in c["endpoint"] else c["tools"]
                return self.send(200, {"items": [{"tool_name": t} for t in c["tools"]], "total": len(c["tools"])})
            if m == "GET":
                return self.send(200, {k2: v for k2, v in c.items() if k2 != "tools"})
            if m == "PUT":
                c["metadata"] = b["metadata"]
                return self.send(200, c)
            if m == "DELETE":
                CONNS.pop(mm.group(1), None)
                return self.send(204)
        if p == "/profiles" and m == "GET":
            return self.send(200, {"items": [{"id": i, "name": v["name"]} for i, v in PROFILES.items()]})
        if p == "/profiles" and m == "POST":
            pid = "p-" + uuid.uuid4().hex[:6]
            PROFILES[pid] = {"name": b["name"], "tools": set()}
            return self.send(201, {"id": pid, "name": b["name"]})
        mm = re.match(r"^/profiles/([^/]+)(/tools)?$", p)
        if mm:
            pr = PROFILES[mm.group(1)]
            if mm.group(2) and m == "PUT":
                pr["tools"] = {(t["connector_id"], t["tool_name"]) for t in b["tools"]}
                return self.send(200, {})
            if mm.group(2):
                return self.send(200, {"items": [{"connector_id": c, "tool_name": t} for c, t in pr["tools"]]})
            if m == "DELETE":
                PROFILES.pop(mm.group(1))
                return self.send(204)
        if p == "/credentials":
            return self.send(200, {"items": []})
        if p == "/analytics/llm-logs":
            return self.send(200, {"items": [{"request_id": "req-1"}]})
        if p.startswith("/analytics/llm-logs/"):
            return self.send(200, {"request_id": "req-1"})
        return self.send(404, {"error": {"type": "not_found"}})

    # -------------------------------------------------------------------- MCP
    def rpc(self, rid, result=None, err=None, headers=None):
        o = {"jsonrpc": "2.0", "id": rid}
        if err:
            o["error"] = {"code": err[0], "message": err[1]}
        else:
            o["result"] = result
        self.send(200, o, headers)

    def mcp(self, m):
        k = keyrec(self.bearer())
        if not k:
            return self.send(401, {"jsonrpc": "2.0", "id": None, "error": {"code": -32001, "message": "unauthorized"}})
        if m == "DELETE":
            SESS.discard(self.headers.get("Mcp-Session-Id"))
            return self.send(204)
        b = self.body()
        sid = self.headers.get("Mcp-Session-Id")
        if sid and sid not in SESS:
            return self.rpc(b.get("id"), err=(-32000, "session not found"))
        prof = PROFILES.get(k["profile_id"])
        hdr = self.headers.get("X-Agent-Profile-Name")
        if hdr and prof and hdr != prof["name"]:
            return self.rpc(b.get("id"), err=(-32003, "this API key is bound to agent profile " + prof["name"] + "; the X-Agent-Profile-Name header names a different profile"))
        meth = b.get("method")
        if meth == "initialize":
            s = secrets.token_urlsafe(32)
            SESS.add(s)
            return self.rpc(b["id"], {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "gw"}},
                            headers={"Mcp-Session-Id": s})
        if "id" not in b:
            self.send_response(204); self.end_headers(); return
        allowed = {(c, t) for c, t in prof["tools"] if CONNS.get(c) and CONNS[c]["metadata"].get("enabled", True)}
        listed = {(c, t) for c, t in prof["tools"] if CONNS.get(c)}   # v0.4.0 lists from a cache that ignores "enabled"
        if meth == "tools/list":
            tools = []
            for c, t in sorted(listed, key=lambda x: x[1]):
                props = {"ticket_id": {"type": "string"}, "query": {"type": "string"}}
                if c != CID:
                    props = {}
                tools.append({"name": f"{CONNS[c]['slug']}__{t}", "inputSchema": {"type": "object", "properties": props}})
            return self.rpc(b["id"], {"tools": tools})
        if meth == "tools/call":
            name = b["params"]["name"]; args = dict(b["params"].get("arguments") or {})
            slug, tool = name.split("__", 1)
            c = next((cid for cid, v in CONNS.items() if v["slug"] == slug), None)
            if c is None or not CONNS[c]["metadata"].get("enabled", True):
                return self.rpc(b["id"], err=(-32004, "connector not found"))
            if (c, tool) not in allowed:
                return self.rpc(b["id"], err=(-32003, f"tool {name} not allowed by profile"))
            if c == CID:
                args["project"] = "APPSEC"
                tdlog({"event": "tool_call", "tool": tool, "args": args})
                if tool == "search_tickets":
                    txt = json.dumps({"project": "APPSEC", "results": [{"id": "APPSEC-101"}]})
                else:
                    tid = args.get("ticket_id", "").upper()
                    txt = json.dumps({"id": tid} if TICKETS.get(tid) == "APPSEC" else {"error": f"no ticket {tid} in project APPSEC"})
                return self.rpc(b["id"], {"content": [{"type": "text", "text": txt}]})
            return self.rpc(b["id"], {"content": [{"type": "text", "text": probe_call(CONNS[c]["endpoint"].replace("host.docker.internal", "127.0.0.1"))}]})
        return self.rpc(b["id"], err=(-32601, "method not found"))

    # -------------------------------------------------------------------- LLM
    def llm(self, m):
        ip = self.client_address[0]
        if FAILS.get(ip, 0) >= 10:
            return self.send(429, {"type": "error", "error": {"message": "too many failed attempts"}})
        k = keyrec(self.headers.get("X-Gateway-Key", ""))
        if not k:
            FAILS[ip] = FAILS.get(ip, 0) + 1
            return self.send(401, {"type": "error", "error": {"message": "invalid key"}})
        b = self.body()
        reg = self.headers.get("X-Bedrock-Region")
        if reg and not re.match(r"^[a-z]{2}(-[a-z]+)+-\d{1,2}$", reg):
            return self.send(400, {"type": "error", "error": {"message": f"invalid AWS region {reg!r}"}})
        lim = k["limits"]
        mt = (b.get("inferenceConfig") or {}).get("maxTokens")
        if mt and lim.get("max_tokens") and mt > lim["max_tokens"]:
            return self.send(400, {"type": "error", "error": {"message": f"max_tokens {mt} exceeds this key's limit of {lim['max_tokens']}"}})
        if lim.get("daily_usd") == 0:
            return self.send(429, {"type": "error", "error": {"message": "daily budget exceeded for this key"}})
        model = urllib.request.unquote(self.path.split("/model/")[1].split("/")[0])
        if model != "apac.amazon.nova-lite-v1:0" or reg:
            return self.send(403, {"message": f"User: arn:aws:sts::1:assumed-role/poc-gateway-role is not authorized to perform: bedrock:InvokeModel on {model}"})
        return self.send(200, {"output": {"message": {"role": "assistant", "content": [{"text": "OK"}]}}, "stopReason": "end_turn",
                               "usage": {"inputTokens": 10, "outputTokens": 2}})


def probe_call(url):
    """Act as the gateway's client to the probe connector: initialize (legacy), call, refuse server requests."""
    import http.client
    H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json", "MCP-Protocol-Version": "2025-06-18"}
    def post(body, sid=None, stream=False):
        u = urllib.parse.urlparse(url)
        c = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
        h = dict(H)
        if sid: h["Mcp-Session-Id"] = sid
        c.request("POST", u.path, json.dumps(body), h)
        return c.getresponse()
    r = post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "gw", "version": "1"}}})
    sid = r.getheader("mcp-session-id"); r.read()
    post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid).read()
    r = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "probe_server_requests", "arguments": {}}}, sid)
    while True:
        line = r.fp.readline().decode()
        if not line:
            break
        if not line.startswith("data:"):
            continue
        msg = json.loads(line[5:])
        if "method" in msg and "id" in msg:
            post({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": msg["method"] + " is not permitted for this connector"}}, sid).read()
        elif msg.get("id") == 2:
            return "".join(c.get("text", "") for c in msg["result"]["content"])
    return "{}"


import urllib.parse
PAPI, PMCP, PLLM = 8081, 8080, 8082
if __name__ == "__main__":
    with open(os.environ["KEYS_OUT"], "w") as f:
        f.write(f"HELPDESK_KEY={HELP}\nHELPDESK_KEY_ID={KEYS[HELP]['id']}\nREADONLY_KEY={RO}\nREADONLY_KEY_ID={KEYS[RO]['id']}\n")
    for p in (PAPI, PMCP, PLLM):
        threading.Thread(target=ThreadingHTTPServer(("127.0.0.1", p), H).serve_forever, daemon=True).start()
    while True:
        time.sleep(3600)
