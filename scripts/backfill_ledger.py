#!/usr/bin/env python3
"""
从 ../交易记录.md 解析全部订单+注资记录，生成 ../data/ledger.json。

用法:
  python3 scripts/backfill_ledger.py [--dry-run] [--verbose]
"""
import os
import re
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

WORK_DIR = Path(os.environ.get("DCD_WORK_DIR", Path.home() / ".dcd-trader")).expanduser()
TRADE_LOG = WORK_DIR / "交易记录.md"
LEDGER = WORK_DIR / "data" / "ledger.json"
SCHEMA_VERSION = "1.0"
TZ_CN = timezone(timedelta(hours=8))

# v3 算法生效参数 — 由用户在 ledger.json 的 meta 段（或下方 env 覆盖）指定，
# 用于把"切换 v3 之前的累计 PnL"合并到一个虚拟初始注资 P_0 中。
V3_FIRST_SEQ = int(os.environ.get("DCD_V3_FIRST_SEQ", "0"))
P0_V3_USD = float(os.environ.get("DCD_P0_V3_USD", "0"))
STRATEGY_V3_EFFECTIVE_DATE = os.environ.get("DCD_STRATEGY_V3_DATE", "")


def parse_money(s, allow_signed=True):
    """从字符串里取出第一个数字（含可选 +/-），去 $ , * 符号"""
    if s is None:
        return None
    cleaned = s.replace("$", "").replace(",", "").replace("*", "").strip()
    if allow_signed:
        m = re.search(r"[+-]?\d+(?:\.\d+)?", cleaned)
    else:
        m = re.search(r"\d+(?:\.\d+)?", cleaned)
    return float(m.group()) if m else None


def parse_field(body, label):
    """提取 markdown 表格 | label | value | 中的 value（label 周围可带 ** 加粗）"""
    pattern = re.compile(
        r"^\|\s*\*{0,2}\s*" + re.escape(label) + r"\s*\*{0,2}\s*\|\s*(.+?)\s*\|",
        re.MULTILINE,
    )
    m = pattern.search(body)
    return m.group(1).strip() if m else None


def parse_datetime(s):
    """'2026-03-16 08:13 (UTC+8)' → ISO8601"""
    if not s:
        return None
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})", s)
    if not m:
        return None
    try:
        dt = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=TZ_CN).isoformat()
    except ValueError:
        return None


def split_orders(content):
    """按所有 H2 (## ...) 段切分边界，但只输出 '## 第N笔：XXX' 这类订单段。

    交易记录里会有 '## 第N笔结算'、'## 8h 扫描 #N'、'## 当前资产快照' 等
    非订单段穿插。如果只按订单标题切，body 会跨过这些段把其它订单的字段误吸进来。
    """
    all_h2 = re.compile(r"^## .+$", re.MULTILINE)
    order_pat = re.compile(r"^## 第(\d+)笔(?:：|:)(.*)$")
    matches = list(all_h2.finditer(content))
    out = []
    for i, m in enumerate(matches):
        title_line = m.group(0).strip()
        order_m = order_pat.match(title_line)
        if not order_m:
            continue
        seq = int(order_m.group(1))
        title = order_m.group(2).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        out.append((seq, title, content[body_start:body_end]))
    return out


