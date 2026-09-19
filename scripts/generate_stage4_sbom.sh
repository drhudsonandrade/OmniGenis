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

curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
  --retry 3 --retry-delay 2 --retry-connrefused --max-time 300 \
  "$url" -o "$archive"
printf '%s  %s\n' "$expected" "$archive" | sha256sum --check --strict
tar -xzf "$archive" -C "$work" syft
observed="$("$work/syft" version | awk '/^Version:/ {print $2}')"
[[ "$observed" = "$version" ]] || {
  echo "Syft version mismatch: expected=$version observed=$observed" >&2
  exit 3
}

"$work/syft" scan "$IMAGE_REF" -q \
  -o "syft-json=$OUTPUT_DIR/omnigenis.syft.json" \
  -o "spdx-json=$OUTPUT_DIR/omnigenis.spdx.json" \
  -o "cyclonedx-json=$OUTPUT_DIR/omnigenis.cdx.json"

# Syft does not currently expose the Conda package database as a first-class
# package catalog. Capture the installed environment from the built image and
# reconcile it byte-independently against the audited explicit Conda lock.
docker run --rm "$IMAGE_REF" \
  micromamba list --name base --json > "$OUTPUT_DIR/omnigenis.conda.json"

python3 "$ROOT/scripts/validate_stage4_sbom.py" \
  "$OUTPUT_DIR/omnigenis.syft.json" \
  "$OUTPUT_DIR/omnigenis.spdx.json" \
  "$OUTPUT_DIR/omnigenis.cdx.json" \
  "$ROOT/locks/conda-linux-64-resolution.json" \
  "$ROOT/locks/python-license-metadata.json" \
  "$OUTPUT_DIR/omnigenis.conda.json"

(
  cd "$OUTPUT_DIR"
  sha256sum \
    omnigenis.syft.json \
    omnigenis.spdx.json \
    omnigenis.cdx.json \
    omnigenis.conda.json > SHA256SUMS
)
