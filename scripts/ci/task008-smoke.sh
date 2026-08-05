#!/bin/sh

set -eu

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH= cd -- "$script_directory/../.." && pwd)
cd "$repository_root"

cmake --preset dev
cmake --build --preset dev
python3 -m tests.fixtures.generate_itch50 --check
ctest --preset dev --output-on-failure -R 'TASK-008|E2E-00[12]'

smoke_root=$(mktemp -d)
case "$smoke_root" in
  '' | / | "$repository_root")
    echo "Unsafe TASK-008 temporary directory: $smoke_root" >&2
    exit 1
    ;;
esac
cleanup() {
  rm -rf -- "$smoke_root"
}
trap cleanup EXIT HUP INT TERM

binary="$repository_root/build/dev/itchlab"
minimal_fixture="$repository_root/tests/fixtures/synthetic_minimal.itch"
diagnostic_config="$repository_root/configs/replay.diagnostic.example.json"
expected_directory="$repository_root/tests/golden/minimal"
expected_events="$repository_root/tests/golden/itch50/synthetic_minimal_diagnostic_events.jsonl"
expected_snapshots="$repository_root/tests/golden/itch50/synthetic_minimal_diagnostic_snapshots.jsonl"

"$binary" inspect \
  --input "$minimal_fixture" \
  --all \
  --symbols AAPL \
  --format json >"$smoke_root/inspect.json"
cmp "$smoke_root/inspect.json" "$expected_directory/inspect.json"

"$binary" replay \
  --config "$diagnostic_config" \
  --output-root "$smoke_root/first" \
  --format json >"$smoke_root/first.json"
"$binary" replay \
  --config "$diagnostic_config" \
  --output-root "$smoke_root/second" \
  --format json >"$smoke_root/second.json"

cmp "$smoke_root/first.json" "$expected_directory/replay.json"
cmp "$smoke_root/second.json" "$expected_directory/replay.json"
cmp "$smoke_root/first/diagnostic-events.jsonl" "$expected_events"
cmp "$smoke_root/second/diagnostic-events.jsonl" "$expected_events"
cmp "$smoke_root/first/diagnostic-snapshots.jsonl" "$expected_snapshots"
cmp "$smoke_root/second/diagnostic-snapshots.jsonl" "$expected_snapshots"

python3 - "$diagnostic_config" "$smoke_root/corrupt.json" <<'PYTHON'
import json
import sys
from pathlib import Path

source = Path(sys.argv[1])
destination = Path(sys.argv[2])
config = json.loads(source.read_text(encoding="utf-8"))
config["input"]["path"] = "tests/fixtures/corrupt/synthetic_corrupt_gzip_checksum.itch.gz"
destination.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
PYTHON

set +e
"$binary" replay \
  --config "$smoke_root/corrupt.json" \
  --output-root "$smoke_root/corrupt-output" \
  --format json >"$smoke_root/corrupt-result.json" 2>"$smoke_root/corrupt-stderr.txt"
corrupt_exit_code=$?
set -e

if [ "$corrupt_exit_code" -ne 3 ]; then
  echo "Corrupt replay exited $corrupt_exit_code; expected 3." >&2
  exit 1
fi
if [ -s "$smoke_root/corrupt-stderr.txt" ]; then
  echo "Corrupt replay wrote unexpected stderr in JSON mode." >&2
  exit 1
fi
cmp "$smoke_root/corrupt-result.json" "$expected_directory/corrupt-replay.json"
test ! -e "$smoke_root/corrupt-output/diagnostic-events.jsonl"
test ! -e "$smoke_root/corrupt-output/diagnostic-snapshots.jsonl"
test ! -s "$smoke_root/corrupt-output/diagnostic-events.jsonl.partial"
test ! -s "$smoke_root/corrupt-output/diagnostic-snapshots.jsonl.partial"

echo "TASK-008 reduced E2E smoke passed."
