#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

TMP_DIR="${TMP_DIR:-/tmp/mestimate-sidecar-test}"
VIDEO="$TMP_DIR/synthetic_translation.mkv"
OUT="$TMP_DIR/sidecar"
SAMPLED_OUT="$TMP_DIR/sidecar_sampled"
NO_VECTOR_OUT="$TMP_DIR/sidecar_no_vectors"
BIN_OUT="$TMP_DIR/sidecar_binary_frames"
BIN_VECTOR_OUT="$TMP_DIR/sidecar_binary_vectors"
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

"$MESTIMATE_SIDECAR_BIN" \
  --input "$VIDEO" \
  --output-dir "$SAMPLED_OUT" \
  --method epzs \
  --mb-size 16 \
  --search-param 12 \
  --vector-output sampled \
  --vector-source past \
  --vector-frame-stride 2 \
  --vector-spatial-stride 2 \
  --vector-min-magnitude 0.25

python3 tests/test_schema.py \
  --frames "$SAMPLED_OUT/synthetic_translation.mestimate-v1.frames.csv.gz" \
  --vectors "$SAMPLED_OUT/synthetic_translation.mestimate-v1.vectors.csv.gz" \
  --metadata "$SAMPLED_OUT/synthetic_translation.mestimate-v1.metadata.json" \
  --expected-frames 49 \
  --allow-sampled-vectors

python3 - "$OUT/synthetic_translation.mestimate-v1.metadata.json" "$SAMPLED_OUT/synthetic_translation.mestimate-v1.metadata.json" <<'PY'
import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    full = json.load(f)
with open(sys.argv[2], "r", encoding="utf-8") as f:
    sampled = json.load(f)

assert sampled["outputs"]["raw_vector_row_count"] == full["outputs"]["raw_vector_row_count"]
assert sampled["outputs"]["vector_row_count"] < full["outputs"]["vector_row_count"]
assert sampled["outputs"]["frame_row_count"] == full["outputs"]["frame_row_count"]
assert sampled["vector_sampling"]["output"] == "sampled"
assert sampled["vector_sampling"]["source"] == "past"
assert sampled["vector_sampling"]["frame_stride"] == 2
assert sampled["vector_sampling"]["spatial_stride"] == 2
PY

"$MESTIMATE_SIDECAR_BIN" \
  --input "$VIDEO" \
  --output-dir "$NO_VECTOR_OUT" \
  --method epzs \
  --mb-size 16 \
  --search-param 12 \
  --vector-output none

python3 - "$OUT/synthetic_translation.mestimate-v1.metadata.json" "$NO_VECTOR_OUT/synthetic_translation.mestimate-v1.metadata.json" "$NO_VECTOR_OUT/synthetic_translation.mestimate-v1.frames.csv.gz" "$NO_VECTOR_OUT/synthetic_translation.mestimate-v1.vectors.csv.gz" <<'PY'
import csv
import gzip
import json
import pathlib
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    full = json.load(f)
with open(sys.argv[2], "r", encoding="utf-8") as f:
    no_vectors = json.load(f)

with gzip.open(sys.argv[3], "rt", newline="") as f:
    rows = list(csv.DictReader(f))

assert len(rows) == full["outputs"]["frame_row_count"]
assert no_vectors["outputs"]["raw_vector_row_count"] == full["outputs"]["raw_vector_row_count"]
assert no_vectors["outputs"]["vector_row_count"] == 0
assert no_vectors["outputs"]["vectors_file"] is None
assert no_vectors["outputs"]["vectors_sha256"] is None
assert no_vectors["vector_sampling"]["output"] == "none"
assert not pathlib.Path(sys.argv[4]).exists()
PY

"$MESTIMATE_SIDECAR_BIN" \
  --input "$VIDEO" \
  --output-dir "$BIN_OUT" \
  --method epzs \
  --mb-size 16 \
  --search-param 12 \
  --frame-output bin \
  --vector-output none

python3 - "$OUT/synthetic_translation.mestimate-v1.metadata.json" "$BIN_OUT/synthetic_translation.mestimate-v1.metadata.json" "$BIN_OUT/synthetic_translation.mestimate-v1.frames.bin.gz" "$BIN_OUT/synthetic_translation.mestimate-v1.vectors.csv.gz" <<'PY'
import json
import pathlib
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    full = json.load(f)
with open(sys.argv[2], "r", encoding="utf-8") as f:
    binary = json.load(f)

assert binary["outputs"]["raw_vector_row_count"] == full["outputs"]["raw_vector_row_count"]
assert binary["outputs"]["frame_row_count"] == full["outputs"]["frame_row_count"]
assert binary["outputs"]["frames_file"].endswith(".frames.bin.gz")
assert binary["frame_summary_encoding"]["format"] == "bin.gz"
assert binary["frame_summary_encoding"]["binary_header_size"] == 32
assert binary["frame_summary_encoding"]["binary_record_size"] == 92
assert binary["frame_summary_encoding"]["binary_float_type"] == "float32"
assert pathlib.Path(sys.argv[3]).exists()
assert not pathlib.Path(sys.argv[4]).exists()
PY

python3 tests/test_schema.py \
  --frames "$BIN_OUT/synthetic_translation.mestimate-v1.frames.bin.gz" \
  --vectors "$OUT/synthetic_translation.mestimate-v1.vectors.csv.gz" \
  --metadata "$BIN_OUT/synthetic_translation.mestimate-v1.metadata.json" \
  --expected-frames 49 \
  --allow-sampled-vectors

"$MESTIMATE_SIDECAR_BIN" \
  --input "$VIDEO" \
  --output-dir "$BIN_VECTOR_OUT" \
  --method epzs \
  --mb-size 16 \
  --search-param 12 \
  --vector-format bin

python3 tests/test_schema.py \
  --frames "$BIN_VECTOR_OUT/synthetic_translation.mestimate-v1.frames.csv.gz" \
  --vectors "$BIN_VECTOR_OUT/synthetic_translation.mestimate-v1.vectors.bin.gz" \
  --metadata "$BIN_VECTOR_OUT/synthetic_translation.mestimate-v1.metadata.json" \
  --expected-frames 49

python3 - "$OUT/synthetic_translation.mestimate-v1.metadata.json" "$BIN_VECTOR_OUT/synthetic_translation.mestimate-v1.metadata.json" "$OUT/synthetic_translation.mestimate-v1.vectors.csv.gz" "$BIN_VECTOR_OUT/synthetic_translation.mestimate-v1.vectors.bin.gz" <<'PY'
import gzip
import json
import pathlib
import sys

with open(sys.argv[1], "r", encoding="utf-8") as f:
    csv_meta = json.load(f)
with open(sys.argv[2], "r", encoding="utf-8") as f:
    bin_meta = json.load(f)

assert bin_meta["outputs"]["vector_row_count"] == csv_meta["outputs"]["vector_row_count"]
assert bin_meta["outputs"]["vectors_file"].endswith(".vectors.bin.gz")
assert bin_meta["vector_encoding"]["format"] == "bin.gz"
assert bin_meta["vector_encoding"]["binary_record_size"] == 76

csv_size = pathlib.Path(sys.argv[3]).stat().st_size
bin_size = pathlib.Path(sys.argv[4]).stat().st_size
with gzip.open(sys.argv[4], "rb") as f:
    header = f.read(32)
assert header.startswith(b"MSCVB1\0\0")
assert bin_size > 0
print(f"csv_vector_bytes={csv_size} binary_vector_bytes={bin_size}")
PY
