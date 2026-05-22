---
description: 手动触发每日 DCD 自动交易流程（与 launchd cron 等价，但前台交互式）
allowed-tools: Bash, Read, Write, Edit, WebSearch, mcp__okx-trade-mcp-live__*
---

## /dcd-trader:dcd-daily — 手动执行每日交易

直接执行 plugin 内置的 `dcd-daily-trade` skill：

读取并按 `${CLAUDE_PLUGIN_ROOT}/skills/dcd-daily-trade/SKILL.md` 的完整指令执行。

确保已设置环境变量：
```bash
export DCD_WORK_DIR="${DCD_WORK_DIR:-$HOME/.dcd-trader}"
```

> 注意：本命令与 launchd 触发的 `run_dcd_cron.sh` 行为一致，但运行在当前 Claude Code 会话中，会受当前会话的权限限制。生产部署应使用 launchd 定时任务。
