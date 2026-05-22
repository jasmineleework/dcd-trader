---
description: 初始化 DCD trader 用户工作目录 — 创建 $DCD_WORK_DIR、复制账本模板和交易记录模板
allowed-tools: Bash, Read, Write
---

## /dcd-trader:dcd-setup — 首次配置

完成以下步骤：

### 1. 确定工作目录

读取环境变量 `DCD_WORK_DIR`：
```bash
WORK_DIR="${DCD_WORK_DIR:-$HOME/.dcd-trader}"
echo "Using DCD_WORK_DIR: $WORK_DIR"
```

如果 `$WORK_DIR` 已存在且包含 `data/ledger.json`，**提示用户该目录已初始化**，询问是否覆盖（默认不覆盖，直接退出）。

### 2. 创建目录结构

```bash
mkdir -p "$WORK_DIR/data" "$WORK_DIR/logs"
```

### 3. 复制模板

```bash
cp "${CLAUDE_PLUGIN_ROOT}/data-template/ledger.example.json" "$WORK_DIR/data/ledger.json"
cp "${CLAUDE_PLUGIN_ROOT}/data-template/ledger.schema.md" "$WORK_DIR/data/ledger.schema.md"
cp "${CLAUDE_PLUGIN_ROOT}/data-template/trading_log.template.md" "$WORK_DIR/交易记录.md"
```

### 4. 引导用户填写 meta

读取 `$WORK_DIR/data/ledger.json`，引导用户填写 `meta` 段：
- `strategy_v3_effective_date`：什么日期开始用 v3 算法（如今天 → ISO 日期 `YYYY-MM-DD`）
- `p0_v3_usd`：v3 起算日的账户总净值（USD 等值）。**新用户填 `0`**，老用户填迁移当时的总资产
- `last_updated`：写入当前 UTC+8 时间戳

更新后调用：
```bash
python3 -c "
import json, sys, os
from datetime import datetime, timezone, timedelta
p = os.path.expandvars(os.path.expanduser('$WORK_DIR/data/ledger.json'))
with open(p) as f: d = json.load(f)
d['meta']['strategy_v3_effective_date'] = '<用户填写>'
d['meta']['p0_v3_usd'] = <用户填写>
d['meta']['last_updated'] = datetime.now(timezone(timedelta(hours=8))).isoformat()
with open(p, 'w') as f: json.dump(d, f, ensure_ascii=False, indent=2)
"
```

### 5. 提示后续步骤

报告以下内容给用户：

```
✅ DCD Trader 工作目录已初始化于 $WORK_DIR

下一步：
1. 配置 OKX API key：在终端运行 `npx @okx_ai/okx-trade-mcp setup --profile live`
2. 配置 Telegram（可选）：在 $HOME/.claude/channels/telegram/.env 写入
     TELEGRAM_BOT_TOKEN=xxx
     TELEGRAM_CHAT_ID=xxx
3. 记录首笔注资：/dcd-trader:dcd-injection <amount> <date>
4. 注册 launchd 定时任务：复制 ${CLAUDE_PLUGIN_ROOT}/shell/launchd/*.plist.example
   到 ~/Library/LaunchAgents/，修改 label 为 com.<your-name>.dcd-*，然后
   `launchctl load ~/Library/LaunchAgents/com.<your-name>.dcd-daily-trade.plist`
5. 手动测试：/dcd-trader:dcd-daily
```
