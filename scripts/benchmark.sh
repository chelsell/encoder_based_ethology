#!/usr/bin/env bash
set -euo pipefail

INPUT="${1:?Usage: $0 INPUT_VIDEO [OUTPUT_DIR]}"
OUTPUT_DIR="${2:-outputs/benchmark_$(basename "${INPUT%.*}")}"
FFMPEG_BIN="${FFMPEG_BIN:-ffmpeg}"
SIDECAR_OUTPUT_ROOT="$OUTPUT_DIR/sidecar"
IMAGE="${IMAGE:-}"
RUNS="${RUNS:-3}"

mkdir -p "$OUTPUT_DIR"
rm -f "$OUTPUT_DIR/results.tsv"
printf "name\trun\twall_seconds\tuser_seconds\tsys_seconds\tmaxrss_kb\n" > "$OUTPUT_DIR/results.tsv"

measure() {
  local name="$1"
  shift
  echo "== $name =="
  for run in $(seq 1 "$RUNS"); do
    local time_file="$OUTPUT_DIR/${name}_${run}.time.txt"
    /usr/bin/time -f "%e\t%U\t%S\t%M" -o "$time_file" "$@" \
      >"$OUTPUT_DIR/${name}_${run}.stdout.txt" \
      2>"$OUTPUT_DIR/${name}_${run}.stderr.txt"
    awk -v name="$name" -v run="$run" 'BEGIN { OFS="\t" } { print name, run, $1, $2, $3, $4 }' \
      "$time_file" | tee -a "$OUTPUT_DIR/results.tsv"
  done
}

sidecar_cmd=("./build/mestimate-sidecar")
sidecar_input="$INPUT"
sidecar_output="$SIDECAR_OUTPUT_ROOT"
if [[ -n "$IMAGE" ]]; then
  sidecar_cmd=("env" "IMAGE=$IMAGE" "scripts/run_sidecar_sif.sh")
  sidecar_input="/work/$INPUT"
  sidecar_output="/work/$SIDECAR_OUTPUT_ROOT"
fi

measure ffmpeg_decode_only "$FFMPEG_BIN" -hide_banner -nostdin -v error \
  -i "$INPUT" -f null -

measure ffmpeg_mestimate_null "$FFMPEG_BIN" -hide_banner -nostdin -v error \
  -i "$INPUT" \
  -vf "format=gray,mestimate=method=epzs:mb_size=16:search_param=12" \
  -f null -

measure sidecar "${sidecar_cmd[@]}" \
  --input "$sidecar_input" \
  --output-dir "$sidecar_output" \
  --method epzs \
  --mb-size 16 \
  --search-param 12 \
  --force

measure ffmpeg_gray_ffv1 "$FFMPEG_BIN" -hide_banner -nostdin -v error -y \
  -i "$INPUT" -map 0:v:0 -an \
  -vf "format=gray" \
  -c:v ffv1 -level 3 "$OUTPUT_DIR/gray_ffv1.mkv"

measure ffmpeg_mestimate_ffv1 "$FFMPEG_BIN" -hide_banner -nostdin -v error -y \
  -i "$INPUT" -map 0:v:0 -an \
  -vf "format=gray,mestimate=method=epzs:mb_size=16:search_param=12" \
  -c:v ffv1 -level 3 "$OUTPUT_DIR/mestimate_ffv1.mkv"

measure ffmpeg_mestimate_codecview_ffv1 "$FFMPEG_BIN" -hide_banner -nostdin -v error -y \
  -i "$INPUT" -map 0:v:0 -an \
  -vf "format=gray,mestimate=method=epzs:mb_size=16:search_param=12,codecview=mv=pf" \
  -c:v ffv1 -level 3 "$OUTPUT_DIR/mestimate_codecview_ffv1.mkv"

echo
echo "Wrote benchmark table: $OUTPUT_DIR/results.tsv"
