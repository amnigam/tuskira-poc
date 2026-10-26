"""poc_agent.py - a minimal AI agent that reaches tools and the model ONLY through the gateway.

  model calls -> gateway LLM plane :8082 -> Amazon Bedrock (Converse API)
  tool calls  -> gateway MCP plane :8080 -> registered MCP servers (ticketdesk)

The agent holds exactly one secret: its gateway key (GATEWAY_KEY). No AWS
credentials, no ticketdesk token.

Usage:
  GATEWAY_KEY=gk_... MODEL_ID=apac.amazon.nova-lite-v1:0 \
  python poc_agent.py "Which open AppSec tickets are high severity?"
"""
import asyncio
import json
import os
import sys

import boto3
import httpx2
from botocore import UNSIGNED
from botocore.config import Config
from botocore.exceptions import ClientError
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

GW = os.environ.get("GATEWAY_HOST", "http://localhost")
KEY = os.environ["GATEWAY_KEY"]
MODEL = os.environ.get("MODEL_ID", "apac.amazon.nova-lite-v1:0")
REGION = os.environ.get("AWS_REGION", "ap-south-1")
MCP_URL = os.environ.get("MCP_URL", f"{GW}:8080/mcp")
LLM_URL = os.environ.get("LLM_URL", f"{GW}:8082/bedrock")
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "1024"))
SYSTEM = ("You are a helpdesk assistant for the AppSec team. Use the ticket tools to answer. "
          "If a tool is refused or missing, say so plainly. Be brief.")

_last_error_body = {}


def bedrock_via_gateway():
    """A normal boto3 Bedrock client pointed at the gateway. It signs nothing
    (UNSIGNED); the gateway signs with its own AWS identity. The only credential
    sent is the gateway key, added as the X-Gateway-Key header."""
    client = boto3.client("bedrock-runtime", region_name=REGION, endpoint_url=LLM_URL,
                          config=Config(signature_version=UNSIGNED, retries={"max_attempts": 1}))

    def add_gateway_key(request, **_):
        request.headers["X-Gateway-Key"] = KEY

    def keep_error_body(http_response, **_):  # boto3 hides the gateway's error text; keep it
        if http_response.status_code >= 400:
            _last_error_body["text"] = http_response.text

    client.meta.events.register("before-send.bedrock-runtime.*", add_gateway_key)
    client.meta.events.register("after-call.bedrock-runtime.*", keep_error_body)
    return client


def to_bedrock_tool(tool):
    schema = {k: v for k, v in tool.input_schema.items() if k not in ("$schema", "title")}
    return {"toolSpec": {"name": tool.name, "description": tool.description or tool.name,
                         "inputSchema": {"json": schema}}}


async def call_tool(mcp, name, args):
    try:
        result = await mcp.call_tool(name, args)
        text = "".join(b.text for b in result.content if b.type == "text")
        return text, ("error" if result.is_error else "success")
    except Exception as exc:  # a JSON-RPC error from the gateway, e.g. -32003 tool not allowed
        return f"REFUSED BY GATEWAY: {exc}", "error"


async def main(prompt):
    http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {KEY}"}, timeout=60)
    async with Client(streamable_http_client(MCP_URL, http_client=http)) as mcp:
        tools = (await mcp.list_tools()).tools
        print(f"[stage 1] tools this agent may see: {[t.name for t in tools]}")
        llm = bedrock_via_gateway()
        messages = [{"role": "user", "content": [{"text": prompt}]}]

        for turn in range(1, 9):
            kwargs = {"modelId": MODEL, "system": [{"text": SYSTEM}], "messages": messages,
                      "inferenceConfig": {"maxTokens": MAX_TOKENS}}
            if tools:
                kwargs["toolConfig"] = {"tools": [to_bedrock_tool(t) for t in tools]}
            try:
                resp = llm.converse(**kwargs)
            except ClientError as e:
                status = e.response["ResponseMetadata"]["HTTPStatusCode"]
                body = _last_error_body.get("text") or e.response.get("Error", {}).get("Message", "")
                print(f"[stage 3] model call {turn} REFUSED: HTTP {status}: {body}")
                return
            msg = resp["output"]["message"]
            messages.append(msg)
            u = resp["usage"]
            print(f"[stage 3] model call {turn}: stop={resp['stopReason']} "
                  f"tokens in={u['inputTokens']} out={u['outputTokens']}")

            if resp["stopReason"] != "tool_use":
                print("\nAnswer: " + "".join(b.get("text", "") for b in msg["content"]))
                return

            results = []
            for block in msg["content"]:
                if "toolUse" in block:
                    tu = block["toolUse"]
                    print(f"[stage 2] model asked for {tu['name']} {json.dumps(tu['input'])}")
                    text, status = await call_tool(mcp, tu["name"], tu["input"])
                    shown = text if len(text) < 300 else text[:300] + "..."
                    print(f"          -> {status}: {shown}")
                    results.append({"toolResult": {"toolUseId": tu["toolUseId"],
                                                   "content": [{"text": text}], "status": status}})
            messages.append({"role": "user", "content": results})
        print("stopped after 8 model calls")


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or "Which open AppSec tickets are high severity?"))
