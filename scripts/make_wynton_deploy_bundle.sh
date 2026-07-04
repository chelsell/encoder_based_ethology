#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-$(pwd)}"
OUTPUT_DIR="${OUTPUT_DIR:-/media/ssd1/tmp/encoder_based_ethology_deploy}"
ALLOW_DIRTY="${ALLOW_DIRTY:-0}"

cd "$REPO_DIR"

commit="$(git rev-parse --short=12 HEAD)"
dirty_count="$(git status --porcelain | wc -l | awk '{print $1}')"
if [[ "$ALLOW_DIRTY" != "1" ]] && [[ "$dirty_count" != "0" ]]; then
  echo "Repository has uncommitted changes. Commit them or set ALLOW_DIRTY=1." >&2
  git status --short >&2
  exit 2
fi

bundle_root="encoder_based_ethology_${commit}"
bundle_name="${bundle_root}.tar.gz"
mkdir -p "$OUTPUT_DIR"

file_list="$(mktemp)"
trap 'rm -f "$file_list"' EXIT

cat > "$file_list" <<'FILES'
CMakeLists.txt
mestimate_sidecar.def
include/mestimate_sidecar.h
src/mestimate_sidecar.c
tests/make_synthetic_translation.py
tests/test_synthetic_translation.sh
tests/test_schema.py
tests/test_archival_plate_task.py
tests/test_manage_archival_sge_queue.py
tests/test_make_well_archival_manifest.py
scripts/archival_plate_array.sge
scripts/build_wynton_container.sh
scripts/make_wynton_deploy_bundle.sh
scripts/make_well_archival_manifest.py
scripts/manage_archival_sge_queue.py
scripts/run_archival_plate_task.py
docs/cluster_reproducibility.md
docs/sge_archival_orchestration.md
docs/well_first_archival_sidecar.md
docs/wynton_container_and_disk_budget.md
README.md
FILES

missing=0
while IFS= read -r path; do
  if [[ ! -e "$path" ]]; then
    echo "Missing deploy file: $path" >&2
    missing=1
  fi
done < "$file_list"
if [[ "$missing" != "0" ]]; then
  exit 1
fi

tar --transform "s#^#${bundle_root}/#" -czf "$OUTPUT_DIR/$bundle_name" --files-from "$file_list"

python3 - "$OUTPUT_DIR" "$bundle_name" "$file_list" <<'PY'
import hashlib
import json
import pathlib
import subprocess
import sys

output_dir = pathlib.Path(sys.argv[1])
bundle_name = sys.argv[2]
file_list = pathlib.Path(sys.argv[3])
bundle_path = output_dir / bundle_name

def sha256(path):
    h = hashlib.sha256()
    with pathlib.Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

files = [line.strip() for line in file_list.read_text(encoding="utf-8").splitlines() if line.strip()]
payload = {
    "bundle_path": str(bundle_path),
    "bundle_sha256": sha256(bundle_path),
    "repo_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "repo_dirty_count": int(subprocess.check_output(["git", "status", "--porcelain"], text=True).count("\n")),
    "files": [{"path": path, "sha256": sha256(path)} for path in files],
}
manifest = bundle_path.with_suffix("").with_suffix(".manifest.json")
manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(json.dumps(payload, indent=2))
PY
