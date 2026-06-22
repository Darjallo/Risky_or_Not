#!/usr/bin/env bash
set -euo pipefail

# flow_log.sh
#
# Dump recent logs for the flow runner (ethelflow) and common downstream services.
# Includes --previous logs to catch crashloops / restarts.
#
# Usage:
#   ./flow_log.sh
#   SINCE=10m TAIL=800 ./flow_log.sh
#   SERVICES="ethelflow file-to-images store-images" ./flow_log.sh
#
# Env vars:
#   NAMESPACE=default
#   SINCE=5m
#   TAIL=300
#   SERVICES="..."   (space-separated deployments; defaults include common ones)

NAMESPACE="${NAMESPACE:-default}"
SINCE="${SINCE:-5m}"
TAIL="${TAIL:-300}"

DEFAULT_SERVICES="ethelflow reasoning executor intent \
file-to-text store-text chunk-text store-chunks embedding store-vectors \
search-vectors retrieve-chunks \
file-to-images store-images"

SERVICES="${SERVICES:-$DEFAULT_SERVICES}"

log() { echo "[$(date +'%H:%M:%S')] $*"; }

have_deploy() {
  microk8s kubectl -n "$NAMESPACE" get deploy "$1" >/dev/null 2>&1
}

print_logs() {
  local d="$1"

  if ! have_deploy "$d"; then
    log "SKIP deploy/$d (not found in ns/$NAMESPACE)"
    return 0
  fi

  echo
  echo "================================================================================"
  echo "DEPLOYMENT: $d   (ns=$NAMESPACE)   since=$SINCE   tail=$TAIL"
  echo "--------------------------------------------------------------------------------"

  # Current logs
  log "kubectl logs deploy/$d"
  microk8s kubectl -n "$NAMESPACE" logs "deploy/$d" --since="$SINCE" --tail="$TAIL" || true

  # Previous container logs (helpful for CrashLoopBackOff / restarts)
  echo
  log "kubectl logs deploy/$d --previous (if available)"
  microk8s kubectl -n "$NAMESPACE" logs "deploy/$d" --previous --since="$SINCE" --tail="$TAIL" 2>/dev/null || true
}

# Optional: show running pods for visibility
echo "NAMESPACE=$NAMESPACE  SINCE=$SINCE  TAIL=$TAIL"
echo "SERVICES=$SERVICES"
echo
log "Pods snapshot:"
microk8s kubectl -n "$NAMESPACE" get pods -o wide || true

# Print logs for each service
for svc in $SERVICES; do
  print_logs "$svc"
done

echo
log "Done."

