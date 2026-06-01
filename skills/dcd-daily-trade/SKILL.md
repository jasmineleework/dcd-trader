---
name: dcd-daily-trade
description: 每日 DCD 双币赢自动交易完整流程 — 复盘上轮结算 → 反思 → 选品下单（PUT + CALL）→ 更新账本 → 8h 闲置扫描启停控制 → 生成执行报告。由 launchd 每日定时触发，或用户手动 /dcd-trader:dcd-daily 调用。
allowed-tools: Bash, Read, Write, Edit, WebSearch, mcp__okx-trade-mcp-live__*
---

## DCD 双币赢全自动交易 — 每日执行流程

**约定**：
- 工作目录 `$DCD_WORK_DIR`（默认 `~/.dcd-trader`），账本 `$DCD_WORK_DIR/data/ledger.json`，叙事日志 `$DCD_WORK_DIR/交易记录.md`
- 脚本工具在 `${CLAUDE_PLUGIN_ROOT}/scripts/`（plugin 安装目录）
- 始终使用 `okx-trade-mcp-live`（实盘），时间 UTC+8

**必读文件**：先完整阅读 `${CLAUDE_PLUGIN_ROOT}/DCD策略.md`（唯一权威规则来源）和 `$DCD_WORK_DIR/交易记录.md`（历史记录+合并有效成本）。

---

### Phase 1：复盘上一轮（检查结算 + 更新记录）

1. 调用 `dcd_get_orders`（live）查询最近订单，找到所有已结算（state=settled）但尚未写入交易记录的订单（对比 `交易记录.md` 中的订单 ID）
2. 对每笔已结算订单：
   - 记录结算价、结果（行权/未行权）、收到金额、权利金
   - **PUT 被行权**：计算收到的 BTC 数量，并入合并池，重新计算合并有效成本；**在「合并有效成本」区块的 BTC 池构成表追加一行**（并入日 = 今日、投入USDG = 该 PUT 的 deposit、BTC量），供 CLOSE 档算 T_eff
   - **PUT 未行权**：记录 USDG/USDT 收益
   - **CALL 被行权**：BTC 已卖出换回 USDG，记录回收金额，清空 BTC 合并池
   - **CALL 未行权**：权利金 BTC 并入合并池，更新合并有效成本（公式：总投入USDG ÷ 总持有BTC）
3. 更新 `$DCD_WORK_DIR/交易记录.md`：
   - 将持仓中(🔵)的订单更新为结算结果(✅/❌)
   - 更新汇总表中对应行
   - 更新合并有效成本
   - 更新当前资产快照

### Phase 2：简要反思

在交易记录中写入 2-3 句复盘：
- 安全距离是否充足？结算价距行权价的余量
- 波动率预测 vs 实际波动：是否低估/高估？
- 如有险过或被行权，分析根因

### Phase 3：执行本轮交易

按 `DCD策略.md` 的执行流程（Step 1-4）操作：

**Step 1：并行获取数据**
- `market_get_index_ticker`（BTC-USD）→ 现价
- `market_get_index_candles`（BTC-USD, bar:1D, limit:30）→ 30天高点 + 7天前价格
- `market_get_indicator`（BTC-USD-SWAP, ATR, period:7, bar:1H）→ 快速 ATR
- `market_get_indicator`（BTC-USD-SWAP, ATR, period:14, bar:1H）→ 慢速 ATR
- `market_get_indicator`（BTC-USD-SWAP, BOLLINGER, period:20, bar:1H）→ BB
- `option_get_greeks`（BTC 近月 ATM）→ IV
- `market_get_funding_rate`（BTC-USD-SWAP）→ 资金费率
- `dcd_get_products`（BTC, USDG, P）+ `dcd_get_products`（BTC, USDT, P）→ PUT 产品
- `dcd_get_products`（BTC, USDG, C）+ `dcd_get_products`（BTC, USDT, C）→ CALL 产品
- `account_get_asset_balance`（USDG,USDT,BTC）→ 余额
- WebSearch → 近期事件日历（FOMC/CPI/NFP/CME）

**Step 2：计算波动率 + 安全下界**
- 调 `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/calc_volatility.py --price X --iv-annual X --atr-fast X --atr-slow X --bb-upper X --bb-lower X --funding-rate X --high-30d X --price-7d-ago X --event-mult X --min-dist X`
- 确定事件乘数（查事件乘数表）和最低安全距离（无事件 3.5%，事件前后 5%）

**Step 3：选品 + 下单**

