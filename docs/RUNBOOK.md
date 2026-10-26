# Runbook: build, use and tear down the POC

This runbook builds the whole POC in a personal AWS account. It assumes little prior experience: each step says where to run it, what it does, and what you should see.

| Phase | What | Time |
|---|---|---|
| 1 | Prepare the AWS account | 10 min |
| 2 | Create the AWS resources and launch the VM | 10 min |
| 3 | Watch the VM install itself | 15–25 min, mostly waiting |
| 4 | Create the gateway admin and write the policy | 5 min |
| 5 | Open the gateway console from your laptop | 10 min, once |
| 6 | Normal agent run | 5 min |
| 7 | Governance tests | 5 min to run, plus reading |
| 8 | Where everything is logged | reference |
| 9 | Copy the results off the VM | 5 min |
| 10 | Pause, resume, tear down | 5 min |

**Three places run commands:**
- **CloudShell**: the `>_` icon in the AWS console's top bar. It's a terminal already signed in as you, with the AWS CLI.
- **VM shell**: EC2 → Instances → `ai-gateway-poc` → Connect → Session Manager → Connect. This opens as `ssm-user`; always run `sudo su - ubuntu` first.
- **Laptop**: any machine with AWS CLI v2 (2.32 or newer) and the Session Manager plugin. It's only used for port-forwarding and file transfer.

---

## Phase 1: Prepare the AWS account

1. **Sign in as an IAM user with MFA, not root.** If you only have root: IAM → Users → Create user (for example `amit-admin`), attach `AdministratorAccess`, enable console access and MFA, then sign in as that user. Also check that root has no access keys (IAM → Security credentials).
2. **Switch the region to Asia Pacific (Mumbai), `ap-south-1`**, using the menu at the top right.
3. **Set a budget alert:** Billing → Budgets → Create budget → Use a template → Monthly cost budget, $10, your email. A forgotten VM then gets caught within days.
4. **Model access.**
   - **Amazon Nova Lite (the default) needs nothing extra.** It is not sold through AWS Marketplace.
   - **Claude Haiku 4.5 (optional, `MODEL_FAMILY=claude`):**
     1. In Bedrock → Model catalog, open Claude Haiku 4.5 and fill in the one-time Anthropic use-case form.
     2. Send one message in its playground as your admin user. That first call creates the Marketplace subscription, which the gateway's role has no permission to do.
     3. If the playground fails with `INVALID_PAYMENT_INSTRUMENT`, your account's payment method can't buy Marketplace products. This happened on the 27 Oct POC with an India-billed account. Fix the default payment method (a Visa or Mastercard credit card), or stay on Nova Lite.

## Phase 2: Create the AWS resources (CloudShell)

1. **Get this repo into CloudShell:** `git clone https://github.com/amnigam/tuskira-poc.git && cd tuskira-poc`.
2. **Build the VM's start-up script:**
   ```bash
   scripts/build-user-data.sh
   ```
   This embeds everything in `kit/` into `dist/poc-user-data.sh`, which must stay under EC2's 16 KB limit. Expected output: `Built dist/poc-user-data.sh (12909 bytes, limit 16384)`, or a similar size.
3. **Create everything and launch:**
   ```bash
   scripts/aws-create.sh                      # Amazon Nova Lite
   # MODEL_FAMILY=claude scripts/aws-create.sh   # Claude Haiku 4.5 instead
   ```

What the script creates, and why:

| Resource | Setting | Why |
|---|---|---|
| IAM role `poc-gateway-role` | Trusted by EC2; `AmazonSSMManagedInstanceCore` | The VM's identity. Session Manager access means no SSH and no open ports |
| Inline policy `bedrock-nova-lite-only` (or `bedrock-haiku-only`) | One model, only through one inference profile | **The model allow-list.** The gateway signs every Bedrock call with this role, so this policy decides which models exist |
| Instance profile `poc-gateway-profile` | Wraps the role | EC2 attaches roles through instance profiles |
| Security group `poc-gateway-sg` | **No inbound rules** | Nothing on the internet can reach the VM |
| EC2 `t3.large`, Ubuntu 24.04 | 2 vCPU, 8 GB | Enough for the gateway, Postgres and ClickHouse together |
| `HttpTokens=required` | IMDSv2 only | Protects the role's credentials from SSRF-style theft |
| `HttpPutResponseHopLimit=2` | Metadata reachable from inside Docker | **Required.** With 1, the gateway container can't get the role's credentials |
| 30 GB gp3, encrypted | | Room for images; captured prompts are stored on this disk |
| `~/poc-ids.env` | Instance and security group IDs | Used by `aws-teardown.sh` later |

