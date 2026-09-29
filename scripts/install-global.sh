#!/usr/bin/env bash
# opencode global'e telegram MCP'yi ekler (idempotent, backup'li)
# Kullanim: ./install-global.sh
# Test: OPENCODE_GLOBAL_CONFIG=/tmp/test-opencode.json ./install-global.sh
set -euo pipefail
PKG_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CFG="${OPENCODE_GLOBAL_CONFIG:-$HOME/.config/opencode/opencode.json}"
mkdir -p "$(dirname "$CFG")"
[ -f "$CFG" ] || echo '{}' > "$CFG"
STAMP="$(date +%Y%m%d-%H%M%S)"
cp "$CFG" "$CFG.bak-$STAMP"
python3 "$PKG_DIR/scripts/merge_global_config.py" "$CFG" "$PKG_DIR/mcp/server.py" "$PKG_DIR/steering/telegram-ops.md" "python3"
echo "Backup: $CFG.bak-$STAMP"
echo "Sonraki: opencode'u kapat-ac, iceride 'telegram_status' yaz."