⚠️ **MCP 单位 bug 警示**：
- OKX 返回的 `annualizedYield` 是**小数形式**（0.1748 = 17.48% APY）
- **真实 APY（百分比）= annualizedYield × 100**
- **禁止**给 `dcd_subscribe` 传 `minAnnualizedYield` 参数（工具端单位解析错误，会误拒）
- 所有 APY 过滤用 list 的 `annualizedYield × 100 >= 5%` 判断

策略 A（PUT，交错到期 + 自动互换）：

**Step 3a：确认可用资金**
- 查 `account_get_asset_balance`（USDG, USDT）
- 只对**今天有到期结算或闲置**的资金下单（已在仓的资金不动）
- 资金账户无 USDG/USDT 时，从交易账户划转：`account_transfer`（from:18, to:6）

**Step 3b：选品**
- 查看手上持有哪些币种（USDG / USDT / 两者都有）
- 每种持有币种独立选品：拉该币种的 PUT 产品列表，选最优产品
- **不做 USDG↔USDT 互换**（手续费 0.1% 远超 APY 差额收益）
- 同时持有 USDG 和 USDT 时：各自独立下单（形成交错到期的天然双仓）

**Step 3c：下单**
- 只选 ✅（行权价 < 安全下界），APY 在 [5%, 150%] 区间（list `annualizedYield × 100`）
- **到期日选择**：优先选与当前在仓 PUT **错开 1 天到期**的产品（交错到期）
  - 另一仓位明天到期 → 本仓选 2 天（后天到期）
  - 无其他在仓 PUT → 优先 1 天
  - 1D/2D 都不满足安全距离 → 尝试 3D-5D（不轻易跳过）
- 投入按 USDG 池"3–5 份轮动"规则切分（详见 `DCD策略.md`）；USDT 池 < 3000 时全仓单仓滚动
- `dcd_subscribe`（**不传 minAnnualizedYield**，notionalCcy: 实际持有币种）
- 硬性约束：安全距离 >= min_dist，行权价 < 安全下界

策略 B（CALL，分级选品）：

先计算：
- `gap_pct = (breakeven - price) / price`（breakeven = 合并有效成本，从 `交易记录.md` 读取）
- `one_sigma_1d = vol_24h`（v3 引擎返回的 1 天预测波动率）

**CLOSE 档**（`gap_pct <= one_sigma_1d`，含 BTC 高于回本）：
- 目标：保本退出 **+ 闭环达标 10% 年化**（收回 USDG >= 最初投入 USDG ×(1+10%/365×T_eff)）
- 先算 `T_eff`（投入加权平均持有天数）：
  - 从当前 BTC 池各批的（并入日、投入USDG）按投入加权：`T_eff = Σ(各批投入USDG × 各批持有天数) / Σ各批投入USDG`
  - 单批时 `T_eff = 今天 − 该批 BTC 并入日`（= 对应 PUT 被行权日）
  - 各批数据从「合并有效成本」区块的 BTC 池构成表读（单批见交易记录激活点；多批见构成表）
- `K_target = breakeven ×(1 + 0.10/365 × T_eff)`
- strike_suggestion = `ceil(K_target / 500) × 500`
- 选最接近且 >= strike_suggestion 的可用产品

**FAR 档**（`gap_pct > one_sigma_1d`，BTC 远低于回本）：
- 目标：最大化权利金降本，行权概率 10-15%
- `sell_dist = max(vol_24h × √到期天数 × 事件乘数, min_dist)`
- `upper_bound = price × (1 + sell_dist)`
- strike_suggestion = `ceil(upper_bound / 500) × 500 + 500`
- 选最接近且 >= strike_suggestion 的可用产品
- **接受尾部风险**：strike 低于 breakeven，意外行权时小幅亏损卖出
- **FAR 档不受 10% 年化约束**：不打算这轮退出，只攒权利金降本；意外行权那一轮不保证达标，属可接受尾部风险

通用：
- BTC >= 0.0001 时执行，全仓（向下取整 0.0001）
- 优先 1 天产品，比较 USDG/USDT 选 APY 高的
- `dcd_subscribe`（**不传 minAnnualizedYield**，notionalCcy: BTC）
- 下单前用 `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/calc_volatility.py --mode sell --breakeven <X> --hold-days <T_eff>` 辅助计算 strike_suggestion（CLOSE 档必传 `--hold-days`；年化目标改用 `--min-annual-yield`，默认 0.10）

**Step 4：记录**
- 新订单写入 `$DCD_WORK_DIR/交易记录.md`，格式与历史记录一致
- **APY 字段**：写入 `quote.annualizedYield × 100`（真实百分比）
- **CALL 订单**：记录 tier（CLOSE/FAR）、gap_pct、strike_suggestion vs 实际选中 strike
  - **CLOSE 档额外记录**：T_eff（持有天数）、K_target（含年化目标价）、implied_apy（= (strike/breakeven−1)×365/T_eff，核对 >= 10%）
