#!/bin/sh

set -eu

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH= cd -- "$script_directory/../.." && pwd)
security_python=${ITCHLAB_SECURITY_PYTHON:-python3}
security_python=$(command -v "$security_python")
clang_tidy=${ITCHLAB_CLANG_TIDY:-clang-tidy}

cd "$repository_root"

if ! command -v "$clang_tidy" >/dev/null 2>&1; then
  echo "clang-tidy is required; set ITCHLAB_CLANG_TIDY to an exact executable." >&2
  exit 1
fi

cmake --version | sed -n '1p'
"$security_python" --version
"$security_python" -m pip_audit --version
"$security_python" -m detect_secrets --version
"$clang_tidy" --version | sed -n '1,2p'

"$security_python" tests/fuzz/generate_corpus.py --check
"$security_python" -m tests.fixtures.generate_itch50 --check
"$security_python" tests/fixtures/generate_interchange_v1.py --check

cmake --preset dev
cmake --build --preset dev

if ! find cpp/src cpp/apps -type f -name '*.cpp' -print -quit | grep -q .; then
  echo "No project C++ sources were found for clang-tidy." >&2
  exit 1
fi
find cpp/src cpp/apps -type f -name '*.cpp' -print0 | \
  xargs -0 "$clang_tidy" -p build/dev --config-file=.clang-tidy \
    '--checks=-*,clang-analyzer-*'

cmake --preset sanitizers
cmake --build --preset sanitizers
ctest --preset sanitizers --output-on-failure

cmake --preset fuzz
cmake --build --preset fuzz
ctest --preset fuzz --output-on-failure -R SEC-FUZZ-001

"$security_python" -m pytest python/tests

git ls-files --cached --others --exclude-standard -z | \
  xargs -0 "$security_python" -m detect_secrets.pre_commit_hook \
    --baseline .secrets.baseline --no-verify

"$security_python" -m pip_audit \
  --require-hashes \
  --disable-pip \
  --strict \
  --progress-spinner off \
  --requirement python/requirements-release.lock

ITCHLAB_SECURITY_PYTHON="$security_python" "$script_directory/network-disabled-smoke.sh"

echo "TASK-028 security suite passed."
