POC governance tests
====================

Files
  run_tests.py        runs scenarios 1-11, 14, 15, 17, 18 and writes the proof files
  probe_mcp.py        test 17: an MCP server that tries to reach back to the agent
  redirect_server.py  test 18: a server that redirects the gateway to the metadata service

Run (on the POC VM, as ubuntu, gateway and ticketdesk running)
  cd ~/poc/poc-tests
  ADMIN=gk_your_admin_key ../.venv/bin/python -I run_tests.py

  Takes 3-5 minutes. Add --no-agent to skip the natural-language agent runs,
  or --only 4,17 to run selected scenarios.

Output
  ~/poc/test-results/<run>/SUMMARY.md     one page: results of every check (send this back)
  ~/poc/test-results/<run>/NN/            per scenario: result.json, requests.json
                                          (every request and response, keys redacted),
                                          ticketdesk_log.jsonl, agent transcripts, server logs
  ~/poc/test-results/<run>.tgz            all of the above in one archive

Side effects
  * Temporary keys, a probe connector and a probe profile are created and deleted again.
  * Scenario 10 disables the ticketdesk connector for a few seconds and re-enables it.
  * Scenario 7 ends with a deliberate brute-force lockout: port 8082 (models) refuses this
    VM for 5 minutes afterwards. MCP (8080) and the console (8081) are not affected.
  * helpdesk-bot, readonly-bot and their profiles are not changed.
