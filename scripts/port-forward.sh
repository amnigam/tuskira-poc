#!/usr/bin/env bash
# port-forward.sh - brings a port on the POC VM to the same port on this machine,
# through Session Manager. Nothing is opened on AWS. Leave it running; Ctrl+C to stop.
#
# Usage:   scripts/port-forward.sh            console  -> http://localhost:8081
#          scripts/port-forward.sh 8090       Adminer (database browser)
#          scripts/port-forward.sh 8099       a temporary file server (see docs/RUNBOOK.md, Phase 9)
# Needs:   AWS CLI v2 + the Session Manager plugin, signed in (e.g. AWS_PROFILE=poc)
set -euo pipefail
PORT=${1:-8081}
REGION=${AWS_REGION:-ap-south-1}
IID=$(aws ec2 describe-instances --region "$REGION" \
  --filters Name=tag:Name,Values=ai-gateway-poc Name=instance-state-name,Values=running \
  --query 'Reservations[0].Instances[0].InstanceId' --output text)
[[ "$IID" != "None" ]] || { echo "No running instance tagged ai-gateway-poc in $REGION." >&2; exit 1; }
echo "Forwarding localhost:$PORT -> $IID:$PORT"
aws ssm start-session --region "$REGION" --target "$IID" --document-name AWS-StartPortForwardingSession \
  --parameters "{\"portNumber\":[\"$PORT\"],\"localPortNumber\":[\"$PORT\"]}"
