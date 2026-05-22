#!/bin/bash
# DCD 双币赢 — 每周投资报告执行器
# 推荐由 launchd 每周日 20:00 触发（与每日 cron 完全独立）

set -u

WORK_DIR="${DCD_WORK_DIR:-$HOME/.dcd-trader}"
PLUGIN_DIR="${DCD_PLUGIN_DIR:-$HOME/.claude/plugins/dcd-trader}"
LOG_DIR="$WORK_DIR/logs"
TELEGRAM_ENV="${TELEGRAM_ENV:-$HOME/.claude/channels/telegram/.env}"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
MODEL="${DCD_MODEL:-claude-sonnet-4-6}"
TIMEOUT_SEC="${DCD_WEEKLY_TIMEOUT_SEC:-1200}"

export DCD_WORK_DIR="$WORK_DIR"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p "$LOG_DIR"
TS=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/dcd_weekly_$TS.log"

cd "$WORK_DIR" || {
    echo "[$(date)] FATAL: cannot cd to $WORK_DIR" >&2
    exit 1
}

if [ -f "$TELEGRAM_ENV" ]; then
    # shellcheck disable=SC1090
    source "$TELEGRAM_ENV"
else
    echo "[$(date)] WARN: telegram env not found at $TELEGRAM_ENV" | tee -a "$LOG_FILE"
fi

send_telegram() {
    local text="$1"
    if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
        echo "[$(date)] SKIP telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing" | tee -a "$LOG_FILE"
        return
    fi
    if [ "${#text}" -gt 3800 ]; then
        text="${text:0:3800}

...（已截断）"
    fi
    curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${text}" \
        >> "$LOG_FILE" 2>&1
}

SKILL_FILE="$PLUGIN_DIR/skills/dcd-weekly-report/SKILL.md"
MCP_CONFIG="$PLUGIN_DIR/.mcp.json"

if [ ! -f "$SKILL_FILE" ]; then
    echo "[$(date)] FATAL: skill file missing: $SKILL_FILE" | tee -a "$LOG_FILE"
    exit 2
fi

echo "[$(date)] === DCD weekly report start (timeout=${TIMEOUT_SEC}s) ===" | tee -a "$LOG_FILE"
echo "[$(date)] Model: $MODEL | work_dir: $WORK_DIR | plugin_dir: $PLUGIN_DIR" | tee -a "$LOG_FILE"

security unlock-keychain "$HOME/Library/Keychains/login.keychain-db" 2>/dev/null

gtimeout "$TIMEOUT_SEC" "$CLAUDE_BIN" \
    --model "$MODEL" \
    --dangerously-skip-permissions \
    --strict-mcp-config \
    --mcp-config "$MCP_CONFIG" \
    --print \
    "读取并执行 $SKILL_FILE 中的完整指令。工作目录: $WORK_DIR" \
    >> "$LOG_FILE" 2>&1

EXIT_CODE=$?
echo "[$(date)] === DCD weekly report end (exit=$EXIT_CODE) ===" | tee -a "$LOG_FILE"

REPORT_FILE="$WORK_DIR/last_weekly_report.md"

if [ "$EXIT_CODE" -eq 0 ] && [ -f "$REPORT_FILE" ]; then
    REPORT=$(cat "$REPORT_FILE")
    send_telegram "$REPORT"

elif [ "$EXIT_CODE" -eq 124 ]; then
    send_telegram "🔴 DCD 周报超时（${TIMEOUT_SEC}秒）被强制终止

日志：$(tail -5 "$LOG_FILE" 2>/dev/null)"

else
    TAIL=$(tail -n 20 "$LOG_FILE" 2>/dev/null)
    send_telegram "🔴 DCD 周报失败 (exit=$EXIT_CODE)

$TAIL"
fi

exit $EXIT_CODE
