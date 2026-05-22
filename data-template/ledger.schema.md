# DCD Ledger Schema v1.0

`data/ledger.json` 是 DCD 项目的**单一事实库**——所有报告（周报、月报、回测）都从这里读，不再扫描 `交易记录.md` 这种为人写的叙述文档。

## 顶层结构

```jsonc
{
  "meta": {
    "schema_version": "1.0",                    // 改 schema 时递增，breaking change 改大版本号
    "last_updated": "2026-01-01T10:00:00+08:00",// 任何写入后更新；ISO8601 + 时区
    "strategy_version": "v3",                   // 当前在跑的策略版本，年化口径起算点
    "strategy_v3_effective_date": "2026-01-01", // v3 算法生效日（用于 d_0_v3）
    "p0_v3_usd": 1000,                          // 示例值：v3 启动时账户净值（已并入策略升级前的 PnL）
    "stable_ccys": ["USDT", "USDG"],            // 稳定币列表，按 1:1 USD 折算
    "base_ccy": "USD"                           // 报告基准币种
  },
  "injections": [ ... ],                        // 外部注资/提取，按时间升序
  "orders": [ ... ],                            // 所有 DCD 订单，按 seq 升序
  "snapshots": [ ... ]                          // 每日资产快照（日 cron 写入）
}
```

## injections[]

外部注资/提取（不含账户内部 USDT↔USDG 兑换、funding↔trading 划转）。

```jsonc
{
  "date": "2026-01-01",                  // YYYY-MM-DD (UTC+8)
  "ccy": "USDT",                         // 实际入金币种
  "amount": 1000,                        // 实际入金数量（净流入，提取用负数）
  "amount_usd_eq": 1000,                 // USD 等值（稳定币 1:1，BTC 按入金当日价折）
  "source": "external_deposit",          // external_deposit | external_withdrawal
  "note": "首次外部充值"
}
```

## orders[]

每笔 DCD 订单（PUT 或 CALL）。**字段尽量完整**，缺失值用 `null`。

```jsonc
{
  "seq": 1,                              // 交易记录里"第 N 笔"的序号
  "order_id": "9482849",
  "side": "PUT",                         // PUT | CALL
  "strategy_version": "v1",              // v1 | v2 | v3（按 seq 推断：1-11=v1/v2，12+=v3）
  "deposit_ccy": "USDT",                 // 投入币种 USDT | USDG | BTC
  "deposit_amount": 1000,                // 投入数量（原币种）
  "deposit_usd_eq": 1000,                // 投入 USD 等值（BTC 按建仓当日价折）
  "instrument_id": "BTC-USDT-260319-72000-P",
  "strike": 72000,                       // 行权价（USD）
  "btc_price_at_open": 73500,            // 建仓时 BTC 价格（USD）
  "safety_distance_pct": 2.04,           // 安全距离百分比
  "apy_pct": 125.28,                     // 实际 APY（百分比，非小数）
  "open_time": "2026-03-16T08:13:00+08:00",
  "settle_time": "2026-03-19T16:00:00+08:00",
  "expiry_time": "2026-03-19T16:00:00+08:00",  // 通常 = settle_time
  "settled_price": 70100.63,             // 结算价（USD）；未结算时 null
  "exercised": true,                     // true=被行权（PUT 跌破/CALL 涨过），false=未行权，null=未结算
  "return_ccy": "BTC",                   // 结算收到币种
  "return_amount": 0.01403191,           // 结算收到数量
  "return_usd_eq": 983.44,               // 结算收到 USD 等值（按结算日 BTC 价折）
  "pnl_usd_eq": -16.56,                  // 单笔 USD 等值 PnL = return_usd_eq − deposit_usd_eq
  "pnl_category": "stable_floating_loss",// stable_profit | stable_floating_loss | btc_premium | btc_recovery
                                         //   stable_profit: PUT 未行权，得稳定币净赚
                                         //   stable_floating_loss: PUT 被行权，稳定币换 BTC 时的瞬时差额
                                         //   btc_premium: CALL 未行权，得 BTC 权利金
                                         //   btc_recovery: CALL 被行权，BTC 换回 USDG
  "state": "settled",                    // settled | open | cancelled
  "note": "BTC 跌破行权价（跨 FOMC）"
}
```

### pnl_category 详细规则

| 订单 side | exercised | pnl_category | 字段口径 |
|----------|-----------|--------------|----------|
| PUT  | false | `stable_profit`         | return - deposit 直接稳定币净赚 |
| PUT  | true  | `stable_floating_loss`  | 稳定币转 BTC 当时的 USD 损失（按结算日 BTC 价折） |
| CALL | false | `btc_premium`           | 权利金 BTC 留账，按建仓日 BTC 价折成 USD |
| CALL | true  | `btc_recovery`          | BTC 卖回稳定币的 USD 等值与投入 BTC 的 USD 等值之差 |

### 累积 PnL 拆分（report 时计算，不存）

```
PnL_strategy_stable_v3 = Σ orders[seq>=12].pnl_usd_eq where pnl_category in (stable_profit, stable_floating_loss)
PnL_btc_v3            = Σ orders[seq>=12].pnl_usd_eq where pnl_category in (btc_premium, btc_recovery)
                      + (current_btc_balance × current_btc_price − Σ historical btc cost basis)
```

## snapshots[]

每日 cron 跑完写一笔。用于历史回看。

```jsonc
{
  "ts": "2026-05-17T18:47:00+08:00",     // 写入时刻
  "usdg_funding": 0.67,
  "usdt_funding": 0.20,
  "btc_balance": 0.0000063,
  "btc_price_usd": 78135,                // 写入时刻 BTC 现价
  "open_positions_usdg": 18108,          // 在仓 USDG 锁定合计
  "open_positions_usdt": 2047,           // 在仓 USDT 锁定合计
  "merged_cost_basis_usd": 68135,        // BTC 合并有效成本（最新值），无 BTC 持仓时 null
  "total_assets_usd_eq": 20156,          // 当时总资产 USD 等值
  "note": "可选"
}
```

## 写入规则

- **日 cron**（每天 16:03 UTC+8）：
  - 每笔订单 settle/open 时 append 到 `orders[]`
  - 每次跑完 append 一笔 `snapshots[]`
  - 修改 `meta.last_updated`
- **用户手动**：仅 `injections[]` 增删（追加新行 / 用负数表示提取）；其他字段不要手改
- **格式约束**：所有金额数字用原生 JSON number（不要字符串），日期用 ISO8601 字符串
