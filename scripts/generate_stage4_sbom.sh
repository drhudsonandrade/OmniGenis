#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: $0 IMAGE_REF OUTPUT_DIR" >&2
  exit 2
fi

IMAGE_REF="$1"
OUTPUT_DIR="$2"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="$ROOT/locks/sbom-tool-lock.json"
mkdir -p "$OUTPUT_DIR"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

readarray -t meta < <(python3 - "$LOCK" <<'PY'
import json
import sys
payload = json.load(open(sys.argv[1], encoding="utf-8"))
print(payload["version"])
print(payload["archive_url"])
print(payload["archive_sha256"])
PY
)
version="${meta[0]}"
url="${meta[1]}"
expected="${meta[2]}"
archive="$work/syft.tgz"

curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 "$url" -o "$archive"
printf '%s  %s\n' "$expected" "$archive" | sha256sum --check --strict
tar -xzf "$archive" -C "$work" syft
observed="$("$work/syft" version | awk '/^Version:/ {print $2}')"
[[ "$observed" == "$version" ]] || {
  echo "Syft version mismatch: expected=$version observed=$observed" >&2
  exit 3
}

"$work/syft" scan "$IMAGE_REF" -q   -o "syft-json=$OUTPUT_DIR/omnigenis.syft.json"   -o "spdx-json=$OUTPUT_DIR/omnigenis.spdx.json"   -o "cyclonedx-json=$OUTPUT_DIR/omnigenis.cdx.json"

python3 "$ROOT/scripts/validate_stage4_sbom.py"   "$OUTPUT_DIR/omnigenis.syft.json"   "$OUTPUT_DIR/omnigenis.spdx.json"   "$OUTPUT_DIR/omnigenis.cdx.json"

(
  cd "$OUTPUT_DIR"
  sha256sum omnigenis.syft.json omnigenis.spdx.json omnigenis.cdx.json > SHA256SUMS
)
