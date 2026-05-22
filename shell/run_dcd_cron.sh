#!/bin/bash
# DCD 双币赢 — 每日定时自动交易执行器
# 推荐由 launchd（label 自取，如 com.<your-name>.dcd-daily-trade）每日触发
#
# 配置项可通过环境变量覆盖：
#   DCD_WORK_DIR       数据/日志目录（默认 ~/.dcd-trader）
#   DCD_PLUGIN_DIR     plugin 仓库根目录（默认 ~/.claude/plugins/dcd-trader 或自定义 clone 路径）
#   TELEGRAM_ENV       含 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID 的 .env 文件
#   DCD_MODEL          Claude 模型（默认 claude-sonnet-4-6）
#   DCD_TIMEOUT_SEC    硬超时秒数（默认 1200）
#   DCD_MAX_RETRIES    失败重试次数（默认 1）
#   DCD_RETRY_DELAY    重试间隔秒数（默认 1800）

set -u

# ========= 配置 =========
WORK_DIR="${DCD_WORK_DIR:-$HOME/.dcd-trader}"
PLUGIN_DIR="${DCD_PLUGIN_DIR:-$HOME/.claude/plugins/dcd-trader}"
LOG_DIR="$WORK_DIR/logs"
TELEGRAM_ENV="${TELEGRAM_ENV:-$HOME/.claude/channels/telegram/.env}"
CLAUDE_BIN="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
MODEL="${DCD_MODEL:-claude-sonnet-4-6}"
TIMEOUT_SEC="${DCD_TIMEOUT_SEC:-1200}"
MAX_RETRIES="${DCD_MAX_RETRIES:-1}"
RETRY_DELAY="${DCD_RETRY_DELAY:-1800}"

export DCD_WORK_DIR="$WORK_DIR"

# 保证 PATH 覆盖常用工具位置（launchd 环境 PATH 很短）
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

mkdir -p "$LOG_DIR"
TS=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/dcd_cron_$TS.log"

cd "$WORK_DIR" || {
    echo "[$(date)] FATAL: cannot cd to $WORK_DIR" >&2
    exit 1
}

# 加载 Telegram bot token + chat id
if [ -f "$TELEGRAM_ENV" ]; then
    # shellcheck disable=SC1090
    source "$TELEGRAM_ENV"
else
    echo "[$(date)] WARN: telegram env not found at $TELEGRAM_ENV" | tee -a "$LOG_FILE"
fi

# ========= Telegram 推送 =========
send_telegram() {
    local text="$1"
    if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
        echo "[$(date)] SKIP telegram: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID missing" | tee -a "$LOG_FILE"
        return
    fi
    # Telegram 单条消息上限 4096 字符，保守截到 3800
    if [ "${#text}" -gt 3800 ]; then
        text="${text:0:3800}

...（已截断）"
    fi
    curl -sS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
        --data-urlencode "text=${text}" \
        >> "$LOG_FILE" 2>&1
}

# ========= 执行 Claude（含重试）=========
SKILL_FILE="$PLUGIN_DIR/skills/dcd-daily-trade/SKILL.md"
MCP_CONFIG="$PLUGIN_DIR/.mcp.json"

if [ ! -f "$SKILL_FILE" ]; then
    echo "[$(date)] FATAL: skill file missing: $SKILL_FILE" | tee -a "$LOG_FILE"
    exit 2
fi

run_claude() {
    local attempt=$1
    echo "[$(date)] === DCD cron attempt $((attempt+1))/$((MAX_RETRIES+1)) (timeout=${TIMEOUT_SEC}s) ===" | tee -a "$LOG_FILE"
    echo "[$(date)] Model: $MODEL | work_dir: $WORK_DIR | plugin_dir: $PLUGIN_DIR" | tee -a "$LOG_FILE"

    # 解锁 Keychain（防屏幕锁定时 Claude 读 token 卡死）
    security unlock-keychain "$HOME/Library/Keychains/login.keychain-db" 2>/dev/null

    # gtimeout 防 MCP 死锁
    # --strict-mcp-config 只加载 okx-trade-mcp-live
    gtimeout "$TIMEOUT_SEC" "$CLAUDE_BIN" \
        --model "$MODEL" \
        --dangerously-skip-permissions \
        --strict-mcp-config \
        --mcp-config "$MCP_CONFIG" \
        --print \
        "读取并执行 $SKILL_FILE 中的完整指令。工作目录: $WORK_DIR" \
        >> "$LOG_FILE" 2>&1
    return $?
}

# ========= 首次执行 =========
run_claude 0
EXIT_CODE=$?
echo "[$(date)] === DCD cron end (exit=$EXIT_CODE) ===" | tee -a "$LOG_FILE"

# ========= 失败重试 =========
if [ "$EXIT_CODE" -ne 0 ]; then
    for i in $(seq 1 "$MAX_RETRIES"); do
        send_telegram "⚠️ DCD 第 $((i)) 次执行失败 (exit=$EXIT_CODE)，${RETRY_DELAY}秒后自动重试..."
        echo "[$(date)] Retry $i/$MAX_RETRIES: sleeping ${RETRY_DELAY}s..." | tee -a "$LOG_FILE"
        sleep "$RETRY_DELAY"

        run_claude "$i"
        EXIT_CODE=$?
        echo "[$(date)] === DCD cron retry $i end (exit=$EXIT_CODE) ===" | tee -a "$LOG_FILE"

        if [ "$EXIT_CODE" -eq 0 ]; then
            break
        fi
    done
fi

# ========= 推送结果 =========
REPORT_FILE="$WORK_DIR/last_run_report.md"

if [ "$EXIT_CODE" -eq 0 ] && [ -f "$REPORT_FILE" ]; then
    REPORT=$(cat "$REPORT_FILE")
    send_telegram "$REPORT"

elif [ "$EXIT_CODE" -eq 124 ]; then
    send_telegram "🔴 DCD 定时任务超时（${TIMEOUT_SEC}秒）被强制终止（已用完重试次数）

需要手动补单！

日志：$(tail -5 "$LOG_FILE" 2>/dev/null)"

else
    TAIL=$(tail -n 20 "$LOG_FILE" 2>/dev/null)
    send_telegram "🔴 DCD 定时任务失败 (exit=$EXIT_CODE)，已重试 ${MAX_RETRIES} 次仍未成功

需要手动补单！

$TAIL"
fi

exit $EXIT_CODE
