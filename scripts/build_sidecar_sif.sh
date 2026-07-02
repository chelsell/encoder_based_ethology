#!/usr/bin/env bash
set -euo pipefail

IMAGE="${1:-mestimate_sidecar.sif}"
DEF="${DEF:-mestimate_sidecar.def}"
APPTAINER_BIN="${APPTAINER_BIN:-apptainer}"
APPTAINER_BUILD_FLAGS="${APPTAINER_BUILD_FLAGS:-}"

if [[ -e /usr/bin/newuidmap ]]; then
  uid_owner="$(stat -c '%U:%G' /usr/bin/newuidmap 2>/dev/null || true)"
  if [[ "$uid_owner" != "root:root" ]]; then
    cat >&2 <<EOF
WARNING: /usr/bin/newuidmap is owned by $uid_owner, not root:root.
Unprivileged Apptainer builds may fail before reading the def file.
Typical fixes are to run the build on a correctly configured host, use a remote
builder, or have an administrator repair newuidmap/newgidmap ownership.
EOF
  fi
fi

# shellcheck disable=SC2086
"$APPTAINER_BIN" build $APPTAINER_BUILD_FLAGS "$IMAGE" "$DEF"
