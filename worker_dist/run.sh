#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x ./.venv/bin/python ]]; then
  cat >&2 <<MSG
Hermes Device Worker is not installed in this folder yet.

Run first:
  ./install.sh --install-cua

Then start the worker again:
  ./run.sh --display-name macbook-allen --host 0.0.0.0 --port 18888
MSG
  exit 2
fi

exec ./.venv/bin/python worker.py "$@"
