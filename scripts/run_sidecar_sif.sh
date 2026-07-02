#!/usr/bin/env bash
set -euo pipefail

IMAGE="${IMAGE:-mestimate_sidecar.sif}"
APPTAINER_BIN="${APPTAINER_BIN:-apptainer}"

if [[ ! -e "$IMAGE" ]]; then
  echo "ERROR: image not found: $IMAGE" >&2
  echo "Build a SIF with: scripts/build_sidecar_sif.sh $IMAGE" >&2
  echo "Or build a sandbox with: scripts/build_sidecar_sandbox.sh $IMAGE" >&2
  exit 2
fi

exec "$APPTAINER_BIN" run --cleanenv --bind "$PWD":/work "$IMAGE" "$@"