def parse_settlement_segments(content):
    """解析 '## 第N笔结算（...）' 段。这些是 cron 复盘段，只含结算字段。
    返回 dict[seq] -> partial_order_dict，供 merge 阶段补字段。
    """
    pat = re.compile(
        r"^## 第(\d+)笔结算（([^)）]+)）.*?$([\s\S]*?)(?=^## |\Z)",
        re.MULTILINE,
    )
    out = {}
    for m in pat.finditer(content):
        seq = int(m.group(1))
        settle_when = m.group(2)
        body = m.group(3)

        partial = {
            "seq": seq,
            "state": "settled",
            "settle_time": parse_datetime(settle_when),
        }
        # 字段同 parse_order_section 子集
        settled_raw = parse_field(body, "结算价")
        if settled_raw:
            partial["settled_price"] = parse_money(settled_raw)

        result_raw = parse_field(body, "结果") or ""
        if "未行权" in result_raw:
            partial["exercised"] = False
        elif "被行权" in result_raw:
            partial["exercised"] = True

        # 净赚 / 浮亏 / 名义损失
        prof = parse_field(body, "净赚")
        if prof:
            mm = re.search(r"([+-]?\$?[\d,]+\.?\d*)\s*(USDT|USDG)", prof)
            if mm:
                partial["pnl_usd_eq"] = parse_money(mm.group(1))
                partial["pnl_category"] = "stable_profit"
                partial["return_ccy"] = mm.group(2)

        for label in ("USDT 浮亏", "USDG 浮亏", "名义损失"):
            raw = parse_field(body, label)
            if raw:
                partial["pnl_usd_eq"] = parse_money(raw)
                partial["pnl_category"] = "stable_floating_loss"
                break

        # 结算收到 (可能在订单段没有,这里补)
        recv_raw = parse_field(body, "结算收到")
        if recv_raw:
            mm = re.search(r"([\d,]+\.?\d*)\s*(USDT|USDG|BTC)", recv_raw)
            if mm:
                partial["return_amount"] = float(mm.group(1).replace(",", ""))
                partial["return_ccy"] = mm.group(2)

        if seq in out:
            # 多次结算复盘段（不应出现，但兜底保留信息更全的）
            for k, v in partial.items():
                if out[seq].get(k) in (None, "") and v not in (None, ""):
                    out[seq][k] = v
        else:
            out[seq] = partial
    return out


def parse_injection_table(content):
    """解析顶部的「本金注资记录」表"""
    rows = []
    # 注资行：| 2026-03-14 | USDT | +6,000 | 6,000 | 备注 |  （日期/币种字段可能加粗）
    pat = re.compile(
        r"^\|\s*\*{0,2}(\d{4}-\d{2}-\d{2})(?:\s+\d{1,2}:\d{2})?\*{0,2}\s*\|\s*\*{0,2}([A-Z]{3,5})\*{0,2}\s*\|\s*\*{0,2}([+-]?[\d,]+(?:\.\d+)?)\*{0,2}\s*\|",
        re.MULTILINE,
    )
    for m in pat.finditer(content):
        date = m.group(1)
        ccy = m.group(2)
        amount = parse_money(m.group(3))
        if amount is None:
            continue
        rows.append(
            {
                "date": date,
                "ccy": ccy,
                "amount": amount,
                "amount_usd_eq": amount,  # 全是稳定币，1:1；项目暂无 BTC 注资
                "source": "external_deposit" if amount > 0 else "external_withdrawal",
                "note": "见交易记录注资表",
            }
        )
    return rows


def infer_strategy_version(seq):
    return "v3" if seq >= V3_FIRST_SEQ else "v1_v2"


SIDE_FROM_TITLE = {"P": "PUT", "C": "CALL"}


def parse_instrument(s):
    """BTC-USDT-260319-72000-P → (USDT, 72000, PUT, instrument_str)"""
    if not s:
        return None, None, None, None
    m = re.search(r"BTC-(USDT|USDG)-(\d{6})-(\d+)-([PC])", s)
    if not m:
        return None, None, None, None
    quote_ccy = m.group(1)
    strike = int(m.group(3))
    side = SIDE_FROM_TITLE.get(m.group(4))
    return quote_ccy, strike, side, m.group(0)


