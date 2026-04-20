#!/bin/bash
# Idempotent starter for Telegram-bot-sesjonen i tmux.
#
# Brukes av com.petter.trening.bot.plist. Sjekker om tmux-sesjonen
# "trening" allerede finnes — hvis ja, gjør ingenting. Hvis nei,
# oppretter den og starter `claude --channels plugin:telegram@...`.

set -euo pipefail

SESSION="trening"
REPO="/Users/petter/Documents/Prosjekter/Trening"
CLAUDE_BIN="/Users/petter/.local/bin/claude"
TMUX_BIN="/opt/homebrew/bin/tmux"
LOG_DIR="$HOME/Library/Logs/Trening"

mkdir -p "$LOG_DIR"

# Sjekk om sesjonen allerede finnes
if "$TMUX_BIN" has-session -t "$SESSION" 2>/dev/null; then
    echo "[$(date '+%Y-%m-%dT%H:%M:%S')] tmux-sesjon '$SESSION' finnes allerede — ingen handling" \
        >> "$LOG_DIR/bot.start.log"
    exit 0
fi

# Start ny detached sesjon
cd "$REPO"
"$TMUX_BIN" new-session -d -s "$SESSION" \
    "$CLAUDE_BIN --channels plugin:telegram@claude-plugins-official"

echo "[$(date '+%Y-%m-%dT%H:%M:%S')] startet tmux '$SESSION' med claude code" \
    >> "$LOG_DIR/bot.start.log"
