#!/usr/bin/env bash
set -euo pipefail

INPUT="${1:-data/A07.mp4}"
OUTPUT_DIR="${2:-outputs/$(basename "${INPUT%.*}")_mb16_epzs_sp12}"

./build/mestimate-sidecar \
  --input "$INPUT" \
  --output-dir "$OUTPUT_DIR" \
  --method epzs \
  --mb-size 16 \
  --search-param 12
