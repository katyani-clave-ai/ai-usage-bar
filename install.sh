#!/usr/bin/env bash
# usage-bar installer: detect deps -> compile -> install LaunchAgent -> start.
set -euo pipefail

APP="usage-bar"
LABEL="com.usage-bar.agent"
BIN_DIR="$HOME/.local/bin"
SHARE_DIR="$HOME/.local/share/$APP"
STATE_DIR="$HOME/.local/state/$APP"
LA_DIR="$HOME/Library/LaunchAgents"
PLIST="$LA_DIR/$LABEL.plist"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/src"

echo "==> Checking prerequisites"
command -v swiftc  >/dev/null || { echo "ERROR: swiftc not found. Run: xcode-select --install"; exit 1; }
command -v python3 >/dev/null || { echo "ERROR: python3 not found."; exit 1; }

CCUSAGE="$(command -v ccusage || true)"
if [ -z "$CCUSAGE" ]; then
  echo "NOTE: 'ccusage' not found — the Claude numbers need it:  npm i -g ccusage"
  echo "      (Codex still works without it.)"
fi
NODE="$(command -v node || true)"
NODE_DIR=""
[ -n "$NODE" ] && NODE_DIR="$(dirname "$NODE")"

echo "==> Installing files"
mkdir -p "$BIN_DIR" "$SHARE_DIR" "$STATE_DIR" "$LA_DIR"
cp "$SRC_DIR/usage_brain.py" "$SHARE_DIR/usage_brain.py"

echo "==> Compiling menu-bar app"
swiftc -O "$SRC_DIR/UsageIndicator.swift" -o "$BIN_DIR/$APP"

echo "==> Writing LaunchAgent"
# PATH must include node so ccusage (a node script) can run under launchd.
AGENT_PATH="/usr/bin:/bin:/usr/local/bin"
[ -n "$NODE_DIR" ] && AGENT_PATH="$NODE_DIR:$AGENT_PATH"
cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>ProgramArguments</key>
    <array><string>$BIN_DIR/$APP</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ProcessType</key><string>Interactive</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key><string>$AGENT_PATH</string>
        <key>USAGE_BAR_CCUSAGE</key><string>${CCUSAGE:-ccusage}</string>
    </dict>
</dict>
</plist>
PLISTEOF

echo "==> Starting"
launchctl bootout   "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/$LABEL"

echo
echo "Done. Look at the top-right of your menu bar for e.g.  🟢 Cdx:42 Cld:68"
echo "Click it for detail. First alert may prompt for notification permission."
echo "Uninstall any time with ./uninstall.sh"
