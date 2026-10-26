#!/usr/bin/env bash
# send-file-to-vm.sh - gets a file from your laptop/CloudShell onto the POC VM.
#
# Session Manager has no file upload, so this puts the file in a private S3 bucket in
# your account and prints a one-hour download link plus the exact command to paste
# on the VM. The VM needs no S3 permissions for this.
#
# Usage:   scripts/send-file-to-vm.sh dist/poc-tests.tgz
#          scripts/send-file-to-vm.sh --cleanup        (deletes the bucket when you are done)
# Needs:   AWS CLI signed in (e.g. AWS_PROFILE=poc), AWS_REGION (default ap-south-1)
set -euo pipefail
REGION=${AWS_REGION:-ap-south-1}
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
BUCKET="poc-gw-files-$ACCOUNT-$REGION"

if [[ "${1:-}" == "--cleanup" ]]; then
  aws s3 rb "s3://$BUCKET" --force --region "$REGION"
  exit 0
fi
FILE=${1:?usage: send-file-to-vm.sh FILE}
NAME=$(basename "$FILE")

if ! aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
  aws s3 mb "s3://$BUCKET" --region "$REGION" >/dev/null
fi
aws s3 cp "$FILE" "s3://$BUCKET/$NAME" --region "$REGION" >/dev/null
URL=$(aws s3 presign "s3://$BUCKET/$NAME" --expires-in 3600 --region "$REGION")
SUM=$(sha256sum "$FILE" 2>/dev/null || shasum -a 256 "$FILE")

cat <<MSG
Uploaded. Paste this on the VM (as the ubuntu user). The single quotes matter: the
link contains '&' characters, which bash would otherwise treat as commands.

cd ~/poc && curl -fsSL -o '$NAME' '$URL' && sha256sum '$NAME'

Expected checksum: ${SUM%% *}
The link expires in one hour. Delete the bucket afterwards: scripts/send-file-to-vm.sh --cleanup
MSG