**About the Nova Lite policy** (`aws/iam/bedrock-nova-lite.json.tmpl`):
- From Mumbai, Nova Lite is reached through the APAC cross-Region inference profile `apac.amazon.nova-lite-v1:0`.
- The policy allows that profile in your region, and the underlying model in any region, but only when the call comes through that profile.

**About the Claude policy** (`aws/iam/bedrock-claude-haiku-global.json.tmpl`):
- It uses AWS's three-statement pattern for global inference profiles.
- The third statement matches `aws:RequestedRegion = "unspecified"`, which is how AWS reports globally routed calls. Without it, global calls are denied.

**At the end, the script waits for Session Manager to report the VM as `Online`.**

## Phase 3: Watch the VM install itself (VM shell)

On first boot the VM runs `dist/poc-user-data.sh` by itself. It:
- installs Docker;
- unpacks the kit to `~/poc`;
- starts the dummy MCP server as the `ticketdesk` service;
- clones the gateway at v0.4.0 and builds and starts it with Docker Compose (the slow part).

```bash
sudo su - ubuntu
sudo tail -f /var/log/poc-bootstrap.log      # wait for "POC bootstrap finished", then Ctrl+C
exit; sudo su - ubuntu                       # a fresh shell picks up the docker group
ls ~/poc/BOOTSTRAP_DONE                      # exists when the bootstrap succeeded
~/poc/gw ps                                  # gateway, postgres, clickhouse, adminer: running or healthy
systemctl status ticketdesk --no-pager | head -3
curl -s localhost:8081/api/v1/health | jq .status
curl -s -X POST localhost:9000/mcp -H 'Content-Type: application/json' -d '{}'
#   -> {"error":"missing or wrong ticketdesk token"}  (only the gateway will hold that token)
```

`~/poc/gw` is a shortcut for the long `docker compose` command. For example, `~/poc/gw logs gateway` and `~/poc/gw restart gateway` just work.

**What is on the VM:**

| Path | What it is |
|---|---|
| `~/poc/ticketdesk_mcp.py` | Dummy MCP server, port 9000. Five tools from harmless to dangerous; logs every request |
| `~/poc/ticketdesk.env` | The server's random token. After Phase 4, only the server and the gateway's encrypted store hold it |
| `~/poc/ticketdesk.log` | The server's own record of what actually arrived |
| `~/poc/poc_agent.py` | The agent: Bedrock Converse through the gateway's LLM plane, tools through its MCP plane |
| `~/poc/setup_policy.sh` | Writes the governance policy (Phase 4) |
| `~/ai-agent-gateway/` | Gateway source at v0.4.0. Its `.env` holds the master key that encrypts stored credentials |

## Phase 4: Create the admin and write the policy (VM shell)

```bash
cd ~/poc
# 1. First admin key. It is printed once, so save it to a file only you can read.
ADMIN=$(~/poc/gw exec -T gateway /gateway bootstrap-key 2>/dev/null | tail -1)
umask 077; echo "ADMIN=$ADMIN" > admin.env; umask 022
echo "${ADMIN:0:10}..."                      # should start with gk_

# 2. A console login. It prints a one-time password you change at first sign-in.
~/poc/gw exec gateway /gateway create-user -tenant default -username admin -role admin
#    Optional read-only login for a reviewer:  ... -username reviewer -role viewer

# 3. The policy
source admin.env && export ADMIN && ./setup_policy.sh
```

The gateway stores only a hash of each key, so a lost admin key can't be recovered. To create another:
```bash
export ADMIN=$(~/poc/gw exec -T gateway /gateway bootstrap-key --force --name another-admin 2>/dev/null | tail -1)
```

**What `setup_policy.sh` creates, and the question each part answers:**

