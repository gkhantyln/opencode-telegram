#!/usr/bin/env bash
# opencode global kaydini kaldirir (telegram MCP + steering talimati).
# Kullanim: ./scripts/uninstall-global.sh
# Idempotent: kayit yoksa da sessizce basarili biter.
set -euo pipefail
PKG_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CFG="${OPENCODE_GLOBAL_CONFIG:-$HOME/.config/opencode/opencode.json}"
STEER="$PKG_DIR/steering/telegram-ops.md"
MERGE="$PKG_DIR/scripts/merge_global_config.py"

[ -f "$CFG" ] || { echo "Zaten yok: $CFG"; exit 0; }

STAMP="$(date +%Y%m%d-%H%M%S)"
cp "$CFG" "$CFG.bak-$STAMP"
echo "Yedek: $CFG.bak-$STAMP"

python3 "$MERGE" "$CFG" "--remove" "$STEER"
echo "Kaldirildi: telegram MCP kaydi + steering talimati"
echo "Simdi opencode'u kapat-ac."
echo "Not: .env ve calisma verisi (mailbox) silinmedi; onlari elle silebilirsin."
