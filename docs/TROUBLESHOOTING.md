# Troubleshooting

Problems hit while building this POC, and their fixes.

## AWS sign-in and CLI

| Symptom | Cause | Fix |
|---|---|---|
| `aws login`: profile 'default' is already configured with Access Key credentials | `aws login` won't overwrite a profile that holds access keys | Use a separate profile: `aws login --remote --profile poc`, then `export AWS_PROFILE=poc` |
| `aws sts get-caller-identity` shows `:root` | Signed in as the account root user | Create an IAM admin user with MFA and use that. Check that root has no access keys |
| `SessionManagerPlugin is not found` | The Session Manager plugin isn't installed on the laptop | Install it from AWS's "Install the Session Manager plugin" page for your OS and CPU type |
| A pasted heredoc (`cat > file <<EOF`) hangs in CloudShell | The closing `EOF` line was lost or indented in the paste | Ctrl+C, then use the scripts in this repo, or write the file with one `printf` line |
| `curl` on the VM: `X-Amz-Expires=3600: command not found`, several background jobs | A presigned URL pasted without quotes; bash splits it at each `&` | Put the URL in single quotes. `scripts/send-file-to-vm.sh` prints the command already quoted |
| Presigned download returns 403 | The link expired (1 hour) or the region doesn't match the bucket | Run the send script again |

## Bedrock

| Symptom | Cause | Fix |
|---|---|---|
| `403 … INVALID_PAYMENT_INSTRUMENT` for Claude | AWS Marketplace refused the account's payment method for the Anthropic subscription | Use Nova Lite (the default), or fix the default payment method (a Visa or Mastercard credit card) and retry |
| `403 … aws-marketplace:ViewSubscriptions / Subscribe` | The agent asked for a Claude model, for example because `MODEL_ID` was unset in an old copy of `poc_agent.py`. The gateway's role tries to subscribe on first use | `export MODEL_ID=apac.amazon.nova-lite-v1:0`. Don't give the gateway's role Marketplace permissions; subscribe once from the console as an admin instead |
| `403 … not authorized to perform bedrock:InvokeModel` for the approved model | The model ID doesn't match the IAM policy, or there's a typo | `aws iam get-role-policy --role-name poc-gateway-role --policy-name bedrock-nova-lite-only`; compare with `MODEL_ID` |
| Gateway log mentions IMDS or "no EC2 IMDS role" | Metadata hop limit 1: containers can't reach the role's credentials | `aws ec2 modify-instance-metadata-options --instance-id $IID --http-tokens required --http-put-response-hop-limit 2`, then `~/poc/gw restart gateway` |

## VM and gateway

| Symptom | Cause | Fix |
|---|---|---|
| Session Manager: instance not connected | The agent hasn't registered yet, or the role is missing | Wait 2–3 minutes; check the instance has `poc-gateway-role` |
| `POC bootstrap finished` never appears | A download failed on first boot | `sudo tail -100 /var/log/poc-bootstrap.log`. Simplest fix: terminate and launch again |
| `permission denied … docker.sock` | The shell started before the docker group was added | `exit`, then `sudo su - ubuntu` |
| `setup_policy.sh` fails at step 3 | ticketdesk isn't running | `systemctl status ticketdesk`; `journalctl -u ticketdesk -n 50` |
| `setup_policy.sh`: keys.env already exists | It was run before | The policy is already there. To redo it, delete `keys.env` and the objects in the console |
| Lost the admin key | Keys are stored hashed and can't be shown again | `export ADMIN=$(~/poc/gw exec -T gateway /gateway bootstrap-key --force --name another-admin 2>/dev/null \| tail -1)` |
| Search returns 0 results | An old ticketdesk matched titles only | Use the current `kit/ticketdesk_mcp.py` (matches title, status and severity words), then `sudo systemctl restart ticketdesk` |
| Access Logs, Overview and Session Timeline are empty | ClickHouse isn't running | `~/poc/gw ps`; `~/poc/gw up -d` |
| Port 8082 returns 429 "too many failed authentication attempts" | Test 7 (or 10 bad keys within a minute) tripped the per-IP lockout | Wait 5 minutes |
| A disabled connector's tools are still in `tools/list` | Tool-list cache, refreshed every 10 minutes | Expected in v0.4.0: calls are refused anyway. Delete the connector to clear it at once |
| Discovery returns `500 internal error` | Often an egress block on a redirect or a private address | `~/poc/gw logs gateway \| grep -i egress` |
