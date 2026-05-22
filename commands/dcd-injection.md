---
description: 记录一笔本金注资或提取，同时写入 ledger.json 的 injections[] 和 交易记录.md 顶部的注资表
argument-hint: <amount> <date> [ccy] [note]
allowed-tools: Bash, Read, Edit
---

## /dcd-trader:dcd-injection — 记录注资/提取

### 参数

- `amount`（必需）：金额 USD 等值，**正数为注资、负数为提取**
- `date`（必需）：日期 ISO 8601（YYYY-MM-DD）
- `ccy`（可选）：币种，默认 `USDG`
- `note`（可选）：备注

### 执行步骤

1. **校验参数**：amount 是数字（含负数）、date 符合 `YYYY-MM-DD` 格式

2. **写入 ledger.json**：

```bash
WORK_DIR="${DCD_WORK_DIR:-$HOME/.dcd-trader}"

cat <<JSON | python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ledger_append.py injection
{
  "date": "$DATE",
  "ccy": "$CCY",
  "amount": $AMOUNT,
  "amount_usd_eq": $AMOUNT,
  "note": "$NOTE"
}
JSON
```

3. **同步更新 `$WORK_DIR/交易记录.md` 顶部"本金注资记录"表**：

读 `$WORK_DIR/交易记录.md`，找到 "本金注资记录" 表（| 日期 | 金额 USD | 币种 | 备注 |），在表末追加一行：

```
| YYYY-MM-DD | +/-N,NNN | USDG | <note> |
```

提取 → 金额前缀 `-`，注资 → 前缀 `+`。

4. **报告**：

```
✅ 注资已记录
- 日期: <DATE>
- 金额: <+/-AMOUNT> <CCY> USD 等值
- 备注: <NOTE>

下次周报（/dcd-trader:dcd-weekly）会自动按新注资计算 Modified Dietz 年化。
```

### 示例

- 注资：`/dcd-trader:dcd-injection 10000 2026-05-21`
- 提取：`/dcd-trader:dcd-injection -2000 2026-06-01`
- 带备注：`/dcd-trader:dcd-injection 5000 2026-07-01 USDT "工资入金"`
