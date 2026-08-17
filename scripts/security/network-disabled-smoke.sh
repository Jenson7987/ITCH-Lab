#!/bin/sh

set -eu

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_root=$(CDPATH= cd -- "$script_directory/../.." && pwd)
script_path="$script_directory/network-disabled-smoke.sh"
security_python=${ITCHLAB_SECURITY_PYTHON:-python3}
security_python=$(command -v "$security_python")

run_inside() {
  platform=$1
  if [ "$platform" = "darwin" ]; then
    "$security_python" - <<'PYTHON'
import socket

try:
    with socket.socket() as stream:
        stream.bind(("127.0.0.1", 0))
except OSError:
    pass
else:
    raise SystemExit("network sandbox unexpectedly permitted a socket bind")
PYTHON
  else
    current_namespace=$(readlink /proc/self/ns/net)
    if [ "$current_namespace" = "${ITCHLAB_PARENT_NET_NS:-}" ]; then
      echo "Network namespace was not isolated." >&2
      exit 1
    fi
  fi

  ctest --preset dev --output-on-failure -R 'E2E-001|E2E-002|IT-012'
  "$security_python" -m pytest -q \
    python/tests/test_simulation_service.py::test_e2e_001_simulation_grid_is_immutable_valid_and_reportable \
    python/tests/test_security_policy.py::test_task_028_runtime_source_declares_no_network_client_surface
}

cd "$repository_root"
if [ "${1:-}" = "--inside" ]; then
  run_inside "${2:?inside platform is required}"
  exit 0
fi

case "$(uname -s)" in
  Darwin)
    if [ ! -x /usr/bin/sandbox-exec ]; then
      echo "sandbox-exec is required for the macOS network-disabled smoke." >&2
      exit 1
    fi
    ITCHLAB_SECURITY_PYTHON="$security_python" /usr/bin/sandbox-exec \
      -p '(version 1)(allow default)(deny network*)' \
      "$script_path" --inside darwin
    ;;
  Linux)
    if ! command -v unshare >/dev/null 2>&1; then
      echo "unshare is required for the Linux network-disabled smoke." >&2
      exit 1
    fi
    parent_namespace=$(readlink /proc/self/ns/net)
    export ITCHLAB_PARENT_NET_NS="$parent_namespace"
    export ITCHLAB_SECURITY_PYTHON="$security_python"
    if [ "$(id -u)" -eq 0 ]; then
      unshare --net "$script_path" --inside linux
    elif sudo -n true >/dev/null 2>&1; then
      sudo --preserve-env=ITCHLAB_PARENT_NET_NS,ITCHLAB_SECURITY_PYTHON \
        unshare --net "$script_path" --inside linux
    else
      echo "Root or passwordless sudo is required for Linux network isolation." >&2
      exit 1
    fi
    ;;
  *)
    echo "Network isolation is supported only on the declared macOS/Linux platforms." >&2
    exit 1
    ;;
esac

echo "TASK-028 network-disabled synthetic security smoke passed."
