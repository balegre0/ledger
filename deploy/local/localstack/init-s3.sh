#!/bin/sh
# The local stack's legal archive (§6.8): an S3 bucket made as production's is, with object lock enabled (so
# versioning too) and a default retention in compliance mode. LocalStack runs this once S3 is ready
# (/etc/localstack/init/ready.d). The store sets each object's own retention (LEDGER_ARCHIVE_RETENTION_YEARS
# at least); the default holds an object written without one. LocalStack keeps nothing past its container:
# `docker compose down` forgets the bucket and every object, whatever their locks say.
set -eu

bucket=ledger-archive
if awslocal s3api head-bucket --bucket "$bucket" >/dev/null 2>&1; then
    exit 0  # made by an earlier run of this container
fi
awslocal s3api create-bucket --bucket "$bucket" --object-lock-enabled-for-bucket >/dev/null
awslocal s3api put-object-lock-configuration --bucket "$bucket" --object-lock-configuration \
    '{"ObjectLockEnabled": "Enabled", "Rule": {"DefaultRetention": {"Mode": "COMPLIANCE", "Years": 10}}}'
echo "bucket $bucket: object lock enabled, default retention 10 years in compliance mode"
