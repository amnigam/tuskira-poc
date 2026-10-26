# AI Agent Gateway POC on AWS

A reproducible proof of concept of [Tuskira's AI Agent Gateway](https://github.com/Tuskira/ai-agent-gateway)
(v0.4.0) in front of Amazon Bedrock and an MCP tool server, with a scripted test suite that
proves which governance controls hold.

Everything runs on one EC2 VM with **no inbound ports**. You reach it through AWS Session
Manager. Agents hold only a gateway key: no AWS credentials and no tool credentials.

```
 agent (poc_agent.py) ──gateway key──▶  AI Agent Gateway  ──IAM role (SigV4)──▶  Amazon Bedrock
                                          │  :8080 MCP plane                         (Nova Lite)
                                          │  :8081 console + admin API
                                          │  :8082 LLM plane
                                          └──ticketdesk token (injected)──▶  ticketdesk MCP server :9000
                                                                              (dummy, 5 tools)
```

## What it shows

| Control | How the POC proves it |
|---|---|
| Per-agent tool allow-lists | `readonly-bot` and `helpdesk-bot` share one MCP server but get 2 and 3 of its 5 tools |
| Deny by default | `delete_ticket` and `export_customers` are granted to nobody and are refused even when called directly |
| Argument pinning | `project` is forced to `APPSEC` and hidden from the model, so FINANCE and HR data is unreachable |
| Central credentials | the ticketdesk token lives only in the gateway's encrypted store; direct calls to the tool get 401 |
| Egress guard | connectors to the EC2 metadata service are refused, including encoded, DNS and redirect forms |
| Key binding | a key cannot claim another agent's profile; made-up keys are locked out |
| Spend control | per-key token caps and daily budgets refuse calls before Bedrock is billed |
| Model allow-list | IAM on the gateway's single role decides which models exist |
| Incident response | revoking a key or disabling a tool server takes effect on the next call |
| Audit | every model and tool call is attributed to the agent key that made it |

**Results of the 27 Oct 2026 run:** all 15 tests passed, with six limits found.
See [docs/RESULTS-2026-10-27.md](docs/RESULTS-2026-10-27.md).

## Repository layout

| Path | What it is |
|---|---|
| `kit/` | Files that go onto the VM: the dummy MCP server, the agent, the policy script, the Compose overlay, and the bootstrap template |
| `tests/` | The governance test suite (`run_tests.py`) and its two hostile helper servers |
| `tests/mock/` | A mock of the gateway's three planes, for changing the tests without AWS |
| `aws/iam/` | The EC2 trust policy and the Bedrock model allow-list templates |
| `scripts/` | Build, launch, port-forward, file transfer and teardown helpers |
| `docs/` | Runbook, test guide, results, console tour, troubleshooting |

## Quick start

This is the short version. [docs/RUNBOOK.md](docs/RUNBOOK.md) explains every step and
what to check at each stage. Allow about 90 minutes the first time, most of it waiting.

**Where commands run:**
- **CloudShell:** the terminal inside the AWS console, already signed in as you.
- **VM:** EC2 → your instance → Connect → Session Manager. Then run `sudo su - ubuntu`.
- **Laptop:** your own machine, with AWS CLI v2 and the Session Manager plugin installed. Used only for the console port-forward and file transfer.

1. **One-time account prep** (AWS console, region Mumbai `ap-south-1`): sign in as an IAM admin user with MFA, not as root, and set a $10 monthly budget alert. For Claude instead of Nova, see Runbook Phase 1.

2. **CloudShell: get the repo, build the user data, launch.**
   ```bash
   git clone https://github.com/amnigam/tuskira-poc.git && cd tuskira-poc
   scripts/build-user-data.sh
   scripts/aws-create.sh                # Nova Lite; use MODEL_FAMILY=claude for Claude Haiku 4.5
   ```

3. **VM: wait for the bootstrap, then write the policy.**
   ```bash
   sudo tail -f /var/log/poc-bootstrap.log            # wait for "POC bootstrap finished", then Ctrl+C
   exit; sudo su - ubuntu                             # pick up the docker group
   cd ~/poc
   ADMIN=$(~/poc/gw exec -T gateway /gateway bootstrap-key 2>/dev/null | tail -1)
   umask 077; echo "ADMIN=$ADMIN" > admin.env; umask 022
   ~/poc/gw exec gateway /gateway create-user -tenant default -username admin -role admin   # console login
   source admin.env && export ADMIN && ./setup_policy.sh
   ```

4. **VM: a normal agent run.**
   ```bash
   cd ~/poc && source keys.env && source .venv/bin/activate
   GATEWAY_KEY=$HELPDESK_KEY python poc_agent.py "Which open AppSec tickets are high severity? Add the comment 'triaged' to it."
   ```

5. **Laptop: open the console.** Run `scripts/port-forward.sh`, then open http://localhost:8081.
   [docs/CONSOLE-TOUR.md](docs/CONSOLE-TOUR.md) explains each page.

6. **VM: run the 15 governance tests.**
   ```bash
   cd ~/poc && git clone https://github.com/amnigam/tuskira-poc.git
   cd ~/poc/tuskira-poc/tests
   source ~/poc/admin.env && export ADMIN && ~/poc/.venv/bin/python -I run_tests.py
   cat ~/poc/test-results/*/SUMMARY.md
   ```
   To update the tests later, run `git -C ~/poc/tuskira-poc pull`.

   [docs/TESTS.md](docs/TESTS.md) explains each test and how to read the results.

7. **Stop or tear down.** To pause, stop the VM (you pay only for the disk). When you're finished, run `scripts/aws-teardown.sh` in CloudShell.

**Cost:** about $0.09 an hour while the VM runs (t3.large in Mumbai). Model calls cost a
fraction of a cent per run.

## Status and caveats

- The gateway is pinned to **v0.4.0**. The tests rely on its API paths and error codes. Re-run them after any upgrade.
- `scripts/aws-create.sh` and `scripts/aws-teardown.sh` script the same commands the runbook gives step by step. The step-by-step commands were used for the 27 Oct POC; the scripts themselves were written afterwards.
- The test suite was run against the real gateway on 27 Oct 2026. Its mock gateway (`tests/mock/`) only mimics v0.4.0's responses and is not a substitute.
- This is a personal-account POC. For an org deployment, see the "org POC" notes in [docs/RUNBOOK.md](docs/RUNBOOK.md#moving-to-an-org-account).
