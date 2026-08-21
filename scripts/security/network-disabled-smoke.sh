#!/bin/sh

set -eu

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH= cd -- "$script_directory/../.." && pwd)
script_path="$script_directory/network-disabled-smoke.sh"
security_python=${ITCHLAB_SECURITY_PYTHON:-python3}
security_python=$(command -v "$security_python")

run_inside() {
  ctest --preset dev --output-on-failure -R 'E2E-001|E2E-002|IT-012'
  "$security_python" -m pytest -q \
    python/tests/test_simulation_service.py::test_e2e_001_simulation_grid_is_immutable_valid_and_reportable \
    python/tests/test_security_policy.py::test_task_028_runtime_source_declares_no_network_client_surface
}

cd "$repository_root"
if [ "${1:-}" = "--inside" ]; then
  run_inside
  echo "TASK-028 network-disabled synthetic security smoke passed."
  exit 0
fi

ITCHLAB_SECURITY_PYTHON="$security_python" \
  "$script_directory/run-network-disabled.sh" "$script_path" --inside
