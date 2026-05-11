#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ -t 1 && -z "${NO_COLOR:-}" ]]; then
  C_INFO=$'\033[36;1m'
  C_OK=$'\033[32;1m'
  C_WARN=$'\033[33;1m'
  C_RESET=$'\033[0m'
else
  C_INFO=""
  C_OK=""
  C_WARN=""
  C_RESET=""
fi

pick_python() {
  if [[ -n "${PYTHON:-}" ]]; then
    printf "%s\n" "$PYTHON"
    return
  fi
  for candidate in python3.12 python3.11 python3.10 /opt/homebrew/bin/python3 /usr/local/bin/python3 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      version=$("$candidate" - <<PY
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)
      major=${version%%.*}
      minor=${version#*.}
      if [[ "$major" -gt 3 || ( "$major" -eq 3 && "$minor" -ge 10 ) ]]; then
        command -v "$candidate"
        return
      fi
    fi
  done
  return 1
}

PY_BIN=$(pick_python || true)
if [[ -z "${PY_BIN:-}" ]]; then
  cat >&2 <<MSG
Hermes Device Worker requires Python 3.10+ because the MCP dependency no longer supports Python 3.9.

Install a newer Python, then rerun this script:
  brew install python@3.12

Or pass one explicitly:
  PYTHON=/opt/homebrew/bin/python3 ./install.sh --install-cua
MSG
  exit 2
fi

echo "${C_INFO}Using Python:${C_RESET} $PY_BIN ($($PY_BIN --version))"
rm -rf .venv
"$PY_BIN" -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt

# Keep ~/.local/bin visible for cua-driver, including launchd/GUI-launched shells.
case ":$PATH:" in
  *:"$HOME/.local/bin":*) ;;
  *) export PATH="$HOME/.local/bin:$PATH" ;;
esac

if [[ "${1:-}" == "--install-cua" ]]; then
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "${C_WARN}cua-driver is macOS-only; skipping.${C_RESET}"
  elif command -v cua-driver >/dev/null 2>&1; then
    echo "${C_OK}cua-driver found:${C_RESET} $(command -v cua-driver)"
  else
    echo "${C_INFO}Installing cua-driver for macOS background desktop control...${C_RESET}"
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/scripts/install.sh)"
  fi
fi

cat <<MSG

Hermes Device Worker dependencies installed.

Run:
  ./run.sh --display-name macbook-allen --host 0.0.0.0 --port 18888

For macOS GUI control:
  1. Grant Accessibility + Screen Recording permissions to your terminal/launcher.
  2. Run ./check_permissions.sh to open macOS permission panes and trigger prompts.
MSG
