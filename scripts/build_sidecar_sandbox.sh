#!/usr/bin/env bash
set -euo pipefail

SANDBOX="${1:-mestimate_sidecar.sandbox}"
DEF="${DEF:-mestimate_sidecar.def}"
APPTAINER_BIN="${APPTAINER_BIN:-apptainer}"
APPTAINER_BUILD_FLAGS="${APPTAINER_BUILD_FLAGS:-}"

if [[ -e "$SANDBOX" ]]; then
  echo "ERROR: sandbox already exists: $SANDBOX" >&2
  echo "Remove it or choose another output directory." >&2
  exit 2
fi

# shellcheck disable=SC2086
"$APPTAINER_BIN" build --sandbox $APPTAINER_BUILD_FLAGS "$SANDBOX" "$DEF"