def parse_order_section(seq, title, body):
    """把一笔订单段解析成 schema 字段"""
    order = {
        "seq": seq,
        "order_id": None,
        "side": None,
        "strategy_version": infer_strategy_version(seq),
        "deposit_ccy": None,
        "deposit_amount": None,
        "deposit_usd_eq": None,
        "instrument_id": None,
        "strike": None,
        "btc_price_at_open": None,
        "safety_distance_pct": None,
        "apy_pct": None,
        "open_time": None,
        "settle_time": None,
        "expiry_time": None,
        "settled_price": None,
        "exercised": None,
        "return_ccy": None,
        "return_amount": None,
        "return_usd_eq": None,
        "pnl_usd_eq": None,
        "pnl_category": None,
        "state": "open",
        "note": title,
    }

    # instrument 从标题里抠（也 fallback 用 body）
    quote_ccy, strike, side, instr = parse_instrument(title) if title else (None,) * 4
    if instr is None:
        # body 里搜
        quote_ccy, strike, side, instr = parse_instrument(body)
    order["instrument_id"] = instr
    order["strike"] = strike
    order["side"] = side

    # order_id
    raw = parse_field(body, "订单 ID") or parse_field(body, "订单ID")
    if raw:
        m = re.search(r"\d+", raw)
        if m:
            order["order_id"] = m.group()

    # 建仓时间 / 到期时间
    order["open_time"] = parse_datetime(parse_field(body, "建仓时间"))
    order["expiry_time"] = parse_datetime(parse_field(body, "到期时间"))

    # 行权价 (备用，从 strike 抠不到时)
    if order["strike"] is None:
        order["strike"] = parse_money(parse_field(body, "行权价"))

    # 建仓时 BTC 价格
    order["btc_price_at_open"] = parse_money(parse_field(body, "建仓时 BTC 价格"))

    # 距现价（安全距离）
    raw = parse_field(body, "距现价")
    if raw:
        m = re.search(r"([+-]?\d+\.?\d*)\s*%", raw)
        if m:
            order["safety_distance_pct"] = float(m.group(1))

    # APY
    raw = parse_field(body, "APY") or parse_field(body, "实际 APY")
    if raw:
        m = re.search(r"(\d+\.?\d*)\s*%", raw)
        if m:
            order["apy_pct"] = float(m.group(1))

    # 投入：'1,000 USDT' 或 '0.0140 BTC'
    raw = parse_field(body, "投入")
    if raw:
        m = re.search(r"([\d,]+\.?\d*)\s*(USDT|USDG|BTC)", raw)
        if m:
            order["deposit_amount"] = float(m.group(1).replace(",", ""))
            order["deposit_ccy"] = m.group(2)
            if m.group(2) in ("USDT", "USDG"):
                order["deposit_usd_eq"] = order["deposit_amount"]
            elif m.group(2) == "BTC" and order["btc_price_at_open"]:
                order["deposit_usd_eq"] = round(
                    order["deposit_amount"] * order["btc_price_at_open"], 2
                )

    # 结算价 → settle_time（用 expiry_time 近似），exercised
    settled_raw = parse_field(body, "结算价")
    if settled_raw and "$" in settled_raw:
        order["settled_price"] = parse_money(settled_raw)
        order["state"] = "settled"
        order["settle_time"] = order["expiry_time"]

        # 结果字段 — "被行权/未行权" 比 ✅/❌ 优先（CALL 行权成功时是 "✅ 被行权"）
        result_raw = parse_field(body, "结果") or ""
        text = result_raw + " " + title
        if "被行权" in text and "未行权" not in text.split("被行权")[0]:
            order["exercised"] = True
        elif "未行权" in text:
            order["exercised"] = False
        else:
            order["exercised"] = None

    # 结算收到 / 净赚 / 浮亏 / 权利金 → pnl
    settled_recv_raw = parse_field(body, "结算收到")
    if settled_recv_raw:
        m = re.search(r"([\d,]+\.?\d*)\s*(USDT|USDG|BTC)", settled_recv_raw)
        if m:
            order["return_amount"] = float(m.group(1).replace(",", ""))
            order["return_ccy"] = m.group(2)

    # 净赚 (PUT 未行权 / 部分 CALL 合并表述)
    profit_raw = parse_field(body, "净赚")
    if profit_raw:
        m = re.search(r"([+-]?\$?[\d,]+\.?\d*)\s*(USDT|USDG)", profit_raw)
        if m:
            order["pnl_usd_eq"] = parse_money(m.group(1))
            order["pnl_category"] = "stable_profit"
            if order["return_amount"] is None:
                order["return_amount"] = (order["deposit_amount"] or 0) + (
                    order["pnl_usd_eq"] or 0
                )
                order["return_ccy"] = m.group(2)
            if order["return_usd_eq"] is None and order["return_amount"] is not None:
                order["return_usd_eq"] = order["return_amount"]

    # USDT/USDG 浮亏 / 名义损失 (PUT 被行权)
    for label in ("USDT 浮亏", "USDG 浮亏", "名义损失"):
        raw = parse_field(body, label)
        if raw:
            order["pnl_usd_eq"] = parse_money(raw)
            order["pnl_category"] = "stable_floating_loss"
            mv = parse_field(body, "结算 BTC 市值")
            if mv:
                order["return_usd_eq"] = parse_money(mv)
            break

    # 权利金 (CALL 未行权)
    if order["pnl_category"] is None and order["side"] == "CALL":
        prem_raw = parse_field(body, "权利金")
        if prem_raw:
            # 例:  +0.00005904 BTC（≈ $4.07）
            usd_m = re.search(r"\$\s*([\d,]+\.?\d*)", prem_raw)
            if usd_m:
                order["pnl_usd_eq"] = float(usd_m.group(1).replace(",", ""))
                order["pnl_category"] = "btc_premium"
            elif order["btc_price_at_open"]:
                btc_m = re.search(r"([+-]?\d+\.?\d*)\s*BTC", prem_raw)
                if btc_m:
                    order["pnl_usd_eq"] = round(
                        float(btc_m.group(1)) * order["btc_price_at_open"], 2
                    )
                    order["pnl_category"] = "btc_premium"

    # CALL 被行权(BTC 卖回稳定币) - 罕见
    if (
        order["pnl_category"] is None
        and order["side"] == "CALL"
        and order["exercised"] is True
    ):
        if (
            order["return_usd_eq"] is not None
            and order["deposit_usd_eq"] is not None
        ):
            order["pnl_usd_eq"] = round(
                order["return_usd_eq"] - order["deposit_usd_eq"], 2
            )
        order["pnl_category"] = "btc_recovery"

    # Fallback: 已结算 + 稳定币 deposit/return 但无显式 pnl 字段
    # 适用于 "结算收到 X.XX USDG, 投入 Y.YY USDG" 这种没有"净赚"行的格式
    if (
        order["state"] == "settled"
        and order["pnl_category"] is None
        and order["deposit_amount"] is not None
        and order["return_amount"] is not None
        and order["deposit_ccy"] in ("USDT", "USDG")
        and order["return_ccy"] in ("USDT", "USDG")
    ):
        order["pnl_usd_eq"] = round(
            order["return_amount"] - order["deposit_amount"], 4
        )
        order["pnl_category"] = (
            "stable_profit" if order["pnl_usd_eq"] >= 0 else "stable_floating_loss"
        )
        if order["return_usd_eq"] is None:
            order["return_usd_eq"] = order["return_amount"]

    # CALL 被行权 (BTC 卖回稳定币) 的 pnl 补算
    if (
        order["state"] == "settled"
        and order["side"] == "CALL"
        and order["exercised"] is True
        and order["pnl_usd_eq"] is None
        and order["return_amount"] is not None
        and order["return_ccy"] in ("USDT", "USDG")
        and order["deposit_usd_eq"] is not None
    ):
        order["pnl_usd_eq"] = round(
            order["return_amount"] - order["deposit_usd_eq"], 4
        )
        order["pnl_category"] = "btc_recovery"
        if order["return_usd_eq"] is None:
            order["return_usd_eq"] = order["return_amount"]

    # 持仓中（无结算价）→ state=open
    if order["state"] != "settled":
        order["state"] = "open"

    return order


