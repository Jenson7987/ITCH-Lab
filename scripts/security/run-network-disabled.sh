#!/bin/sh

set -eu

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
script_path="$script_directory/run-network-disabled.sh"
security_python=${ITCHLAB_SECURITY_PYTHON:-python3}
security_python=$(command -v "$security_python")

verify_isolation() {
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
}

if [ "${1:-}" = "--inside" ]; then
  platform=${2:?inside platform is required}
  shift 2
  if [ "$#" -eq 0 ]; then
    echo "A command is required inside the network-disabled environment." >&2
    exit 2
  fi
  verify_isolation "$platform"
  exec "$@"
fi

if [ "$#" -eq 0 ]; then
  echo "Usage: run-network-disabled.sh command [arguments ...]" >&2
  exit 2
fi

case "$(uname -s)" in
  Darwin)
    if [ ! -x /usr/bin/sandbox-exec ]; then
      echo "sandbox-exec is required for the macOS network-disabled smoke." >&2
      exit 1
    fi
    ITCHLAB_SECURITY_PYTHON="$security_python" /usr/bin/sandbox-exec \
      -p '(version 1)(allow default)(deny network*)' \
      "$script_path" --inside darwin "$@"
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
      unshare --net "$script_path" --inside linux "$@"
    elif sudo -n true >/dev/null 2>&1; then
      if ! command -v setpriv >/dev/null 2>&1; then
        echo "setpriv is required to drop sudo privileges inside Linux network isolation." >&2
        exit 1
      fi
      setpriv_path=$(command -v setpriv)
      caller_uid=$(id -u)
      caller_gid=$(id -g)
      sudo --preserve-env=ITCHLAB_PARENT_NET_NS,ITCHLAB_SECURITY_PYTHON \
        unshare --net "$setpriv_path" \
          --reuid "$caller_uid" --regid "$caller_gid" --clear-groups \
          "$script_path" --inside linux "$@"
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
