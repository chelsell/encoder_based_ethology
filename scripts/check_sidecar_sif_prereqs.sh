#!/usr/bin/env bash
set -euo pipefail

APPTAINER_BIN="${APPTAINER_BIN:-apptainer}"

echo "Apptainer:"
if command -v "$APPTAINER_BIN" >/dev/null 2>&1; then
  "$APPTAINER_BIN" --version
else
  echo "  missing: $APPTAINER_BIN"
  exit 2
fi

echo
echo "Fakeroot helper ownership:"
status=0
for helper in /usr/bin/newuidmap /usr/bin/newgidmap; do
  if [[ -e "$helper" ]]; then
    line="$(ls -l "$helper")"
    owner="$(stat -c '%U:%G' "$helper")"
    mode="$(stat -c '%A' "$helper")"
    echo "  $line"
    if [[ "$owner" != "root:root" || "${mode:3:1}" != "s" ]]; then
      status=1
    fi
  else
    echo "  missing: $helper"
    status=1
  fi
done

echo
if [[ "$status" -eq 0 ]]; then
  echo "Local unprivileged Apptainer builds are plausibly configured."
else
  cat <<'EOF'
Local unprivileged Apptainer builds may fail before reading the def file.
Use a correctly configured build host, a site remote builder, or ask an
administrator to repair newuidmap/newgidmap ownership and setuid mode.
EOF
fi

exit "$status"