| Object | Setting | Answers |
|---|---|---|
| Credential `ticketdesk-token` | Encrypted with the master key; never returned by the API | Who holds the tool's secret? Only the gateway |
| Connector `ticketdesk` | Injects `Authorization: Bearer <token>` on every call | How is a call authorised at the tool? The gateway adds the token on the way through |
| Connector `ticketdesk` | `tool_arg_overrides: {"project": "APPSEC"}` | Can the model reach FINANCE or HR? No: `project` is forced and hidden from the model |
| Connector `ticketdesk` | `server_requests` all off | Can the tool server drive the agent's model or prompt the user? No |
| Profile `appsec-helpdesk` | search_tickets, get_ticket, add_comment | What may a helpdesk bot do? Read and comment |
| Profile `appsec-readonly` | search_tickets, get_ticket | What may a reporting bot do? Read only |
| Keys `helpdesk-bot`, `readonly-bot` | Role `agent`, one profile each; daily $, requests/min and max_tokens limits | Which agent is this, and how much may it spend? |
| Gateway setting | `GATEWAY_MCP_REQUIRE_PROFILE=true` | Can a key with no profile see tools? No |
| IAM (Phase 2) | One model through one profile | Which models exist at all? One |

The agent keys are written to `~/poc/keys.env`. A key *is* its profile: the gateway looks up the profile bound to the key on every request. That's why `HELPDESK_KEY` acts as the helpdesk bot no matter what the agent claims.

## Phase 5: Open the console (laptop)

The console listens only on the VM's `localhost:8081`. Session Manager carries that port to your laptop through its encrypted channel; nothing opens on AWS.

```bash
aws configure set region ap-south-1 --profile poc
aws login --remote --profile poc     # a separate profile leaves your 'default' credentials untouched
export AWS_PROFILE=poc               # repeat in every new terminal
scripts/port-forward.sh              # leave running; open http://localhost:8081
```

**Notes:**
- `aws login` needs AWS CLI 2.32 or newer. Its session lasts up to 12 hours.
- Without `--profile`, `aws login` refuses to run if your `default` profile holds access keys.
- If the Session Manager plugin is missing, install it from AWS's "Install the Session Manager plugin" page for your OS.

Sign in with the login from Phase 4. [CONSOLE-TOUR.md](CONSOLE-TOUR.md) explains each page.

## Phase 6: A normal agent run (VM shell)

```bash
cd ~/poc && source keys.env && source .venv/bin/activate
export GATEWAY_KEY=$HELPDESK_KEY            # MODEL_ID defaults to apac.amazon.nova-lite-v1:0
python poc_agent.py "Which open AppSec tickets are high severity? Add the comment 'triaged by helpdesk-bot' to it."
```

Expect `[stage 1]` to list 3 tools, then a few `[stage 3]` model calls and `[stage 2]` tool calls, then an answer. The model's exact steps vary.

**Look at the same run from three sides:**
1. **The agent's view** (the output above). It saw 3 of 5 tools, and none of them has a `project` input.
2. **The tool's view:** run `tail -n 8 ~/poc/ticketdesk.log | jq -c .`.
   - `http_request` lines show `"token_ok": true`: the gateway added the token.
   - `tool_call` lines show `"project": "APPSEC"`: the gateway added the project, which the model never sent.
3. **The gateway's view:**
   - Console → Observe → **LLM Logs** shows one row per model call.
   - Console → Observe → **Access Logs** shows one row per MCP request.
   - Both are attributed to `helpdesk-bot`.

For Claude instead: `export MODEL_ID=global.anthropic.claude-haiku-4-5-20251001-v1:0`. This only works if you launched with `MODEL_FAMILY=claude`.

## Phase 7: Governance tests

The test suite makes every attempt for real and checks the outcome against the tool server's own log. It saves proof files and writes a one-page summary. See [TESTS.md](TESTS.md) for what each test does.

```bash
# VM (the repo is public, so the VM can clone it directly)
cd ~/poc && git clone https://github.com/amnigam/tuskira-poc.git     # later: git -C tuskira-poc pull
cd ~/poc/tuskira-poc/tests
source ~/poc/admin.env && export ADMIN
~/poc/.venv/bin/python -I run_tests.py                  # about 3-5 minutes
cat ~/poc/test-results/*/SUMMARY.md
```

If the VM can't reach GitHub, run `scripts/pack-tests.sh && scripts/send-file-to-vm.sh dist/poc-tests.tgz` on your laptop instead. Paste the command it prints on the VM, then `tar xzf poc-tests.tgz`.

