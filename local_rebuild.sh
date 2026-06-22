#!/usr/bin/env bash
set -euo pipefail

# local_rebuild.sh
#
# Build + import a local image into MicroK8s containerd and restart deployments so they pick it up.
# Key behavior:
#   - Always tags the build as ethelflow:latest (plus an optional timestamp tag)
#   - Imports ethelflow:latest into MicroK8s containerd
#   - Rollout-restarts selected deployments (so pods actually reload the refreshed image)
#
# Usage:
#   ./local_rebuild.sh
#
# Optional env vars:
#   NAMESPACE=default
#   DOCKERFILE=ethelflow.Dockerfile
#   IMAGE_REPO=ethelflow
#   TAG=20251230-184408            (optional; if unset, script uses current datetime)
#   DEPLOYMENTS="ethelflow store-chunks store-text store-vectors chunk-text file-to-text embedding reasoning executor"
#   RESTART_ONLY=0                 (set to 1 to skip build/import and only restart)
#
# Requirements:
#   docker, microk8s
#
# Notes:
#   - This script assumes your k8s YAMLs use image: ethelflow:latest (recommended for local dev).
#   - If your YAML uses explicit tags, you can still use this script, but you’d need to update those tags.

NAMESPACE="${NAMESPACE:-default}"
DOCKERFILE="${DOCKERFILE:-ethelflow.Dockerfile}"
IMAGE_REPO="${IMAGE_REPO:-ethelflow}"
TAG="${TAG:-$(date +%Y%m%d-%H%M%S)}"
DEPLOYMENTS="${DEPLOYMENTS:-ethelflow store-chunks store-text store-images store-vectors chunk-text file-to-text file-to-images intent embedding reasoning executor search-vectors retrieve-chunks}"
RESTART_ONLY="${RESTART_ONLY:-0}"

log() { echo "[$(date +'%H:%M:%S')] $*"; }

need() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: missing command: $1" >&2; exit 1; }
}

need docker
need microk8s

if [[ ! -f "$DOCKERFILE" ]]; then
  echo "ERROR: Dockerfile not found: $DOCKERFILE" >&2
  exit 1
fi

# Resolve deployments list into array
read -r -a DEPLOY_ARR <<<"$DEPLOYMENTS"

# Helper: does deployment exist?
deploy_exists() {
  microk8s kubectl -n "$NAMESPACE" get deploy "$1" >/dev/null 2>&1
}

# Helper: detect first container name
container_name() {
  microk8s kubectl -n "$NAMESPACE" get deploy "$1" -o jsonpath='{.spec.template.spec.containers[0].name}'
}

if [[ "$RESTART_ONLY" != "1" ]]; then
  # Build with a timestamp tag and also tag as :latest (so all services using latest stay current)
  IMAGE_TAGGED="${IMAGE_REPO}:${TAG}"
  IMAGE_LATEST="${IMAGE_REPO}:latest"

  log "Building image: ${IMAGE_TAGGED}"
  sudo docker build -f "$DOCKERFILE" -t "$IMAGE_TAGGED" .

  log "Tagging as ${IMAGE_LATEST}"
  sudo docker tag "$IMAGE_TAGGED" "$IMAGE_LATEST"

  # Import :latest into microk8s containerd (this is what k8s will run for local images)
  log "Importing ${IMAGE_LATEST} into MicroK8s containerd"
  sudo docker save "$IMAGE_LATEST" | sudo microk8s ctr image import -
else
  log "RESTART_ONLY=1 -> skipping build/import"
fi

# Restart deployments so they pick up the refreshed local image
for d in "${DEPLOY_ARR[@]}"; do
  if deploy_exists "$d"; then
    cname="$(container_name "$d")"
    if [[ -z "$cname" ]]; then
      echo "ERROR: could not determine container name for deploy/$d" >&2
      exit 1
    fi

    # Ensure they point at :latest (safe even if already set)
    log "Setting deploy/$d container/$cname image -> ${IMAGE_REPO}:latest"
    microk8s kubectl -n "$NAMESPACE" set image "deploy/$d" "$cname=${IMAGE_REPO}:latest" >/dev/null

    log "Rollout restart deploy/$d"
    microk8s kubectl -n "$NAMESPACE" rollout restart "deploy/$d" >/dev/null

    log "Waiting for deploy/$d rollout..."
    microk8s kubectl -n "$NAMESPACE" rollout status "deploy/$d"
  else
    log "Skipping deploy/$d (not found in ns/$NAMESPACE)"
  fi
done

log "Done."

