#!/bin/sh

set -eu

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH= cd -- "$script_directory/../.." && pwd)
coverage_python=${ITCHLAB_COVERAGE_PYTHON:-python3}
coverage_python=$(command -v "$coverage_python")

cd "$repository_root"

cmake --preset coverage
cmake --build --preset coverage

# Compiler counters are not owned by CMake's clean target. Remove only generated
# counters for this dedicated preset so a changed binary cannot merge stale data.
find build/coverage -type f -name '*.gcda' -delete
ctest --preset coverage --output-on-failure

set -- \
  --root . \
  --filter 'cpp/src/' \
  --filter 'cpp/apps/' \
  --exclude '.*/_deps/.*' \
  --json-summary-pretty \
  --output build/coverage/coverage-summary.json \
  --fail-under-line 75 \
  --fail-under-branch 35

if [ "$(uname -s)" = Darwin ]; then
  llvm_cov=$(xcrun --find llvm-cov)
  set -- "$@" --gcov-executable "$llvm_cov gcov"
fi

"$coverage_python" -m gcovr "$@" build/coverage
"$coverage_python" - <<'PY'
import json
from pathlib import Path

report = json.loads(Path("build/coverage/coverage-summary.json").read_text(encoding="utf-8"))
print(
    "C++ compiler coverage regression floor passed: "
    f"line {report['line_percent']:.1f}%, branch {report['branch_percent']:.1f}%."
)
PY
