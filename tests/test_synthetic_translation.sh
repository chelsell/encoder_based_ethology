#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TMP_DIR="${TMP_DIR:-/tmp/mestimate-sidecar-test}"
VIDEO="$TMP_DIR/synthetic_translation.mkv"
OUT="$TMP_DIR/sidecar"
MESTIMATE_SIDECAR_BIN="${MESTIMATE_SIDECAR_BIN:-./build/mestimate-sidecar}"

rm -rf "$TMP_DIR"
mkdir -p "$TMP_DIR"

python3 tests/make_synthetic_translation.py --output "$VIDEO"

"$MESTIMATE_SIDECAR_BIN" \
  --input "$VIDEO" \
  --output-dir "$OUT" \
  --method epzs \
  --mb-size 16 \
  --search-param 12

python3 tests/test_schema.py \
  --frames "$OUT/synthetic_translation.mestimate-v1.frames.csv.gz" \
  --vectors "$OUT/synthetic_translation.mestimate-v1.vectors.csv.gz" \
  --metadata "$OUT/synthetic_translation.mestimate-v1.metadata.json" \
  --expected-frames 49
