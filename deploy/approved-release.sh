#!/usr/bin/env bash
# Sourced by both timer and manual deployments. Default is fail-closed production.
ROLE=${PARKING_DEPLOY_ROLE:-production}
case "$ROLE" in
  staging) ;;
  production)
    APPROVAL=${PARKING_APPROVED_RELEASE_FILE:-/etc/parking/approved-release}
    if [ ! -r "$APPROVAL" ]; then
      echo "No staging-approved production release: $APPROVAL" >&2
      exit 1
    fi
    APPROVED=$(tr -d '\r\n' < "$APPROVAL")
    [[ "$APPROVED" =~ ^[0-9a-f]{40}$ ]] || { echo "Invalid approved commit" >&2; exit 1; }
    if [ -n "${PARKING_DEPLOY_TARGET:-}" ] && [ "$PARKING_DEPLOY_TARGET" != "$APPROVED" ]; then
      echo "Target differs from staging-approved release" >&2
      exit 1
    fi
    export PARKING_DEPLOY_TARGET="$APPROVED"
    ;;
  *) echo "PARKING_DEPLOY_ROLE must be staging or production" >&2; exit 1 ;;
esac
