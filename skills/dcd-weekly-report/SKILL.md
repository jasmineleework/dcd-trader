---
name: dcd-weekly-report
description: 生成 DCD 双币赢每周投资报告 — 读 ledger.json 计算累积 PnL 和 Modified Dietz 年化，覆盖写入 last_weekly_report.md。只读 + 计算 + 写报告，不下单不划转。
allowed-tools: Bash, Read, Write, mcp__okx-trade-mcp-live__market_get_index_ticker
---

## DCD 双币赢 — 每周投资报告生成流程（V1 / ledger.json 驱动）

**目标**：生成跨期累积视角的周报，覆盖写入 `$DCD_WORK_DIR/last_weekly_report.md`，由包装脚本自动推送 Telegram。

**关键性能**：所有数据已结构化在 `$DCD_WORK_DIR/data/ledger.json` 里。**不要 grep `交易记录.md` 全文**，直接读 JSON。预期 LLM 工作量：2–4 分钟。

**只读 + 计算 + 写报告**：不要下单、不要划转、不要修改 `交易记录.md` 或 `ledger.json`。

---

### Phase 1：取数

#### 1.1 主数据源 `$DCD_WORK_DIR/data/ledger.json`

```bash
Read $DCD_WORK_DIR/data/ledger.json
```

字段说明见 `${CLAUDE_PLUGIN_ROOT}/data-template/ledger.schema.md`。核心使用：

- `meta.strategy_v3_effective_date`（v3 算法起算日 d_0）
- `meta.p0_v3_usd`（v3 启动时账户净值，作为虚拟初始注资 P_0）
- `injections[]`：外部注资记录（已含日期+USD 等值）
- `orders[]`：每笔订单的 deposit / return / pnl_usd_eq / pnl_category / state
- `snapshots[]`：（日 cron 写入）每日资产快照，可用作交叉验证

> 若发现 `ledger.orders` 数量明显少于交易记录里的最新订单序号，说明日 cron 还没把新订单写入 JSON — 继续往下用 `last_run_report` 取在仓金额即可，**不要尝试手工补 ledger**。

#### 1.2 当前资产快照（在仓金额 + 资金账户余额）

```bash
Read $DCD_WORK_DIR/last_run_report.md
```

从"📈 当前资产快照"段提取：USDG 资金账户、USDT 资金账户、BTC 余额、在仓 USDG 合计、在仓 USDT 合计。

#### 1.3 BTC 实时价

调 `mcp__okx-trade-mcp-live__market_get_index_ticker` `instId=BTC-USD`，取 `idxPx`。

> Fallback：MCP 失败则用 `last_run_report.md` 里最近的 BTC 价，注明"快照价"。

---

### Phase 2：算（推荐用 calc_apy.py 一次性算好）

**优先方案**：直接调脚本一次取得所有数字 —

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/calc_apy.py \
  --ledger "$DCD_WORK_DIR/data/ledger.json" \
  --v-now <V_now USD等值> \
  --btc-price <BTC现价>
```

返回 JSON：`{P_v3, V_now, PnL_v3_total, PnL_stable, PnL_btc_flow, PnL_btc_floating, T_days_v3, denominator, ROR_v3, annualized_v3, cross_check_annualized}`。

**手算口径（若脚本不可用）**：

#### A. v3 虚拟注资集

```
P_0_v3 = ledger.meta.p0_v3_usd
d_0_v3 = ledger.meta.strategy_v3_effective_date
注资集 = [(d_0_v3, P_0_v3),
         *[(inj.date, inj.amount_usd_eq) for inj in ledger.injections if inj.date >= d_0_v3]]
P_v3 = Σ amount
```

#### B. 当前总资产 V_now（USDG 等值）

```
V_now = USDG_funding + USDG_在仓
      + USDT_funding + USDT_在仓
      + BTC_余额 × BTC_现价
```

#### C. v3 累积净收益（含分类）

```
PnL_v3_total = V_now − P_v3

V3_FIRST_SEQ = ledger.meta 中标注的 v3 首笔订单序号（若无，按 effective_date 切分）

