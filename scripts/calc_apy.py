#!/usr/bin/env python3
"""
DCD 累积年化计算工具（Modified Dietz）

公式：
    T_days_v3 = (今天 − d_0_v3).days

    虚拟注资集 = [(d_0_v3, P_0_v3),
                *[(inj.date, inj.amount_usd_eq) for inj in injections if inj.date >= d_0_v3]]

    P_v3 = Σ amount_i

    对每笔注资 i：
        days_in_pool_i = (今天 − date_i).days
        weight_i       = days_in_pool_i / T_days_v3

    分母         = Σ amount_i × weight_i
    PnL_v3_total = V_now − P_v3
    ROR_v3       = PnL_v3_total / 分母
    v3 累积年化  = ROR_v3 × 365 / T_days_v3

口径要点：
    - 全部按 USDG 等值（稳定币 1:1，BTC 按实时价折算）
    - 在仓订单按"投入金额"入账
    - v3 切换前订单的 PnL 已并入 P_0_v3，不重复计算

用法：
    python3 calc_apy.py --ledger data/ledger.json --v-now <V_now> [--btc-price <现价>] [--date <YYYY-MM-DD>]

输出：JSON 到 stdout，含所有中间量和最终年化率。
"""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path


def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def main() -> int:
    ap = argparse.ArgumentParser(description="DCD Modified Dietz 累积年化计算")
    ap.add_argument("--ledger", required=True, help="ledger.json 路径")
    ap.add_argument("--v-now", type=float, required=True, help="当前总资产 USD 等值")
    ap.add_argument("--btc-price", type=float, default=None, help="BTC 现价（仅参考，不参与计算）")
    ap.add_argument("--date", default=None, help="计算基准日期 YYYY-MM-DD（默认今天）")
    args = ap.parse_args()

    ledger_path = Path(args.ledger).expanduser()
    if not ledger_path.exists():
        print(f"ERROR: ledger not found: {ledger_path}", file=sys.stderr)
        return 1

    with ledger_path.open() as f:
        ledger = json.load(f)

    meta = ledger.get("meta", {})
    d_0_str = meta.get("strategy_v3_effective_date", "")
    p_0 = float(meta.get("p0_v3_usd", 0))
    v3_first_seq = int(meta.get("v3_first_seq", 0))

    if not d_0_str:
        print("ERROR: ledger.meta.strategy_v3_effective_date is empty. Run /dcd-trader:dcd-setup first.", file=sys.stderr)
        return 2

    d_0 = parse_date(d_0_str)
    today = parse_date(args.date) if args.date else date.today()
    t_days = (today - d_0).days
    if t_days <= 0:
        print(f"ERROR: today ({today}) is on or before d_0_v3 ({d_0}). No cumulative yield to compute yet.", file=sys.stderr)
        return 3

    # Virtual injections: P_0 at d_0, plus each external injection on/after d_0.
    virtual_injections = [(d_0, p_0)]
    for inj in ledger.get("injections", []):
        inj_date = parse_date(inj["date"])
        if inj_date >= d_0:
            virtual_injections.append((inj_date, float(inj.get("amount_usd_eq", inj.get("amount", 0)))))

    p_v3 = sum(amt for _, amt in virtual_injections)

    denominator = 0.0
    avg_principal = 0.0
    details = []
    for d, amt in virtual_injections:
        days_in_pool = (today - d).days
        weight = days_in_pool / t_days if t_days else 0
        denom_contrib = amt * weight
        avg_principal_contrib = amt * days_in_pool / t_days if t_days else 0
        denominator += denom_contrib
        avg_principal += avg_principal_contrib
        details.append({
            "date": d.isoformat(),
            "amount_usd": amt,
            "days_in_pool": days_in_pool,
            "weight": weight,
            "denominator_contribution": denom_contrib,
        })

    pnl_v3_total = args.v_now - p_v3

    # Categorical breakdown (settled v3 orders only)
    pnl_stable = 0.0
    pnl_btc_flow = 0.0
    for o in ledger.get("orders", []):
        if o.get("state") != "settled":
            continue
        if int(o.get("seq", 0)) < v3_first_seq:
            continue
        pnl = float(o.get("pnl_usd_eq", 0))
        cat = o.get("pnl_category", "")
        if cat in ("stable_profit", "stable_floating_loss"):
            pnl_stable += pnl
        elif cat in ("btc_premium", "btc_recovery"):
            pnl_btc_flow += pnl

    pnl_btc_floating = pnl_v3_total - pnl_stable - pnl_btc_flow

    ror_v3 = pnl_v3_total / denominator if denominator else 0
    annualized_v3 = ror_v3 * 365 / t_days if t_days else 0
    cross_check_annualized = (pnl_v3_total / avg_principal * 365 / t_days) if avg_principal else 0

    out = {
        "d_0_v3": d_0.isoformat(),
        "today": today.isoformat(),
        "T_days_v3": t_days,
        "P_0_v3_usd": p_0,
        "P_v3_total_usd": p_v3,
        "V_now_usd": args.v_now,
        "btc_price_usd": args.btc_price,
        "PnL_v3_total_usd": round(pnl_v3_total, 4),
        "PnL_stable_usd": round(pnl_stable, 4),
        "PnL_btc_flow_usd": round(pnl_btc_flow, 4),
        "PnL_btc_floating_usd": round(pnl_btc_floating, 4),
        "denominator_usd": round(denominator, 4),
        "avg_principal_usd": round(avg_principal, 4),
        "ROR_v3": round(ror_v3, 6),
        "annualized_v3_pct": round(annualized_v3 * 100, 4),
        "cross_check_annualized_pct": round(cross_check_annualized * 100, 4),
        "virtual_injections_count": len(virtual_injections),
        "details": details,
    }
    json.dump(out, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