**Side effects:**
- Test 10 disables ticketdesk for a few seconds and then re-enables it.
- Test 7 ends with a deliberate brute-force lockout: port 8082 refuses the VM for 5 minutes afterwards.
- Temporary keys, a probe connector and a probe profile are created and deleted again.
- Your two agent keys and their profiles are never changed.

## Phase 8: Where everything is logged

| Question | Where to look |
|---|---|
| Which agent called which tool, and was it allowed? | Console → Observe → Access Logs (row details show the profile, error code and bodies) |
| What did one agent session do, in order? | Click a Session ID in Access Logs → Session Timeline |
| What did each model call cost, and who made it? | Console → Observe → LLM Logs; Dashboards → Token Monitoring |
| What exactly went to the model and came back? | LLM Logs → open a row (Messages, System, Tools, bodies) |
| Calls refused for budget or token caps | LLM Logs, error column: `budget_exceeded`, `max_tokens_exceeded` |
| Raw tables | Adminer: `scripts/port-forward.sh 8090`, then http://localhost:8090 (PostgreSQL, server `postgres`, user/password/database `gateway`) |
| JSON lines for a SIEM | `~/poc/gw logs --no-log-prefix gateway`, records of `"type":"access"` and `"type":"llm_call"` |
| What actually reached the tool | `~/poc/ticketdesk.log` |
| What AWS saw | CloudTrail → Event history, event source `bedrock.amazonaws.com`. The caller is always `poc-gateway-role`, never the individual agent |

Our `poc_agent.py` doesn't send a session ID on its model calls, so Session Timeline shows only the MCP side. Model calls are matched to tool calls by key and time.

## Phase 9: Copy the results off the VM

Use a temporary file server and the same port-forward:

```bash
# VM
cd ~/poc/test-results && ls && python3 -m http.server 8099 --bind 127.0.0.1
# laptop, second terminal
scripts/port-forward.sh 8099
# laptop, third terminal (or a browser at http://localhost:8099/)
curl -O http://localhost:8099/<run>.tgz
```

Press Ctrl+C in both terminals when you're done.

In the archive, keys are redacted to their first few characters and the ticketdesk token is replaced with a placeholder. Prompts and the dummy server's fake customer data are still in it.

## Phase 10: Pause, resume, tear down

- **Pause** (you pay only for the disk, about $3 a month):
  ```bash
  source ~/poc-ids.env; aws ec2 stop-instances --instance-ids "$IID"
  ```
- **Resume:**
  ```bash
  aws ec2 start-instances --instance-ids "$IID"
  ```
  Then in a VM shell run `~/poc/gw up -d`. The ticketdesk service restarts on its own. Restart your port-forward too.
- **Tear down** (CloudShell, in the repo folder): `scripts/aws-teardown.sh`. It terminates the VM, which deletes its disk, keys and logs, then deletes the security group, instance profile, inline policies and role.
- **If you used `send-file-to-vm.sh`**, delete its bucket: `scripts/send-file-to-vm.sh --cleanup`.

---

## Moving to an org account

Same design, with these changes:

**Network**
- Use a private subnet with no public IP. Reach Bedrock, SSM and ECR through VPC endpoints.
- The gateway's egress guard blocks private addresses, so allow-list the endpoint hostnames: `GATEWAY_EGRESS_ALLOWED_HOSTS=bedrock-runtime.<region>.amazonaws.com,...`.
- Tool servers should accept traffic only from the gateway, through security groups or a private network.

**Identity, access and configuration**
- Give the gateway its own role with a model allow-list per gateway deployment. For different model tiers per team, run separate deployments with separate roles.
- Use real internal MCP servers, each with least-privilege service tokens stored in the gateway.
- Keep agent keys in Secrets Manager and rotate them. Use one key per agent per environment.
- Keep profiles and policy as code: a script like `setup_policy.sh`, reviewed in Git.
- Fix the six findings in [RESULTS-2026-10-27.md](RESULTS-2026-10-27.md). In particular:
  - redefine the `agent` role through `auth.roles`;
  - set `GATEWAY_API_TRUSTED_PROXIES` behind a load balancer.

**Logging and content controls**
- Send the gateway's JSON lines or OpenTelemetry output to the SIEM.
- Make Bedrock Guardrails mandatory through IAM, because the gateway does not inspect content.

**Where to run it**
- Run on EKS or ECS rather than Docker Compose. Set environment variables directly, since the Compose overlay in `kit/` exists only because Compose passes through only the variables it lists.
