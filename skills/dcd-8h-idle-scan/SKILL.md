---
name: dcd-8h-idle-scan
description: 每 8 小时扫描闲置资金 — 当主 cron 因波动率不达标跳过下单时，捕获日内波动收敛带来的窗口。重新拉市场数据、重新评估候选产品，合规则下单。无闲置资金时自动停用 flag。
allowed-tools: Bash, Read, Write, Edit, WebSearch, mcp__okx-trade-mcp-live__*
---

## DCD 8h 闲置扫描 — 执行流程

**触发前提**：包装脚本 `run_8h_idle_scan.sh` 已检测 flag 文件 `$DCD_WORK_DIR/.idle_scan_enabled` 存在，才会调起本 skill。

**必读文件**：先完整阅读 `${CLAUDE_PLUGIN_ROOT}/DCD策略.md` 和 `$DCD_WORK_DIR/交易记录.md`。

**核心目的**：捕获日内 ATR 回落或 BB 收敛带来的"再开仓窗口"。如 2026-05-04 23:19 跳过 → 2026-05-05 08:13 部署的真实案例：12 小时内 ATR 回落 33%，重新出现合规 + 高 APY 产品。

---

### Phase 1：复盘新结算（如有）

照 `dcd-daily-trade` 的 Phase 1 流程执行 — 查 `dcd_get_orders`，把已结算未入账的订单写入 `交易记录.md` 和 `data/ledger.json`。**没有新结算则跳过本 phase**。

### Phase 2：评估闲置资金

```bash
# 拉余额
account_get_asset_balance (USDG, USDT, BTC)
earn_get_savings_balance (USDT)   # 简单赚币里的 USDT，计入可用
```

判断三类闲置：
- USDG_funding >= 1000 → 候选 PUT-USDG（USDG 不支持简单赚币）
- (USDT_funding + USDT_savings) >= 1000 → 候选 PUT-USDT（赚币活期可即时赎回）
- BTC_funding   >= 0.0001 → 候选 CALL-BTC

**全部 < 阈值**（USDG < 100、USDT 资金账户+赚币 < 1000、BTC < 0.0001）→ 执行"停用 flag"：
```bash
rm -f "$DCD_WORK_DIR/.idle_scan_enabled"
```
然后生成简短报告（说明已停用 flag、回归每日 16:03 主 cron），结束。

### Phase 3：重新计算 v3 vol_24h

照 `dcd-daily-trade` 的 Step 1-2 重新拉数据 + 调 `calc_volatility.py`，得到本时点的 vol_24h、安全下界、min_dist。

### Phase 4：选品（仅针对闲置候选）

对每类闲置资金独立选品（按 `dcd-daily-trade` 的 Step 3 规则）：

- 行权价 < 安全下界（✅）
- APY ≥ 5%（`annualizedYield × 100`）
- 优先 1D，1D 不合规试 2D-5D
- 按 USDG 池"3–5 份轮动"规则切分投入；USDT 池 < 3000 时全仓

**合规 → 立即 `dcd_subscribe`** 部署闲置份额（USDT 投入 > 资金账户余额时，先 `earn_savings_redeem` 赎回缺口并重查余额确认到账，再下单；未到账则本轮跳过该 USDT PUT 不报错）。
**不合规** → 跳过本轮，记录扫描时点与判断理由，等下次 8h 扫描；**USDT 无合规品时把资金账户闲置 USDT（>= 100）`earn_savings_purchase` 停泊进简单赚币吃利息**（USDG 不支持，维持闲置）。

### Phase 5：记录 + flag 启停

不论是否下单，都要：

1. 在 `$DCD_WORK_DIR/交易记录.md` 顶部追加一段 "## 8h 扫描 #N" 简要记录（扫描时间、闲置金额、判断结果、是否下单）
2. 若下单，同步写 `data/ledger.json`（参考 `dcd-daily-trade` Phase 3 Step 4.5）
3. 写当日快照到 ledger（参考 Step 4.6）
4. 重新评估 flag：
   - 部署后仍有 USDG >= 1000 或 (USDT 资金账户 + 赚币) >= 1000 或 BTC >= 0.0001 → 保留 flag（USDT 停在赚币里不算已部署）
   - 已全部进 DCD 仓 → `rm -f "$DCD_WORK_DIR/.idle_scan_enabled"` 停用扫描

### Phase 6：生成报告

覆盖写入 `$DCD_WORK_DIR/last_run_report.md`（包装脚本会读后推 Telegram）：

```
# DCD 8h 扫描报告 — <YYYY-MM-DD HH:MM UTC+8>

## 📊 闲置资金
- USDG: X / USDT: X / BTC: X

## 🎯 本轮判断
- 市场状态：vol_24h = X% (vs 上次 cron: Y%) → ATR/BB 收敛 / 维持 / 恶化
- 决策：<下单 X 笔 / 跳过等下次>

## 📦 部署详情（若下单）
- PUT-USDG: 产品 / 行权价 / APY / 安全距离 / 投入
- ...

## ⏭️ Flag 状态
- <保留 / 停用>
```

---

### 关键约束

- 不修改已在仓订单
- 不做 USDG↔USDT 互换
- 不做币种切换（USDT 闲置不去买 BTC 来开 CALL）
- 所有硬性约束（安全距离、风险评级、事件日规避）同 `dcd-daily-trade`
