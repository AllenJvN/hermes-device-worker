#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

DISPLAY_NAME="${1:-$(scutil --get ComputerName 2>/dev/null || hostname)}"
PLIST="$HOME/Library/LaunchAgents/com.hermes.device-worker.plist"
WORKDIR="$(pwd)"

mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.hermes.device-worker</string>
  <key>WorkingDirectory</key>
  <string>$WORKDIR</string>
  <key>ProgramArguments</key>
  <array>
    <string>$WORKDIR/.venv/bin/python</string>
    <string>$WORKDIR/worker.py</string>
    <string>--display-name</string>
    <string>$DISPLAY_NAME</string>
    <string>--host</string>
    <string>0.0.0.0</string>
    <string>--port</string>
    <string>18888</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$HOME/Library/Logs/hermes-device-worker.log</string>
  <key>StandardErrorPath</key>
  <string>$HOME/Library/Logs/hermes-device-worker.err.log</string>
</dict>
</plist>
PLIST

launchctl unload "$PLIST" >/dev/null 2>&1 || true
launchctl load "$PLIST"

echo "Installed launchd service: $PLIST"
echo "Logs: ~/Library/Logs/hermes-device-worker.log"

