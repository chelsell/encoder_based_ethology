#!/usr/bin/env bash
#SBATCH --job-name=mestimate-sidecar
#SBATCH --cpus-per-task=2
#SBATCH --mem=4G
#SBATCH --time=02:00:00
#SBATCH --output=slurm_logs/mestimate-sidecar_%A_%a.out
#SBATCH --error=slurm_logs/mestimate-sidecar_%A_%a.err
#
# Set the array range at submit time:
#
#   sbatch --array=1-N scripts/sidecar_array.sbatch

set -euo pipefail

REPO_DIR="${REPO_DIR:-$SLURM_SUBMIT_DIR}"
MANIFEST="${MANIFEST:?Set MANIFEST to the sidecar Slurm manifest CSV.}"
IMAGE="${IMAGE:?Set IMAGE to the Apptainer .sif or sandbox path.}"
APPTAINER_BIN="${APPTAINER_BIN:-apptainer}"

cd "$REPO_DIR"
mkdir -p slurm_logs

python3 scripts/run_sidecar_manifest_task.py \
  --manifest "$MANIFEST" \
  --image "$IMAGE" \
  --apptainer-bin "$APPTAINER_BIN" \
  ${SIDECAR_EXTRA_BIND_ARGS:-}