- 更新汇总表、当前资产快照
- 如跳过某策略，记录跳过原因

**Step 4.5：同步写入 `$DCD_WORK_DIR/data/ledger.json`**（V1 数据层，供周报使用）

对 **本轮所有结算订单**（Phase 1 已结算的）+ **本轮新开仓订单**（Phase 3 下单的）：

```bash
# 结算订单：构造 JSON（含 seq, state="settled", settle_time, settled_price, exercised, return_*, pnl_usd_eq, pnl_category），通过 stdin 传入：
echo '<JSON 字符串>' | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ledger_append.py settle

# 新开仓订单：构造 JSON（含 seq, state="open", side, deposit_*, instrument_id, strike, apy_pct, open_time, expiry_time, safety_distance_pct, note）：
echo '<JSON 字符串>' | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ledger_append.py open
```

字段定义见 `${CLAUDE_PLUGIN_ROOT}/data-template/ledger.schema.md`。`pnl_category` 取值：
- `stable_profit`：PUT 未行权 → 稳定币净赚（USDT/USDG）
- `stable_floating_loss`：PUT 被行权 → 稳定币转 BTC 的瞬时差额
- `btc_premium`：CALL 未行权 → BTC 权利金（按建仓日价折 USD）
- `btc_recovery`：CALL 被行权 → BTC 卖回稳定币的 USD 差

写完后 `ledger_append.py` 自动更新 `meta.last_updated`。**ledger.json 与 `交易记录.md` 双写**：JSON 给机器/报告读，Markdown 给人读。

**Step 4.6：写入当日快照到 ledger**

```bash
echo '{"ts":"<ISO8601>","usdg_funding":<X>,"usdt_funding":<X>,"btc_balance":<X>,"btc_price_usd":<X>,"open_positions_usdg":<X>,"open_positions_usdt":<X>,"total_assets_usd_eq":<X>}' | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ledger_append.py snapshot
```

### Phase 4：8h 闲置扫描启停控制

本轮下单完成。**不要创建新的定时任务**（launchd 会自动触发下一轮）。

**8h 闲置扫描启停**（flag 文件 `$DCD_WORK_DIR/.idle_scan_enabled`）：

- 检查资金账户当前余额（部署后剩余值，不算在仓）：
  - 若 **USDG >= 1000** 或 **USDT >= 1000** 或 **BTC >= 0.0001** → 仍有闲置 →
    `touch "$DCD_WORK_DIR/.idle_scan_enabled"` （启用扫描）
  - 否则（全是零头）→
    `rm -f "$DCD_WORK_DIR/.idle_scan_enabled"` （停用扫描）
- 报告 Phase 5 末尾用一句话注明本次 flag 操作（启用 / 停用 / 维持现状）

> 物理实现：launchd 任务按固定 cron 触发，但 `run_8h_idle_scan.sh` 入口先检查 flag。flag 不存在 → 直接 exit 0 不调 Claude、不发 TG，等同于"暂停"。

---

### Phase 5：生成执行报告（必须）

将本轮摘要**覆盖写入** `$DCD_WORK_DIR/last_run_report.md`（控制在 1500 字以内，Markdown 格式），包含：

```
# DCD 执行报告 — <YYYY-MM-DD HH:MM UTC+8>

## 📊 Phase 1 复盘
- 结算订单：N 笔
- 结果：<简述>
- 合并有效成本更新：<旧 → 新，若变化>

## 💭 Phase 2 反思
<2-3 句>

## 🎯 Phase 3 本轮下单
- PUT：<产品ID / 行权价 / APY / 安全距离 / 投入> 或「跳过，原因：...」
- CALL：<tier=CLOSE|FAR / gap_pct / 产品ID / 行权价 / APY / 投入> 或「跳过，原因：...」

## 📈 当前资产快照
- USDG: X
- USDT: X
- BTC: X

## ⚠️ 关注事项
<如有险过、距行权价 < 1%、连续被行权等>
```

报告写入后任务结束。包装脚本会自动读取该文件并推送到 Telegram。

---

### 关键提醒
- 始终使用 `okx-trade-mcp-live`（实盘）
- 时间统一 UTC+8
- 下单失败不重试，记录原因
- 连续被行权 2 次后暂停 1 天
- BTC 跌至距行权价 < 1% 时在记录中提醒关注
- **不可与用户交互**，全流程自主决策
