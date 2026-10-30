#!/usr/bin/env bash
# Download the Cohere Wikipedia embedding sets as packaged by VectorDBBench (zilliztech).
# Usage: scripts/fetch_data.sh cohere_small_100k [cohere_medium_1m]
set -euo pipefail
BASE=https://assets.zilliz.com/benchmark
mkdir -p "$(dirname "$0")/../data"; cd "$(dirname "$0")/../data"
for ds in "${@:-cohere_small_100k}"; do
  mkdir -p "$ds"
  for f in test.parquet neighbors.parquet train.parquet; do
    [ -s "$ds/$f" ] || curl -fsS --retry 3 -o "$ds/$f" "$BASE/$ds/$f"
  done
  (cd "$ds" && { command -v sha256sum >/dev/null && sha256sum *.parquet || shasum -a 256 *.parquet; } > SHA256SUMS && cat SHA256SUMS)
done
