# DCD Trader

OKX **双币赢（Dual-Currency Deposit）** 自动化交易 Claude Code plugin。PUT + CALL 双线策略，自动结算复盘、每周报告、闲置资金 8h 扫描。

> ⚠️ **风险提示 / Disclaimer**
>
> 本项目涉及加密货币衍生品（双币赢期权产品），波动剧烈、可能造成本金亏损。代码仅供学习与研究使用，作者不对任何使用本工具产生的财务损失负责。**实盘使用前请在小额资金上充分测试，并自行判断是否符合所在司法管辖区的法律法规。**
>
> This project trades crypto derivatives (dual-currency deposit options). Markets are volatile and you may lose principal. The code is for educational and research purposes; the authors accept no liability for financial losses. Test thoroughly with small amounts before live use, and verify legality in your jurisdiction.

---

## 这是什么

DCD 双币赢是一种"赚利息可能换币"的结构化产品：
- **PUT**：用稳定币（USDG/USDT）抵押，到期 BTC 价高于行权价 → 拿利息；低于行权价 → 用稳定币按行权价买入 BTC
- **CALL**：用 BTC 抵押，到期价低于行权价 → 拿利息；高于行权价 → 把 BTC 按行权价卖出换回稳定币

本 plugin 实现一套基于波动率预测的双线策略：
- **PUT 线**：行权价低于"安全下界"（v3 波动率引擎计算），赚稳定币 APY
- **CALL 线**：分 CLOSE（保本卖出）/ FAR（远离回本）两档，BTC 持仓时主动降本

完整规则见 [DCD策略.md](DCD策略.md)。

## 功能

- 🤖 **自动交易**：每日 launchd cron 触发，自主复盘上一轮 → 选品 → 下单 → 写账本
- 📊 **结构化账本**：`data/ledger.json` 存所有订单/注资/快照，Markdown 叙事日志双写
- 📈 **每周报告**：Modified Dietz 累积年化、本周净收益、当前仓位风险，自动推 Telegram
- 🔄 **8h 闲置扫描**：主 cron 因波动率过高跳过时，每 8 小时重扫一次捕获再开仓窗口
- 🛡️ **硬性约束**：安全距离 ≥ 3.5%（事件前后 5%）、PUT 风险评级必须为 ✅、重大事件日规避

## 依赖

- macOS（launchd 调度）；Linux 需自行换 systemd timer
- [Claude Code](https://claude.com/claude-code) CLI
- Node.js 18+（运行 OKX MCP server）
- Python 3.10+（运行账本脚本）
- OKX 账户 + API key（live profile）
- 可选：Telegram bot（推送报告）

## 安装

```bash
/plugin install jasmineleework/dcd-trader
```

或本地开发：

```bash
git clone https://github.com/jasmineleework/dcd-trader
claude --plugin-dir ./dcd-trader
```

## 首次配置

1. **配置 OKX API key**（live profile）：

   ```bash
   npx -y @okx_ai/okx-trade-mcp setup --profile live
   ```

2. **初始化工作目录**：

   ```
   /dcd-trader:dcd-setup
   ```

   会在 `~/.dcd-trader/`（或 `$DCD_WORK_DIR`）创建账本和交易记录模板，并引导填写 `meta.strategy_v3_effective_date` / `meta.p0_v3_usd`。

3. **配置 Telegram**（可选）：在 `~/.claude/channels/telegram/.env` 写入

   ```bash
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id
   ```

4. **记录首笔注资**：

   ```
   /dcd-trader:dcd-injection 10000 2026-05-22
   ```

5. **注册 launchd 定时任务**：

   ```bash
   cp shell/launchd/com.dcd.daily-trade.plist.example ~/Library/LaunchAgents/com.<your-name>.dcd-daily-trade.plist
   # 编辑 plist，把所有 CHANGE_ME 替换成你的路径
   launchctl load ~/Library/LaunchAgents/com.<your-name>.dcd-daily-trade.plist
   ```

   8h 扫描和周报同理。

6. **手动测试**：

   ```
   /dcd-trader:dcd-daily
   ```

## 用法

- 每日 cron 自动跑（launchd 触发 → `shell/run_dcd_cron.sh` → 调起 `skills/dcd-daily-trade`）
- 手动触发：`/dcd-trader:dcd-daily`
- 周报：`/dcd-trader:dcd-weekly` 或周日 20:00 自动跑
- 注资/提取：`/dcd-trader:dcd-injection <amount> <date>`

## 文件说明

| 路径 | 作用 |
|------|------|
| [DCD策略.md](DCD策略.md) | 策略文档（唯一权威规则） |
| [AGENTS.md](AGENTS.md) | Agent 运行通用规则 |
| `skills/dcd-daily-trade/SKILL.md` | 每日交易完整流程 |
| `skills/dcd-weekly-report/SKILL.md` | 周报生成 |
| `skills/dcd-8h-idle-scan/SKILL.md` | 闲置资金扫描 |
| `commands/dcd-*.md` | 4 个 slash 命令 |
| `scripts/calc_volatility.py` | v3 波动率引擎 |
| `scripts/calc_apy.py` | Modified Dietz 累积年化 |
| `scripts/ledger_append.py` | 账本写入工具 |
| `scripts/backfill_ledger.py` | 从交易记录.md 回填 ledger |
| `data-template/` | 用户工作目录初始模板 |
| `backtest/` | 历史回测脚本（参考） |
| `shell/launchd/*.plist.example` | launchd 定时任务模板 |

## 环境变量

| 变量 | 默认值 | 作用 |
|------|--------|------|
| `DCD_WORK_DIR` | `~/.dcd-trader` | 用户工作目录（账本、交易记录、日志） |
| `DCD_PLUGIN_DIR` | `~/.claude/plugins/dcd-trader` | Plugin 安装路径（shell 脚本用） |
| `DCD_MODEL` | `claude-sonnet-4-6` | Claude 模型 |
| `DCD_TIMEOUT_SEC` | `1200` | 主 cron 硬超时 |
| `TELEGRAM_ENV` | `~/.claude/channels/telegram/.env` | Telegram 配置文件 |

## OKX MCP — 本地 vs 远程

当前 `.mcp.json` 使用本地 STDIO server：

```json
"command": "npx",
"args": ["-y", "@okx_ai/okx-trade-mcp", "--profile", "live", "--modules", "all"]
```

OKX 官方[宣布](https://github.com/okx/agent-trade-kit) cloud-hosted MCP "coming soon"。上线后切换为：

```json
"url": "https://mcp.okx.com/agent-trade-kit",
"headers": { "Authorization": "Bearer ${OKX_MCP_TOKEN}" }
```

## 贡献

欢迎 issue / PR。讨论交易策略变更请先开 issue 描述真实回测数据。

## License

[MIT](LICENSE)
