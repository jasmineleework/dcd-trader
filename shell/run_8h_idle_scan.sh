#!/bin/bash
# DCD 双币赢 — 8h 闲置资金扫描
# 推荐由 launchd 每 8 小时触发（如 00:13 / 08:13 / 16:13）
# 触发条件：$WORK_DIR/.idle_scan_enabled flag 文件存在
# 取消条件：扫描后若闲置资金已全部部署，由 skill 自动 rm flag

set -u

WORK_DIR="${DCD_WORK_DIR:-$HOME/.dcd-trader}"
PLUGIN_DIR="${DCD_PLUGIN_DIR:-$HOME/.claude/plugins/dcd-trader}"
LOG_DIR="$WORK_DIR/logs"
TELEGRAM_ENV="${TELEGRAM_ENV:-$HOME/.claude/channels/telegram/.env}"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
MODEL="${DCD_MODEL:-claude-sonnet-4-6}"
TIMEOUT_SEC="${DCD_IDLE_TIMEOUT_SEC:-900}"

export DCD_WORK_DIR="$WORK_DIR"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p "$LOG_DIR"
TS=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/dcd_8h_scan_$TS.log"

cd "$WORK_DIR" || {
    echo "[$(date)] FATAL: cannot cd to $WORK_DIR" >&2
    exit 1
}

if [ -f "$TELEGRAM_ENV" ]; then
    # shellcheck disable=SC1090
    source "$TELEGRAM_ENV"
fi

send_telegram() {
    local text="$1"
    if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then return; fi
    if [ "${#text}" -gt 3800 ]; then
        text="${text:0:3800}
...（已截断）"
    fi
    curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${text}" \
        >> "$LOG_FILE" 2>&1
}

SKILL_FILE="$PLUGIN_DIR/skills/dcd-8h-idle-scan/SKILL.md"
MCP_CONFIG="$PLUGIN_DIR/.mcp.json"

if [ ! -f "$SKILL_FILE" ]; then
    echo "[$(date)] FATAL: skill file missing: $SKILL_FILE" | tee -a "$LOG_FILE"
    exit 2
fi

# Flag 闸：无 flag 即视为「无闲置资金，扫描已暂停」
FLAG_FILE="$WORK_DIR/.idle_scan_enabled"
if [ ! -f "$FLAG_FILE" ]; then
    echo "[$(date)] === 8h 扫描跳过：flag $FLAG_FILE 不存在（无闲置资金，扫描暂停） ===" | tee -a "$LOG_FILE"
    exit 0
fi

echo "[$(date)] === DCD 8h 闲置扫描开始 ===" | tee -a "$LOG_FILE"

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
echo "[$(date)] === DCD 8h 闲置扫描结束 (exit=$EXIT_CODE) ===" | tee -a "$LOG_FILE"

REPORT_FILE="$WORK_DIR/last_run_report.md"
if [ "$EXIT_CODE" -eq 0 ] && [ -f "$REPORT_FILE" ]; then
    REPORT=$(cat "$REPORT_FILE")
    send_telegram "🔄 [8h 扫描] $REPORT"
elif [ "$EXIT_CODE" -eq 124 ]; then
    send_telegram "⏱️ [8h 扫描] 超时（${TIMEOUT_SEC}s），请手动检查"
else
    TAIL=$(tail -n 10 "$LOG_FILE" 2>/dev/null)
    send_telegram "⚠️ [8h 扫描] 执行失败 (exit=$EXIT_CODE)
$TAIL"
fi

exit $EXIT_CODE
