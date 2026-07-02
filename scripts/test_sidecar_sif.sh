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

"$APPTAINER_BIN" test "$IMAGE"

"$APPTAINER_BIN" exec \
  --cleanenv \
  --bind "$PWD":/work \
  "$IMAGE" \
  bash -lc 'cd /work && FFMPEG_BIN=/usr/bin/ffmpeg MESTIMATE_SIDECAR_BIN=/opt/mestimate-sidecar/bin/mestimate-sidecar tests/test_synthetic_translation.sh'
