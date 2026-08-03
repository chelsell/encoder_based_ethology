#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(pwd)}"
DEF_FILE="${DEF_FILE:-mestimate_sidecar.def}"
IMAGE_DIR="${IMAGE_DIR:-/wynton/scratch/$USER/encoder_based_ethology/containers}"
IMAGE_NAME="${IMAGE_NAME:-}"
APPTAINER_BIN="${APPTAINER_BIN:-apptainer}"
APPTAINER_CACHE_ROOT="${APPTAINER_CACHE_ROOT:-/wynton/scratch/$USER/encoder_based_ethology/apptainer-cache}"
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"
RUN_TEST="${RUN_TEST:-1}"

cd "$REPO_DIR"

commit="$(git rev-parse --short=12 HEAD)"
if [[ "$ALLOW_DIRTY" != "1" ]] && [[ -n "$(git status --porcelain)" ]]; then
  echo "Repository has uncommitted changes. Commit them or set ALLOW_DIRTY=1." >&2
  git status --short >&2
  exit 2
fi

if [[ -z "$IMAGE_NAME" ]]; then
  IMAGE_NAME="mestimate_sidecar_${commit}.sif"
fi

mkdir -p "$IMAGE_DIR" "$APPTAINER_CACHE_ROOT"
export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-$APPTAINER_CACHE_ROOT/cache}"
export APPTAINER_TMPDIR="${APPTAINER_TMPDIR:-${TMPDIR:-$APPTAINER_CACHE_ROOT/tmp}}"
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"

image_path="$IMAGE_DIR/$IMAGE_NAME"
manifest_path="${image_path%.sif}.build-manifest.json"

"$APPTAINER_BIN" build "$image_path" "$DEF_FILE"

if [[ "$RUN_TEST" == "1" ]]; then
  "$APPTAINER_BIN" test "$image_path"
fi

image_sha="$(sha256sum "$image_path" | awk '{print $1}')"
def_sha="$(sha256sum "$DEF_FILE" | awk '{print $1}')"
apptainer_version="$("$APPTAINER_BIN" --version)"
ffmpeg_version="$("$APPTAINER_BIN" exec "$image_path" ffmpeg -hide_banner -version | head -n 1)"
sidecar_version="$("$APPTAINER_BIN" run "$image_path" --version)"
dirty="$(git status --porcelain | wc -l | awk '{print $1}')"

python3 - \
  "$manifest_path" \
  "$image_path" \
  "$image_sha" \
  "$DEF_FILE" \
  "$def_sha" \
  "$REPO_DIR" \
  "$(git rev-parse HEAD)" \
  "$dirty" \
  "$apptainer_version" \
  "$ffmpeg_version" \
  "$sidecar_version" <<'PY'
import json
import pathlib
import sys

payload = {
    "image_path": sys.argv[2],
    "image_sha256": sys.argv[3],
    "definition_file": sys.argv[4],
    "definition_sha256": sys.argv[5],
    "repo_dir": sys.argv[6],
    "repo_commit": sys.argv[7],
    "repo_dirty": sys.argv[8],
    "apptainer_version": sys.argv[9],
    "ffmpeg_version": sys.argv[10],
    "sidecar_version": sys.argv[11],
}
path = pathlib.Path(sys.argv[1])
path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2))
PY
