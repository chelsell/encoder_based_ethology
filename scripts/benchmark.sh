#!/usr/bin/env bash
set -euo pipefail

INPUT="${1:?Usage: $0 INPUT_VIDEO [OUTPUT_DIR]}"
OUTPUT_DIR="${2:-outputs/benchmark_$(basename "${INPUT%.*}")}"
FFMPEG_BIN="${FFMPEG_BIN:-ffmpeg}"
SIDECAR_BIN="${SIDECAR_BIN:-./build-current/mestimate-sidecar}"
RUNS="${RUNS:-3}"
AV1_ENCODER="${AV1_ENCODER:-libaom-av1}"
AV1_CRF="${AV1_CRF:-35}"
AV1_PRESET="${AV1_PRESET:-8}"
SIDE_METHOD="${SIDE_METHOD:-epzs}"
SIDE_MB_SIZE="${SIDE_MB_SIZE:-16}"
SIDE_SEARCH_PARAM="${SIDE_SEARCH_PARAM:-12}"
FRAME_DIFF_THRESHOLD="${FRAME_DIFF_THRESHOLD:-10}"
SUMMARY_FLOAT_PRECISION="${SUMMARY_FLOAT_PRECISION:-6}"

mkdir -p "$OUTPUT_DIR"
rm -f "$OUTPUT_DIR/results.tsv" "$OUTPUT_DIR/summary.tsv"
printf "name\trun\twall_seconds\tuser_seconds\tsys_seconds\tmaxrss_kb\toutput_bytes\n" > "$OUTPUT_DIR/results.tsv"

av1_args=()
case "$AV1_ENCODER" in
  libaom-av1)
    av1_args=(-c:v "$AV1_ENCODER" -crf "$AV1_CRF" -cpu-used "$AV1_PRESET")
    ;;
  libsvtav1)
    av1_args=(-c:v "$AV1_ENCODER" -crf "$AV1_CRF" -preset "$AV1_PRESET")
    ;;
  *)
    av1_args=(-c:v "$AV1_ENCODER" -crf "$AV1_CRF")
    ;;
esac

measure() {
  local name="$1"
  local output_path="$2"
  shift 2
  echo "== $name =="
  for run in $(seq 1 "$RUNS"); do
    local time_file="$OUTPUT_DIR/${name}_${run}.time.txt"
    rm -rf "$output_path"
    /usr/bin/time -f "%e\t%U\t%S\t%M" -o "$time_file" "$@" \
      >"$OUTPUT_DIR/${name}_${run}.stdout.txt" \
      2>"$OUTPUT_DIR/${name}_${run}.stderr.txt"
    local bytes=0
    if [[ -e "$output_path" ]]; then
      bytes="$(du -sb "$output_path" | awk '{print $1}')"
    fi
    awk -v name="$name" -v run="$run" -v bytes="$bytes" 'BEGIN { OFS="\t" } { print name, run, $1, $2, $3, $4, bytes }' \
      "$time_file" | tee -a "$OUTPUT_DIR/results.tsv"
  done
}

measure ffmpeg_decode_only "$OUTPUT_DIR/null.out" \
  "$FFMPEG_BIN" -hide_banner -nostdin -v error \
  -i "$INPUT" -f null -

measure av1_transcode "$OUTPUT_DIR/archive_av1.mkv" \
  "$FFMPEG_BIN" -hide_banner -nostdin -v error -y \
  -i "$INPUT" -map 0:v:0 -an \
  -vf "format=gray" \
  "${av1_args[@]}" \
  "$OUTPUT_DIR/archive_av1.mkv"

measure sidecar_summary_csv "$OUTPUT_DIR/sidecar_summary_csv" \
  "$SIDECAR_BIN" \
  --input "$INPUT" \
  --output-dir "$OUTPUT_DIR/sidecar_summary_csv" \
  --method "$SIDE_METHOD" \
  --mb-size "$SIDE_MB_SIZE" \
  --search-param "$SIDE_SEARCH_PARAM" \
  --frame-diff-threshold "$FRAME_DIFF_THRESHOLD" \
  --frame-output csv \
  --summary-float-precision "$SUMMARY_FLOAT_PRECISION" \
  --vector-output none \
  --force

