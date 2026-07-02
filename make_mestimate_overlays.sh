#!/usr/bin/env bash
#
# make_mestimate_overlays.sh
#
# Create motion-vector overlay videos for every MP4/MOV/M4V in one directory.
#
# Usage:
#   ./make_mestimate_overlays.sh INPUT_DIR OUTPUT_DIR
#
# Example:
#   ./make_mestimate_overlays.sh \
#     "/home/cole/OneDrive/sello_lab/videos/PTZ_CBD_vids" \
#     "./mestimate_overlays"
#

set -uo pipefail

INPUT_DIR="${1:?Usage: $0 INPUT_DIR OUTPUT_DIR}"
OUTPUT_DIR="${2:?Usage: $0 INPUT_DIR OUTPUT_DIR}"

FFMPEG_BIN="ffmpeg-lab"

# ---- Basic checks -----------------------------------------------------------

if [[ ! -d "$INPUT_DIR" ]]; then
    echo "ERROR: Input directory does not exist:" >&2
    echo "  $INPUT_DIR" >&2
    exit 1
fi

if ! command -v "$FFMPEG_BIN" >/dev/null 2>&1; then
    echo "ERROR: '$FFMPEG_BIN' was not found on PATH." >&2
    echo "Expected location: \$HOME/.local/bin/ffmpeg-lab" >&2
    echo "Check with: command -v ffmpeg-lab" >&2
    exit 1
fi

# Let ffmpeg-lab itself validate that mestimate and codecview exist.
# Running -version is a cheap fail-fast check before processing a batch.
if ! "$FFMPEG_BIN" -hide_banner -version >/dev/null 2>&1; then
    echo "ERROR: '$FFMPEG_BIN' did not pass its startup validation." >&2
    echo "Run this directly for the underlying error:" >&2
    echo "  $FFMPEG_BIN -hide_banner -version" >&2
    exit 1
fi

mkdir -p "$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR/logs"

MANIFEST="$OUTPUT_DIR/manifest.tsv"

printf "input_file\toutput_file\tstatus\tlog_file\n" > "$MANIFEST"

total=0
ok=0
failed=0
skipped=0

# ---- Process one video at a time -------------------------------------------

while IFS= read -r -d '' input_file; do
    total=$((total + 1))

    filename="$(basename "$input_file")"
    stem="${filename%.*}"

    output_file="$OUTPUT_DIR/${stem}.mestimate_overlay.mkv"
    log_file="$OUTPUT_DIR/logs/${stem}.log"

    # Do not overwrite existing finished output files.
    if [[ -s "$output_file" ]]; then
        echo "[SKIP] $filename"
        printf "%s\t%s\t%s\t%s\n" \
            "$input_file" \
            "$output_file" \
            "already_exists" \
            "$log_file" >> "$MANIFEST"
        skipped=$((skipped + 1))
        continue
    fi

    echo "[RUN ] $filename"

    if "$FFMPEG_BIN" \
        -hide_banner \
        -nostdin \
        -y \
        -i "$input_file" \
        -map 0:v:0 \
        -an \
        -vf "format=gray,mestimate=method=epzs:mb_size=16:search_param=12,codecview=mv=pf" \
        -c:v ffv1 \
        -level 3 \
        "$output_file" \
        >"$log_file" 2>&1
    then
        echo "[ OK ] $filename"

        printf "%s\t%s\t%s\t%s\n" \
            "$input_file" \
            "$output_file" \
            "ok" \
            "$log_file" >> "$MANIFEST"

        ok=$((ok + 1))
    else
        echo "[FAIL] $filename" >&2
        echo "       See: $log_file" >&2

        # Avoid leaving a partial MP4 that looks superficially valid.
        rm -f "$output_file"

        printf "%s\t%s\t%s\t%s\n" \
            "$input_file" \
            "$output_file" \
            "failed" \
            "$log_file" >> "$MANIFEST"

        failed=$((failed + 1))
    fi

done < <(
    find "$INPUT_DIR" \
        -maxdepth 1 \
        -type f \
        \( -iname '*.mp4' -o -iname '*.m4v' -o -iname '*.mov' \) \
        -print0 | sort -z
)

echo
echo "Finished."
echo "  Found:   $total"
echo "  OK:      $ok"
echo "  Skipped: $skipped"
echo "  Failed:  $failed"
echo "  Output:  $OUTPUT_DIR"
echo "  Manifest: $MANIFEST"

if [[ "$failed" -gt 0 ]]; then
    exit 2
fi
