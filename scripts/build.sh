#!/usr/bin/env bash
set -euo pipefail

CMAKE_BIN="${CMAKE_BIN:-}"
if [[ -z "$CMAKE_BIN" ]]; then
  if [[ -x /usr/bin/cmake ]]; then
    CMAKE_BIN=/usr/bin/cmake
  else
    CMAKE_BIN=cmake
  fi
fi

"$CMAKE_BIN" -S . -B build -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE:-Release}"
"$CMAKE_BIN" --build build --parallel
