#!/usr/bin/env bash
# aws-create.sh - creates every AWS resource the POC needs, then launches the VM.
# Run it in AWS CloudShell (or any shell with the AWS CLI signed in as an admin user),
# from the root of this repo, AFTER scripts/build-user-data.sh.
#
# Creates:
#   IAM role poc-gateway-role (trusted by EC2) + AmazonSSMManagedInstanceCore
#   inline policy = the model allow-list (one model, through one inference profile)
#   instance profile poc-gateway-profile
#   security group poc-gateway-sg with NO inbound rules (default VPC)
#   EC2 t3.large, Ubuntu 24.04, IMDSv2 with hop limit 2, 30 GB encrypted gp3, tag ai-gateway-poc
# Saves IID and SG to ~/poc-ids.env for scripts/aws-teardown.sh.
#
# Settings (environment variables, all optional):
#   AWS_REGION    default ap-south-1
#   MODEL_FAMILY  nova (default) or claude
#                 nova   -> Amazon Nova Lite via the APAC profile: no Marketplace subscription needed
#                 claude -> Claude Haiku 4.5 via the global profile: needs the Anthropic use-case
#                           form and a working Marketplace subscription (see docs/RUNBOOK.md)
#   INSTANCE_TYPE default t3.large
set -euo pipefail
cd "$(dirname "$0")/.."

export AWS_REGION=${AWS_REGION:-ap-south-1}
MODEL_FAMILY=${MODEL_FAMILY:-nova}
INSTANCE_TYPE=${INSTANCE_TYPE:-t3.large}
ROLE=poc-gateway-role
IPROFILE=poc-gateway-profile

case "$MODEL_FAMILY" in
  nova)   TEMPLATE=aws/iam/bedrock-nova-lite.json.tmpl; POLICY=bedrock-nova-lite-only
          PROFILE_ID=${PROFILE_ID:-apac.amazon.nova-lite-v1:0}; MODEL=amazon.nova-lite-v1:0 ;;
  claude) TEMPLATE=aws/iam/bedrock-claude-haiku-global.json.tmpl; POLICY=bedrock-haiku-only
          PROFILE_ID=${PROFILE_ID:-global.anthropic.claude-haiku-4-5-20251001-v1:0}; MODEL=anthropic.claude-haiku-4-5-20251001-v1:0 ;;
  *) echo "MODEL_FAMILY must be nova or claude" >&2; exit 1 ;;
esac

[[ -f dist/poc-user-data.sh ]] || { echo "Run scripts/build-user-data.sh first." >&2; exit 1; }
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
echo "Account $ACCOUNT, region $AWS_REGION, model profile $PROFILE_ID"

echo "1/6 IAM role $ROLE"
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document file://aws/iam/trust-ec2.json \
    --query Role.Arn --output text
fi
aws iam attach-role-policy --role-name "$ROLE" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore

echo "2/6 Model allow-list: inline policy $POLICY"
sed -e "s|__REGION__|$AWS_REGION|g" -e "s|__ACCOUNT__|$ACCOUNT|g" \
    -e "s|__PROFILE_ID__|$PROFILE_ID|g" -e "s|__MODEL__|$MODEL|g" "$TEMPLATE" > dist/bedrock-policy.json
aws iam put-role-policy --role-name "$ROLE" --policy-name "$POLICY" --policy-document file://dist/bedrock-policy.json

echo "3/6 Instance profile $IPROFILE"
if ! aws iam get-instance-profile --instance-profile-name "$IPROFILE" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$IPROFILE" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$IPROFILE" --role-name "$ROLE"
  sleep 15   # a new instance profile takes a few seconds to become usable by EC2
fi

echo "4/6 Security group with no inbound rules"
VPC=$(aws ec2 describe-vpcs --filters Name=is-default,Values=true --query 'Vpcs[0].VpcId' --output text)
[[ "$VPC" != "None" ]] || { echo "No default VPC in $AWS_REGION: run 'aws ec2 create-default-vpc' and retry." >&2; exit 1; }
SG=$(aws ec2 describe-security-groups --filters Name=group-name,Values=poc-gateway-sg Name=vpc-id,Values="$VPC" \
  --query 'SecurityGroups[0].GroupId' --output text)
if [[ "$SG" == "None" ]]; then
  SG=$(aws ec2 create-security-group --group-name poc-gateway-sg --description "AI gateway POC - no inbound" \
    --vpc-id "$VPC" --query GroupId --output text)
fi
echo "    $SG inbound rules: $(aws ec2 describe-security-groups --group-ids "$SG" --query 'length(SecurityGroups[0].IpPermissions)')"

echo "5/6 Launching the VM (Ubuntu 24.04, $INSTANCE_TYPE)"
AMI=$(aws ssm get-parameter --name /aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --query Parameter.Value --output text)
IID=$(aws ec2 run-instances --image-id "$AMI" --instance-type "$INSTANCE_TYPE" \
  --iam-instance-profile Name="$IPROFILE" --security-group-ids "$SG" \
  --metadata-options HttpTokens=required,HttpPutResponseHopLimit=2,HttpEndpoint=enabled \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":30,"VolumeType":"gp3","Encrypted":true}}]' \
  --user-data file://dist/poc-user-data.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=ai-gateway-poc}]' \
  --query 'Instances[0].InstanceId' --output text)
printf 'IID=%s\nSG=%s\nAWS_REGION=%s\nPOLICY=%s\n' "$IID" "$SG" "$AWS_REGION" "$POLICY" > ~/poc-ids.env
aws ec2 wait instance-running --instance-ids "$IID"
echo "    $IID is running (ids saved to ~/poc-ids.env)"

echo "6/6 Waiting for Session Manager to see the VM (up to 5 minutes)"
for _ in $(seq 1 30); do
  st=$(aws ssm describe-instance-information --filters Key=InstanceIds,Values="$IID" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || true)
  [[ "$st" == "Online" ]] && break
  sleep 10
done
echo "    Session Manager status: ${st:-unknown}"
echo
echo "Done. The VM is now installing everything (15-25 minutes)."
echo "Next: docs/RUNBOOK.md, Phase 3 (connect with Session Manager and watch /var/log/poc-bootstrap.log)."
