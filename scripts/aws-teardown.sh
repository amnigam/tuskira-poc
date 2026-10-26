#!/usr/bin/env bash
# aws-teardown.sh - deletes everything aws-create.sh made. Run in CloudShell.
# Terminating the VM deletes its disk, including every key, credential and log on it.
# Copy the test results off the VM first if you want to keep them (docs/RUNBOOK.md, Phase 9).
set -euo pipefail
# shellcheck disable=SC1090
source ~/poc-ids.env
export AWS_REGION
read -r -p "Terminate $IID and delete the POC role, profile and security group? [y/N] " ok
[[ "$ok" == [yY] ]] || exit 0

aws ec2 terminate-instances --instance-ids "$IID" --query 'TerminatingInstances[0].CurrentState.Name' --output text
aws ec2 wait instance-terminated --instance-ids "$IID" && echo "VM terminated"
aws ec2 delete-security-group --group-id "$SG" && echo "security group deleted"
aws iam remove-role-from-instance-profile --instance-profile-name poc-gateway-profile --role-name poc-gateway-role || true
aws iam delete-instance-profile --instance-profile-name poc-gateway-profile || true
for p in $(aws iam list-role-policies --role-name poc-gateway-role --query 'PolicyNames[]' --output text); do
  aws iam delete-role-policy --role-name poc-gateway-role --policy-name "$p"
done
aws iam detach-role-policy --role-name poc-gateway-role \
  --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore || true
aws iam delete-role --role-name poc-gateway-role && echo "role deleted"
echo "Done. Keep your AWS budget alert; it costs nothing."