def merge_duplicate_seqs(orders):
    """同一 seq 在 markdown 里通常出现两次（开仓 + 结算）。保留信息更全的版本。
    取最大 score：settled > open；含 pnl > 无 pnl"""

    def score(o):
        s = 0
        if o["state"] == "settled":
            s += 100
        if o["pnl_category"] not in (None, "unknown"):
            s += 10
        if o["settled_price"] is not None:
            s += 5
        if o["order_id"]:
            s += 1
        return s

    bucket = {}
    for o in orders:
        cur = bucket.get(o["seq"])
        if cur is None or score(o) > score(cur):
            bucket[o["seq"]] = o
        else:
            # 合并：把缺失字段从新副本补到旧副本（不覆盖已填值）
            for k, v in o.items():
                if cur.get(k) in (None, "") and v not in (None, ""):
                    cur[k] = v
    return sorted(bucket.values(), key=lambda x: x["seq"])


def main(args):
    verbose = "--verbose" in args
    dry_run = "--dry-run" in args

    content = TRADE_LOG.read_text(encoding="utf-8")

    injections = parse_injection_table(content[:1500])  # 注资表在文件最前部
    orders_raw = split_orders(content)
    orders = []
    parse_errors = []
    skipped_segments = 0
    for seq, title, body in orders_raw:
        # 跳过 "⏭️ 跳过" 段（非真实订单）
        if "⏭️" in title or "跳过" in title:
            skipped_segments += 1
            continue
        try:
            o = parse_order_section(seq, title, body)
            orders.append(o)
        except Exception as e:
            parse_errors.append((seq, str(e)))

    # 合并同 seq 多副本（开仓 🔵 + 结算 ✅/❌）
    orders = merge_duplicate_seqs(orders)

    # 合并独立的 "## 第N笔结算" 复盘段（cron 写入的，与订单段并存）
    settlement_partials = parse_settlement_segments(content)
    for o in orders:
        partial = settlement_partials.get(o["seq"])
        if not partial:
            continue
        for k, v in partial.items():
            if k == "seq":
                continue
            # 结算段的字段优先级高于"开仓"段的空值
            if o.get(k) in (None, "", "open") and v not in (None, ""):
                o[k] = v
        # 触发 pnl fallback 重算
        if (
            o["state"] == "settled"
            and o["pnl_category"] is None
            and o["deposit_amount"] is not None
            and o["return_amount"] is not None
            and o["deposit_ccy"] in ("USDT", "USDG")
            and o["return_ccy"] in ("USDT", "USDG")
        ):
            o["pnl_usd_eq"] = round(
                o["return_amount"] - o["deposit_amount"], 4
            )
            o["pnl_category"] = (
                "stable_profit"
                if o["pnl_usd_eq"] >= 0
                else "stable_floating_loss"
            )
            if o["return_usd_eq"] is None:
                o["return_usd_eq"] = o["return_amount"]

    # 统计
    settled = [o for o in orders if o["state"] == "settled"]
    open_orders = [o for o in orders if o["state"] == "open"]
    by_cat = {}
    for o in settled:
        c = o["pnl_category"] or "unknown"
        by_cat[c] = by_cat.get(c, 0) + 1

    print(f"📋 解析结果")
    print(f"  注资记录: {len(injections)} 条")
    print(f"  跳过段（非订单）: {skipped_segments} 个")
    print(f"  订单总数: {len(orders)} 笔（已合并开仓+结算副本）")
    print(f"    已结算: {len(settled)} 笔")
    print(f"    持仓中: {len(open_orders)} 笔")
    print(f"    PnL 分类: {by_cat}")
    if parse_errors:
        print(f"  ⚠️  解析失败: {len(parse_errors)} 条")
        for seq, e in parse_errors:
            print(f"      第{seq}笔: {e}")

    # 对账：v3 后稳定币累积 PnL
    v3_stable = sum(
        o["pnl_usd_eq"] or 0
        for o in settled
        if o["seq"] >= V3_FIRST_SEQ
        and o["pnl_category"] in ("stable_profit", "stable_floating_loss")
    )
    v3_btc = sum(
        o["pnl_usd_eq"] or 0
        for o in settled
        if o["seq"] >= V3_FIRST_SEQ
        and o["pnl_category"] in ("btc_premium", "btc_recovery")
    )
    print(f"\n💰 v3 后(seq>=12) 累积 PnL（USD 等值，来自 ledger）")
    print(f"  策略已实现（稳定币）: {v3_stable:+.2f}")
    print(f"  BTC 权利金/回收     : {v3_btc:+.2f}")

    if verbose:
        print("\n=== 前 3 笔订单详情 ===")
        for o in orders[:3]:
            print(json.dumps(o, ensure_ascii=False, indent=2))
        print("\n=== 注资 ===")
        for inj in injections:
            print(json.dumps(inj, ensure_ascii=False))

    ledger = {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "last_updated": datetime.now(TZ_CN).isoformat(timespec="seconds"),
            "strategy_version": "v3",
            "strategy_v3_effective_date": STRATEGY_V3_EFFECTIVE_DATE,
            "p0_v3_usd": P0_V3_USD,
            "stable_ccys": ["USDT", "USDG"],
            "base_ccy": "USD",
        },
        "injections": injections,
        "orders": orders,
        "snapshots": [],
    }

    if dry_run:
        print(f"\n--dry-run: 不写入 {LEDGER}")
        return

    LEDGER.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    size_kb = LEDGER.stat().st_size / 1024
    print(f"\n✅ 写入 {LEDGER}（{size_kb:.1f} KB）")


if __name__ == "__main__":
    main(sys.argv[1:])
