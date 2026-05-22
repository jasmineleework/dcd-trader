# AGENTS.md — DCD Trader 运行约定

本文件是 plugin 自带的"Agent 运行通用规则"，给任何 LLM agent（不限 Claude Code）在执行 DCD 自动交易 / 周报 skill 时参考。**不含任何个人数据**，所有个性化参数从 `$DCD_WORK_DIR/data/ledger.json` 的 `meta` 段动态读取。

## 1. 目录约定

| 角色 | 路径 |
|------|------|
| Plugin 源（只读） | `${CLAUDE_PLUGIN_ROOT}/` |
| 用户工作目录（读写） | `$DCD_WORK_DIR`（默认 `~/.dcd-trader`） |
| 策略文档 | `${CLAUDE_PLUGIN_ROOT}/DCD策略.md`（唯一权威规则） |
| 交易叙事日志 | `$DCD_WORK_DIR/交易记录.md`（人读） |
| 结构化账本 | `$DCD_WORK_DIR/data/ledger.json`（机器读） |
| 每日执行报告 | `$DCD_WORK_DIR/last_run_report.md` |
| 每周报告 | `$DCD_WORK_DIR/last_weekly_report.md` |
| 日志 | `$DCD_WORK_DIR/logs/` |
| 8h 扫描启用 flag | `$DCD_WORK_DIR/.idle_scan_enabled`（touch / rm 切换） |

## 2. 自动交易硬性约束（不可违反）

违反任一条 → **跳过本轮，不下单**：

- PUT 行权价必须低于安全下界（基于预测波动率计算）
- PUT 安全距离 ≥ 3.5%（事件前后 ≥ 5%）
- PUT 风险评级必须为 ✅（行权价 < 安全下界）
- 重大事件当天 PUT 不下单
- 持仓期间（购买日 → 到期日）不跨重大事件
- PUT 投入按"3–5 份轮动"规则切分 USDG 资金池（详见 `DCD策略.md`）；USDT 资金池 < 3,000 时仍全仓单仓滚动
- CALL 投入 = 全部可用 BTC（向下取整 0.0001）
- CALL 行权价按分级策略（CLOSE / FAR）选择（参见 `DCD策略.md`）
- 每笔下单后必须更新 `交易记录.md` + `ledger.json`（双写）

## 3. Ledger 双写规范

每笔订单状态变更（开仓 / 结算）都要同时更新两处：

- **`交易记录.md`**：叙事段落（含中文复盘文字、表格行）
- **`data/ledger.json`**：结构化字段（`orders[]`、`snapshots[]`），通过 `${CLAUDE_PLUGIN_ROOT}/scripts/ledger_append.py` 写入

`ledger_append.py` 入口：`open` / `settle` / `snapshot` / `injection`，payload 从 stdin 读 JSON。**禁止 LLM 直接编辑 `ledger.json`**（JSON 转义易错）。

字段定义见 `${CLAUDE_PLUGIN_ROOT}/data-template/ledger.schema.md`。

## 4. 8h 闲置扫描 SOP

**触发条件**：每次定时任务执行完毕后，若资金账户存在显著闲置资金（USDG ≥ 1,000 或 USDT ≥ 1,000 或 BTC ≥ 0.0001），但**未完成对应仓位的部署**，则 `touch $DCD_WORK_DIR/.idle_scan_enabled` 启用 8h 扫描。

**取消条件**：扫描后若所有闲置资金都成功部署（资金账户余额回到零头水平 < 100），则 `rm -f $DCD_WORK_DIR/.idle_scan_enabled` 停用扫描，回归每日主 cron 流程。

**目的**：捕获日内波动收敛带来的窗口（如某日深夜 ATR 高 → 跳过；次日早上 ATR 回落 → 重新出现合规产品）。

## 5. 累积年化算法（Modified Dietz）

**起算点**：v3 算法生效日 `d_0_v3 = meta.strategy_v3_effective_date`，把当时账户净值 `P_0_v3 = meta.p0_v3_usd` 视为虚拟初始注资。

**公式**：

```
T_days_v3 = (今天 − d_0_v3).days

虚拟注资集 = [(d_0_v3, P_0_v3),
            *[(inj.date, inj.amount_usd_eq) for inj in injections if inj.date >= d_0_v3]]

P_v3 = Σ amount_i

对每笔注资 i：
  days_in_pool_i = (今天 − date_i).days
  weight_i       = days_in_pool_i / T_days_v3

分母         = Σ amount_i × weight_i
PnL_v3_total = V_now − P_v3            # V_now = 当前总资产 USD 等值
ROR_v3       = PnL_v3_total / 分母
v3 累积年化  = ROR_v3 × 365 / T_days_v3
```

**口径要点**：
- 全部按 USDG 等值（稳定币 USDT/USDG 视为 1:1，BTC 按实时价折算）
- 在仓订单按"投入金额"入账（双币赢到期前本金锁定，无公允市值）
- 累积净收益 = v3 后策略已实现 + BTC 持仓浮动盈亏（合并计入，单列展示）
- **v3 切换前订单的 PnL 已并入 P_0_v3，不重复计算**

**注资记录方式**：通过 `/dcd-trader:dcd-injection <amount> <date>` 命令记录，自动写 `ledger.json` 的 `injections[]` + 同步追加到 `交易记录.md` 顶部注资表。

**策略升级到 v4**：需更新 `ledger.json` 的 `meta.strategy_version`、`meta.strategy_v3_effective_date`（或新增 `v4_effective_date`）、`meta.p0_v3_usd`（或 `p0_v4_usd`），并调整 `calc_apy.py` 切分逻辑。

## 6. Skills 与 Commands 一览

| 入口 | 类型 | 作用 |
|------|------|------|
| `/dcd-trader:dcd-setup` | command | 首次初始化 `$DCD_WORK_DIR` |
| `/dcd-trader:dcd-injection <amt> <date>` | command | 记录注资/提取（负数为提取） |
| `/dcd-trader:dcd-daily` | command | 手动触发日交易（等同 launchd cron） |
| `/dcd-trader:dcd-weekly` | command | 手动触发周报 |
| `skills/dcd-daily-trade/SKILL.md` | skill | 每日自动交易完整流程 |
| `skills/dcd-weekly-report/SKILL.md` | skill | 每周报告生成 |
| `skills/dcd-8h-idle-scan/SKILL.md` | skill | 8h 闲置扫描 |

## 7. 对 LLM 的硬性要求

- 始终使用 `okx-trade-mcp-live`（实盘），不要切到 demo
- 时间统一 UTC+8
- 下单失败不重试，记录原因
- 连续被行权 2 次后暂停 1 天
- BTC 跌至距行权价 < 1% 时在记录中提醒关注
- **不可与用户交互**（在 launchd 触发下），全流程自主决策
- 重大事件当天 PUT 不下单（FOMC / CPI / NFP / CME 期权到期）