PnL_stable    = Σ orders[seq≥V3_FIRST_SEQ AND state=settled AND pnl_category ∈ {stable_profit, stable_floating_loss}].pnl_usd_eq
PnL_btc_flow  = Σ orders[seq≥V3_FIRST_SEQ AND state=settled AND pnl_category ∈ {btc_premium, btc_recovery}].pnl_usd_eq
PnL_btc_float = PnL_v3_total − PnL_stable − PnL_btc_flow   # ≈ BTC 持仓未实现浮动 + 历史余额价差
```

> 用 Python 一次性算好（Bash 调 `python3 -c "import json; ..."`），不要让 LLM 心算累加。

#### D. v3 累积年化（Modified Dietz）

```
T_days_v3 = (今天 − d_0_v3).days

对每笔虚拟注资 i：
  days_in_pool_i = (今天 − date_i).days
  weight_i       = days_in_pool_i / T_days_v3

分母 = Σ amount_i × weight_i
ROR_v3      = PnL_v3_total / 分母
v3 累积年化 = ROR_v3 × 365 / T_days_v3
```

#### E. 交叉验证（应与 D 一致，仅舍入误差）

```
平均部署本金 = Σ amount_i × days_in_pool_i / T_days_v3
年化_验证    = PnL_v3_total / 平均部署本金 × 365 / T_days_v3
```

差 >1pp 标红 ⚠️ 列中间量。

#### F. 本周维度（最近 7 天，T−7 起）

```
本周结算订单 = [o for o in ledger.orders
              if o.state == "settled"
              and o.settle_time 在 [T−7天 00:00, T 23:59]]
本周净收益   = Σ 本周结算订单.pnl_usd_eq
本周行权/未行权 = 统计 exercised 字段
本周平均 APY = mean(o.apy_pct for o in 本周结算订单)
周化年化     ≈ (本周净收益 / V_now) × 365 / 7
```

#### G. 当前仓位（从 last_run_report 提取）

- 在仓笔数 N
- 锁定金额合计 USDG 等值
- 加权剩余到期 / 最小安全距离：从 last_run_report 文本提取

---

### Phase 3：覆盖写入 `$DCD_WORK_DIR/last_weekly_report.md`

格式（≤2500 字）：

```markdown
# 📊 DCD 周报 — <YYYY-MM-DD> 第 N 周

## 💰 累积业绩（v3 算法口径，自 d_0_v3 起算）

- v3 累积投入：**P_v3 USD 等值**（P_0_v3 @ d_0_v3 + 各笔注资）
- 当前总资产：**V_now USDG 等值**
  - 资金账户：USDG XX / USDT XX / BTC X.XXXX（@ $XX,XXX）
  - 在仓锁定：USDG X,XXX + USDT X,XXX
- v3 累积净收益：**+/-XXX USDG（+/-X.XX%）**
  - 策略已实现（稳定币口径，ledger 精确）：+/-XXX USDG
  - BTC 流转已实现（v3 后 CALL 权利金 + 行权回收）：+/-XX USDG
  - BTC 持仓浮动（含历史余额价差）：+/-XX USDG
- **v3 累积年化收益率：XX.XX%**（Modified Dietz，自 d_0_v3 起算）
  - 交叉验证（加权平均本金）：XX.XX% ✅

> v3 切换前订单的 PnL 已并入 P_0_v3，不计入累积收益。
> 数据源：`data/ledger.json`，PnL 累加由 calc_apy.py 预计算。

## 📅 本周（M/D – M/D）

- 结算笔数：N 笔（行权 X / 未行权 Y）
- 本周净收益：+/-XX USDG
- 周化年化：XX.XX%
- 平均 APY：XX%

## 📦 当前仓位

- 在仓订单：N 笔，锁定 X,XXX USDG 等值
- 加权剩余到期：X.X 天（从 last_run_report 提取）
- 最小安全距离：X.XX%（从 last_run_report 提取）

## ⚠️ 风险提示

<从 last_run_report 的"⚠️ 关注事项"段摘要。无重大风险则写"无重大风险">

---

> **算法**：累积年化用 Modified Dietz，以 v3 算法生效日 d_0_v3 为起算点 — 把当时账户净值 P_0_v3 视为虚拟初始注资，后续追加注资按"到今天在场天数"加权得到等效平均本金，换算 365 天口径。
```

---

### Phase 4：结束

**完成标志**：`$DCD_WORK_DIR/last_weekly_report.md` 已覆盖写入。脚本会自动推 Telegram。**不要**修改 `交易记录.md` / `ledger.json`，不要下单。
