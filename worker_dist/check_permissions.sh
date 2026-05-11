#!/usr/bin/env bash
set -euo pipefail

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

cat <<MSG
${C_INFO}Hermes Device Worker macOS permission check${C_RESET}

macOS requires manual approval for desktop control. This script can trigger
prompts and open the correct Settings panes, but it cannot grant permissions.
MSG

echo
if command -v cua-driver >/dev/null 2>&1; then
  echo "${C_OK}✓ cua-driver found:${C_RESET} $(command -v cua-driver)"
else
  echo "${C_WARN}✗ cua-driver not found.${C_RESET} Run: ./install.sh --install-cua"
fi

echo
cat <<MSG
Opening permission panes. Approve the terminal app/launcher that runs the worker
(Terminal, iTerm, VS Code, Cursor, etc.) and any Python/cua-driver entry macOS shows.
MSG

open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility" || true
sleep 1
open "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture" || true

echo
cat <<MSG
${C_INFO}Triggering likely permission prompts...${C_RESET}
MSG

set +e
osascript -e 'tell application "System Events" to get name of first process' >/tmp/hermes-accessibility-test.out 2>/tmp/hermes-accessibility-test.err
AX_STATUS=$?
screencapture -x /tmp/hermes-screen-test.png >/tmp/hermes-screen-test.out 2>/tmp/hermes-screen-test.err
SC_STATUS=$?
rm -f /tmp/hermes-screen-test.png
set -e

echo
if [[ $AX_STATUS -eq 0 ]]; then
  echo "${C_OK}✓ Accessibility probe succeeded.${C_RESET}"
else
  echo "${C_WARN}⚠ Accessibility probe failed or needs approval.${C_RESET} Check the Accessibility pane."
  sed -n 1,3p /tmp/hermes-accessibility-test.err 2>/dev/null || true
fi

if [[ $SC_STATUS -eq 0 ]]; then
  echo "${C_OK}✓ Screen capture probe succeeded.${C_RESET}"
else
  echo "${C_WARN}⚠ Screen capture probe failed or needs approval.${C_RESET} Check the Screen Recording pane."
  sed -n 1,3p /tmp/hermes-screen-test.err 2>/dev/null || true
fi

cat <<MSG

After granting permissions:
  1. Quit/restart the terminal or worker process if macOS asks.
  2. Start the worker again:
     ./run.sh --display-name macbook-allen --host 0.0.0.0 --port 18888
  3. On Hermes, check:
     hermes device node capabilities macbook-allen

Expected:
  "computer_use": true
MSG
