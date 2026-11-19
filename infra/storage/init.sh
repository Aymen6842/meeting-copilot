#!/bin/sh
# One-shot storage setup, run by the storage-init service on every `docker compose up`.
# Every step is idempotent, and re-running it rotates the app secret to the value in .env.
#
# Uses the root credentials, which only this script and the storage server receive. The API
# and worker get their own user, limited to the bucket by app-policy.json (ADR-0006).
set -eu

: "${STORAGE_ENDPOINT:?}" "${STORAGE_BUCKET:?}" "${STORAGE_ROOT_USER:?}" "${STORAGE_ROOT_PASSWORD:?}"
: "${STORAGE_APP_ACCESS_KEY:?}" "${STORAGE_APP_SECRET_KEY:?}"

POLICY_NAME="meeting-copilot-app"
POLICY_TEMPLATE="$(dirname "$0")/app-policy.json"

case "$STORAGE_BUCKET" in
  *[!a-z0-9.-]*) echo "STORAGE_BUCKET may contain only a-z, 0-9, '.' and '-'" >&2; exit 1 ;;
esac
if [ "$STORAGE_APP_ACCESS_KEY" = "$STORAGE_ROOT_USER" ]; then
  echo "STORAGE_APP_ACCESS_KEY must differ from STORAGE_ROOT_USER" >&2
  exit 1
fi

policy_file="$(mktemp)"
sed "s/__BUCKET__/$STORAGE_BUCKET/g" "$POLICY_TEMPLATE" > "$policy_file"

rc alias set root "$STORAGE_ENDPOINT" "$STORAGE_ROOT_USER" "$STORAGE_ROOT_PASSWORD" > /dev/null
rc bucket create "root/$STORAGE_BUCKET" > /dev/null
rc admin policy create root "$POLICY_NAME" "$policy_file" > /dev/null
rc admin user add root "$STORAGE_APP_ACCESS_KEY" "$STORAGE_APP_SECRET_KEY" > /dev/null
rc admin policy attach root "$POLICY_NAME" --user "$STORAGE_APP_ACCESS_KEY" > /dev/null

echo "bucket $STORAGE_BUCKET is ready; app user has policy $POLICY_NAME"
