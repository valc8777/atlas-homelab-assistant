#!/bin/zsh

set -euo pipefail

ROOT="/Users/valdemort/Projects/homelab"
SOURCE_LIST="$ROOT/ai-corpus/sources.txt"
OUT_DIR="$ROOT/ai-corpus/build/current"
FILES_DIR="$OUT_DIR/files"
MANIFEST="$OUT_DIR/manifest.tsv"

mkdir -p "$FILES_DIR"
rm -rf "$FILES_DIR"
mkdir -p "$FILES_DIR"

{
  printf "source_path\trelative_name\tbytes\tmtime_epoch\n"

  while IFS= read -r src || [[ -n "$src" ]]; do
    [[ -z "$src" ]] && continue

    if [[ ! -f "$src" ]]; then
      printf "WARN missing source: %s\n" "$src" >&2
      continue
    fi

    rel="${src#/}"
    dest="$FILES_DIR/$rel"
    mkdir -p "$(dirname "$dest")"
    if ! cp "$src" "$dest" 2>/dev/null; then
      printf "WARN unreadable source skipped: %s\n" "$src" >&2
      rm -f "$dest"
      continue
    fi

    bytes="$(stat -f '%z' "$src")"
    mtime="$(stat -f '%m' "$src")"
    printf "%s\t%s\t%s\t%s\n" "$src" "$rel" "$bytes" "$mtime"
  done < "$SOURCE_LIST"
} > "$MANIFEST"

printf "Built corpus at %s\n" "$OUT_DIR"
printf "Manifest: %s\n" "$MANIFEST"
printf "File count: "
tail -n +2 "$MANIFEST" | wc -l | tr -d ' '
printf "\n"
