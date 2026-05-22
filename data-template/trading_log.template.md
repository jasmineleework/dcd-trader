# DCD 双币赢 — 交易记录

> 本文件是 DCD trader 的人读叙事日志，机器读的结构化数据在 `data/ledger.json`。
> 两者由 `${CLAUDE_PLUGIN_ROOT}/scripts/ledger_append.py` 双写保持同步。

---

## 本金注资记录

| 日期 | 金额 USD | 币种 | 备注 |
|------|----------|------|------|
| | | | |

> 注资/提取请用 `/dcd-trader:dcd-injection <amount> <date>` 命令记录，会自动追加到本表 + 写入 ledger.json。

---

## 合并有效成本（BTC 池）

- BTC 池总持有：**0** BTC
- BTC 池总投入（USDG 等值）：**0**
- 合并有效成本（USD / BTC）：**N/A**

> CALL 行权后会卖出 BTC，BTC 池清零，合并成本失效，重新累计。
> 公式：`总投入USDG ÷ 总持有BTC`。

---

## 当前资产快照

- 资金账户：USDG 0 / USDT 0 / BTC 0
- 在仓锁定：USDG 0 / USDT 0
- 总资产（USD 等值）：0

---

## 交易记录

> 每笔订单格式：

### 第 N 笔 — <YYYY-MM-DD HH:MM UTC+8>

- **订单 ID**：xxx
- **方向**：PUT / CALL
- **产品**：BTC-USDG-260601-50000-P
- **行权价**：50,000
- **APY**：12.5%
- **安全距离**：4.2%
- **投入**：1,000 USDG
- **结算时间**：2026-06-01 16:00 UTC+8
- **结算价**：51,234
- **结果**：未行权 ✅ / 被行权 ❌
- **收益**：+3.42 USDG
- **复盘**：<2-3 句>

---

## 8h 扫描记录

### 8h 扫描 #1 — <YYYY-MM-DD HH:MM UTC+8>

- 闲置资金：USDG X / USDT X / BTC X
- 市场状态：vol_24h = X%（vs 上次 cron Y%）
- 判断：下单 / 跳过等下次
- 部署详情（若下单）：...
