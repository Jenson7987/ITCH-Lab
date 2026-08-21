#!/bin/sh

set -eu

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH= cd -- "$script_directory/../.." && pwd)
release_python=${ITCHLAB_RELEASE_PYTHON:-python3}
release_python=$(command -v "$release_python")

smoke_root=$(mktemp -d)
case "$smoke_root" in
  '' | / | "$repository_root")
    echo "Unsafe TASK-030 temporary directory: $smoke_root" >&2
    exit 1
    ;;
esac
cleanup() {
  rm -rf -- "$smoke_root"
}
trap cleanup EXIT HUP INT TERM

candidate_argument=
if [ "${ITCHLAB_ALLOW_DIRTY_RELEASE_CANDIDATE:-0}" = "1" ]; then
  candidate_argument=--allow-dirty-candidate
fi

cd "$repository_root"
"$release_python" scripts/release/build_release.py \
  --output-root "$smoke_root/release" ${candidate_argument:+"$candidate_argument"}

"$release_python" - "$smoke_root/release" <<'PYTHON'
import hashlib
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected = {}
for line in (root / "SHA256SUMS").read_text(encoding="ascii").splitlines():
    digest, name = line.split("  ", 1)
    expected[name] = digest
assert expected
for name, digest in expected.items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
PYTHON

native_archive=
wheel=
for candidate in "$smoke_root"/release/itchlab-*-macos-arm64.tar.gz \
                 "$smoke_root"/release/itchlab-*-linux-x86_64.tar.gz; do
  if [ -f "$candidate" ]; then
    if [ -n "$native_archive" ]; then
      echo "More than one native release archive was produced." >&2
      exit 1
    fi
    native_archive=$candidate
  fi
done
for candidate in "$smoke_root"/release/itchlab_research-*.whl; do
  if [ -f "$candidate" ]; then
    if [ -n "$wheel" ]; then
      echo "More than one Python wheel was produced." >&2
      exit 1
    fi
    wheel=$candidate
  fi
done
if [ -z "$native_archive" ] || [ -z "$wheel" ]; then
  echo "Release build did not produce the required native archive and wheel." >&2
  exit 1
fi

mkdir "$smoke_root/native" "$smoke_root/wheelhouse" "$smoke_root/workspace"
tar -xzf "$native_archive" -C "$smoke_root/native"
binary=$(find "$smoke_root/native" -type f -path '*/bin/itchlab' -print)
binary_count=$(find "$smoke_root/native" -type f -path '*/bin/itchlab' -print | wc -l | tr -d ' ')
if [ "$binary_count" -ne 1 ]; then
  echo "Native archive must contain exactly one itchlab executable." >&2
  exit 1
fi

"$release_python" -m pip download \
  --require-hashes \
  --requirement python/requirements-release.lock \
  --dest "$smoke_root/wheelhouse"
"$release_python" -m venv "$smoke_root/venv"
"$smoke_root/venv/bin/python" -m pip install \
  --no-index \
  --find-links "$smoke_root/wheelhouse" \
  --require-hashes \
  --requirement python/requirements-release.lock
"$smoke_root/venv/bin/python" -m pip install \
  --no-index \
  --no-deps \
  "$wheel"

ITCHLAB_SECURITY_PYTHON="$smoke_root/venv/bin/python" \
  scripts/security/run-network-disabled.sh \
  "$smoke_root/venv/bin/python" scripts/release/installed_e2e.py \
  --binary "$binary" \
  --python "$smoke_root/venv/bin/python" \
  --workspace "$smoke_root/workspace"

echo "TASK-030 clean installed release smoke passed."
