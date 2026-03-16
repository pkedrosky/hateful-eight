#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$ROOT/hateful-eight-interactive.html"
OUT_DIR="$ROOT/dist"
OUT="$OUT_DIR/index.html"

if [[ ! -f "$SRC" ]]; then
  echo "Missing source HTML: $SRC" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
cp "$SRC" "$OUT"
echo "Wrote $OUT"
