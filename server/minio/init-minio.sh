#!/bin/sh
# One-shot MinIO (PGSTY Silo) setup, safe to re-run:
#  - private bucket for OfinTech Desk files (documents, imports, generated workbooks and reports), with versioning
#    (deleted/overwritten objects are kept for NONCURRENT_DAYS, default 90)
#  - least-privilege application user (S3_ACCESS_KEY) that can only read,
#    write, list and delete objects in that bucket
# The API never uses the root credentials.
set -eu
: "${MINIO_ROOT_USER:?}" "${MINIO_ROOT_PASSWORD:?}" "${S3_BUCKET:?}" "${S3_ACCESS_KEY:?}" "${S3_SECRET_KEY:?}"
MC="${MC:-mcli}"
ENDPOINT="${MINIO_ENDPOINT:-http://minio:9000}"
DAYS="${NONCURRENT_DAYS:-90}"
export MC_CONFIG_DIR="${MC_CONFIG_DIR:-/tmp/.mcli}"
TMP="${TMPDIR:-/tmp}"

if [ "$S3_ACCESS_KEY" = "$MINIO_ROOT_USER" ]; then
  echo "[minio-init] S3_ACCESS_KEY must differ from MINIO_ROOT_USER (the app must not use root)" >&2
  exit 1
fi

tries=0
until "$MC" alias set ofx "$ENDPOINT" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null 2>&1; do
  tries=$((tries + 1))
  if [ "$tries" -ge 60 ]; then
    echo "[minio-init] cannot reach $ENDPOINT with the root credentials" >&2
    exit 1
  fi
  sleep 2
done

echo "[minio-init] bucket $S3_BUCKET"
"$MC" mb --ignore-existing "ofx/$S3_BUCKET"
"$MC" anonymous set none "ofx/$S3_BUCKET" >/dev/null
"$MC" version enable "ofx/$S3_BUCKET" >/dev/null
cat > "$TMP/ofx-lifecycle.json" <<JSON
{"Rules":[{"ID":"expire-noncurrent","Status":"Enabled","Filter":{"Prefix":""},
 "NoncurrentVersionExpiration":{"NoncurrentDays":$DAYS},
 "AbortIncompleteMultipartUpload":{"DaysAfterInitiation":7}}]}
JSON
"$MC" ilm import "ofx/$S3_BUCKET" < "$TMP/ofx-lifecycle.json" >/dev/null

echo "[minio-init] application user and policy"
cat > "$TMP/ofx-policy.json" <<JSON
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["s3:GetBucketLocation","s3:ListBucket","s3:ListBucketMultipartUploads"],
  "Resource":["arn:aws:s3:::$S3_BUCKET"]},
 {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject","s3:AbortMultipartUpload","s3:ListMultipartUploadParts"],
  "Resource":["arn:aws:s3:::$S3_BUCKET/*"]}]}
JSON
"$MC" admin policy create ofx ofintech-desk-app "$TMP/ofx-policy.json" >/dev/null
"$MC" admin user add ofx "$S3_ACCESS_KEY" "$S3_SECRET_KEY" >/dev/null
if ! out=$("$MC" admin policy attach ofx ofintech-desk-app --user "$S3_ACCESS_KEY" 2>&1); then
  case "$out" in
    *"already"*) ;;  # re-run: policy is already attached
    *) echo "$out" >&2; exit 1 ;;
  esac
fi
rm -f "$TMP/ofx-policy.json" "$TMP/ofx-lifecycle.json"
echo "[minio-init] done"
