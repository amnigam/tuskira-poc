# Mock gateway

`mockgw.py` imitates the three ports of AI Agent Gateway v0.4.0:
- 8080 MCP
- 8081 console API
- 8082 LLM

It imitates them closely enough to run every code path in `tests/run_tests.py`. The status codes, JSON-RPC error codes and messages were copied from the gateway's source, and the 27 Oct run against the real gateway matched them.

```bash
tests/mock/run-mock.sh              # all tests
tests/mock/run-mock.sh --only 4,17  # selected tests
```

**What it does:**
- It starts the real `kit/ticketdesk_mcp.py` and the mock, then runs the tests with a temporary home folder in place of `~/poc`.
- Test 17 runs against the real probe server; the mock plays the gateway's part of refusing sampling and elicitation.
- The agent runs are stubbed out: no model is called.

**Use it to check that changes to `run_tests.py` don't crash.** Never use it as evidence about the gateway.

**It needs ports 8080–8082 and 9000–9002 to be free.**
