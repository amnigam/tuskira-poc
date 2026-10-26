#!/bin/bash
# EC2 user data for the AI Agent Gateway POC (Ubuntu 24.04).
# Runs once, as root, on first boot. Progress: /var/log/poc-bootstrap.log
# When finished it creates /home/ubuntu/poc/BOOTSTRAP_DONE.
#
# What it does:
#   1. installs Docker (official repo), git, jq, Python venv
#   2. unpacks the POC kit to /home/ubuntu/poc (dummy MCP server, agent, setup script)
#   3. runs the dummy "ticketdesk" MCP server as a systemd service on :9000
#   4. clones the gateway at tag v0.4.0, writes its .env, and starts it with Docker Compose
set -euxo pipefail
exec > >(tee -a /var/log/poc-bootstrap.log) 2>&1
export DEBIAN_FRONTEND=noninteractive
# wait (up to 10 min) if Ubuntu's first-boot automatic updates hold the apt lock
apt_get() { apt-get -o DPkg::Lock::Timeout=600 "$@"; }

IMDS_TOKEN=$(curl -sX PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
REGION=$(curl -s -H "X-aws-ec2-metadata-token: $IMDS_TOKEN" http://169.254.169.254/latest/meta-data/placement/region)

# 1. packages
apt_get update
apt_get install -y ca-certificates curl git jq python3-venv
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" > /etc/apt/sources.list.d/docker.list
apt_get update
apt_get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
usermod -aG docker ubuntu

# 2. POC kit (embedded below as base64 tar.gz)
mkdir -p /home/ubuntu/poc
base64 -d <<'KIT' | tar -xz -C /home/ubuntu/poc
__KIT_B64__
KIT
cd /home/ubuntu/poc
python3 -m venv .venv
.venv/bin/pip install --quiet "mcp>=2,<3" uvicorn boto3
umask 077
echo "TICKETDESK_TOKEN=$(openssl rand -hex 24)" > ticketdesk.env
umask 022
chown -R ubuntu:ubuntu /home/ubuntu/poc

# 3. dummy MCP server as a service
cat > /etc/systemd/system/ticketdesk.service <<UNIT
[Unit]
Description=Dummy ticketdesk MCP server (POC)
After=network-online.target
[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/poc
EnvironmentFile=/home/ubuntu/poc/ticketdesk.env
Environment=TICKETDESK_LOG=/home/ubuntu/poc/ticketdesk.log
ExecStart=/home/ubuntu/poc/.venv/bin/python -I /home/ubuntu/poc/ticketdesk_mcp.py
Restart=on-failure
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now ticketdesk

# 4. the gateway
sudo -u ubuntu git clone --depth 1 --branch v0.4.0 https://github.com/Tuskira/ai-agent-gateway.git /home/ubuntu/ai-agent-gateway
cd /home/ubuntu/ai-agent-gateway
cp /home/ubuntu/poc/docker-compose.poc.yml deploy/docker-compose.poc.yml
umask 077
cat > .env <<ENV
GATEWAY_MASTER_KEY=$(openssl rand -base64 32)
CLICKHOUSE_PASSWORD=$(openssl rand -hex 16)
GATEWAY_MCP_REQUIRE_PROFILE=true
GATEWAY_LLM_PROXY_BEDROCK_REGION=$REGION
AWS_REGION=$REGION
GATEWAY_LLM_PROXY_PROVIDERS_OPENAI_ENABLED=false
GATEWAY_LLM_PROXY_PROVIDERS_GEMINI_ENABLED=false
GATEWAY_SINKS_CLICKHOUSE_ENABLED=true
ENV
umask 022
chown -R ubuntu:ubuntu /home/ubuntu/ai-agent-gateway
cat > /home/ubuntu/poc/gw <<'GW'
#!/bin/bash
# gw - shortcut for the POC's docker compose command, e.g.  ~/poc/gw ps   ~/poc/gw logs gateway
cd /home/ubuntu/ai-agent-gateway && exec docker compose --env-file .env -f deploy/docker-compose.yml -f deploy/docker-compose.poc.yml --profile analytics "$@"
GW
chmod +x /home/ubuntu/poc/gw
chown ubuntu:ubuntu /home/ubuntu/poc/gw
/home/ubuntu/poc/gw up --build -d
for _ in $(seq 1 120); do curl -sf localhost:8081/api/v1/health >/dev/null && break; sleep 5; done
curl -sf localhost:8081/api/v1/health
touch /home/ubuntu/poc/BOOTSTRAP_DONE
chown ubuntu:ubuntu /home/ubuntu/poc/BOOTSTRAP_DONE
echo "POC bootstrap finished"
