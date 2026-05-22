---
description: 手动触发每周投资报告生成（与 launchd 周日 20:00 触发等价）
allowed-tools: Bash, Read, Write, mcp__okx-trade-mcp-live__market_get_index_ticker
---

## /dcd-trader:dcd-weekly — 手动生成周报

读取并按 `${CLAUDE_PLUGIN_ROOT}/skills/dcd-weekly-report/SKILL.md` 的完整指令执行。

确保已设置环境变量：
```bash
export DCD_WORK_DIR="${DCD_WORK_DIR:-$HOME/.dcd-trader}"
```

输出会覆盖写入 `$DCD_WORK_DIR/last_weekly_report.md`。

> 注意：本命令只读 + 计算 + 写报告，不会下单、不会修改 ledger.json 或 交易记录.md。