measure sidecar_summary_bin "$OUTPUT_DIR/sidecar_summary_bin" \
  "$SIDECAR_BIN" \
  --input "$INPUT" \
  --output-dir "$OUTPUT_DIR/sidecar_summary_bin" \
  --method "$SIDE_METHOD" \
  --mb-size "$SIDE_MB_SIZE" \
  --search-param "$SIDE_SEARCH_PARAM" \
  --frame-diff-threshold "$FRAME_DIFF_THRESHOLD" \
  --frame-output bin \
  --summary-float-precision "$SUMMARY_FLOAT_PRECISION" \
  --vector-output none \
  --force

measure sidecar_sampled_vectors "$OUTPUT_DIR/sidecar_sampled_vectors" \
  "$SIDECAR_BIN" \
  --input "$INPUT" \
  --output-dir "$OUTPUT_DIR/sidecar_sampled_vectors" \
  --method "$SIDE_METHOD" \
  --mb-size "$SIDE_MB_SIZE" \
  --search-param "$SIDE_SEARCH_PARAM" \
  --frame-diff-threshold "$FRAME_DIFF_THRESHOLD" \
  --frame-output bin \
  --summary-float-precision "$SUMMARY_FLOAT_PRECISION" \
  --vector-output sampled \
  --vector-format bin \
  --vector-source past \
  --vector-frame-stride 5 \
  --vector-spatial-stride 2 \
  --vector-min-magnitude 0.25 \
  --force

measure sidecar_full_vectors_csv "$OUTPUT_DIR/sidecar_full_vectors_csv" \
  "$SIDECAR_BIN" \
  --input "$INPUT" \
  --output-dir "$OUTPUT_DIR/sidecar_full_vectors_csv" \
  --method "$SIDE_METHOD" \
  --mb-size "$SIDE_MB_SIZE" \
  --search-param "$SIDE_SEARCH_PARAM" \
  --frame-diff-threshold "$FRAME_DIFF_THRESHOLD" \
  --frame-output bin \
  --summary-float-precision "$SUMMARY_FLOAT_PRECISION" \
  --vector-output all \
  --vector-format csv \
  --force

measure sidecar_full_vectors_bin "$OUTPUT_DIR/sidecar_full_vectors_bin" \
  "$SIDECAR_BIN" \
  --input "$INPUT" \
  --output-dir "$OUTPUT_DIR/sidecar_full_vectors_bin" \
  --method "$SIDE_METHOD" \
  --mb-size "$SIDE_MB_SIZE" \
  --search-param "$SIDE_SEARCH_PARAM" \
  --frame-diff-threshold "$FRAME_DIFF_THRESHOLD" \
  --frame-output bin \
  --summary-float-precision "$SUMMARY_FLOAT_PRECISION" \
  --vector-output all \
  --vector-format bin \
  --force

python3 - "$OUTPUT_DIR/results.tsv" "$OUTPUT_DIR/summary.tsv" <<'PY'
import csv
import statistics
import sys
from collections import defaultdict

rows = []
with open(sys.argv[1], newline="", encoding="utf-8") as f:
    for row in csv.DictReader(f, delimiter="\t"):
        row["wall_seconds"] = float(row["wall_seconds"])
        row["output_bytes"] = int(row["output_bytes"])
        rows.append(row)

groups = defaultdict(list)
for row in rows:
    groups[row["name"]].append(row)

baseline = statistics.median(r["wall_seconds"] for r in groups["av1_transcode"])
with open(sys.argv[2], "w", newline="", encoding="utf-8") as f:
    fieldnames = ["name", "runs", "median_wall_seconds", "relative_to_av1", "median_output_bytes"]
    writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
    writer.writeheader()
    for name in sorted(groups):
        wall = statistics.median(r["wall_seconds"] for r in groups[name])
        size = int(statistics.median(r["output_bytes"] for r in groups[name]))
        writer.writerow(
            {
                "name": name,
                "runs": len(groups[name]),
                "median_wall_seconds": f"{wall:.6f}",
                "relative_to_av1": f"{wall / baseline:.6f}" if baseline else "",
                "median_output_bytes": size,
            }
        )
PY

echo
echo "Wrote benchmark rows: $OUTPUT_DIR/results.tsv"
echo "Wrote benchmark summary: $OUTPUT_DIR/summary.tsv"
