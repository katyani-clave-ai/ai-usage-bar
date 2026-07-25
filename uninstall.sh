#!/usr/bin/env bash
# usage-bar uninstaller: stop + remove everything it installed. Leaves ccusage.
set -euo pipefail

APP="usage-bar"
LABEL="com.usage-bar.agent"

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f  "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -f  "$HOME/.local/bin/$APP"
rm -rf "$HOME/.local/share/$APP"
rm -rf "$HOME/.local/state/$APP"

echo "Removed usage-bar. (ccusage, if you installed it, is left in place.)"
