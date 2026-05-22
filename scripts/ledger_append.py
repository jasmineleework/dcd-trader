#!/usr/bin/env python3
"""
ledger 写入辅助工具，供日 cron 调用。

用法（JSON 通过 stdin 传入，避免 shell 转义噩梦）:

  echo '{"seq": 65, "order_id": "10200000", "side": "PUT", ...}' | \
    python3 scripts/ledger_append.py open

  echo '{"seq": 65, "settled_price": 78000, ...}' | \
    python3 scripts/ledger_append.py settle

  echo '{"ts": "2026-05-18T16:05+08:00", "usdg_funding": 0.5, ...}' | \
    python3 scripts/ledger_append.py snapshot

  echo '{"date": "2026-06-01", "ccy": "USDG", "amount": 5000, "note": "..."}' | \
    python3 scripts/ledger_append.py injection

每次写入会自动更新 meta.last_updated。
"""
import json
import os
import sys
import datetime
import pathlib

WORK_DIR = pathlib.Path(os.environ.get("DCD_WORK_DIR", pathlib.Path.home() / ".dcd-trader")).expanduser()
LEDGER = WORK_DIR / "data" / "ledger.json"
TZ_CN = datetime.timezone(datetime.timedelta(hours=8))

ORDER_FIELDS = [
    "seq", "order_id", "side", "strategy_version",
    "deposit_ccy", "deposit_amount", "deposit_usd_eq",
    "instrument_id", "strike", "btc_price_at_open",
    "safety_distance_pct", "apy_pct",
    "open_time", "settle_time", "expiry_time",
    "settled_price", "exercised",
    "return_ccy", "return_amount", "return_usd_eq",
    "pnl_usd_eq", "pnl_category",
    "state", "note",
]


def load():
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def save(ledger):
    ledger["meta"]["last_updated"] = datetime.datetime.now(TZ_CN).isoformat(
        timespec="seconds"
    )
    LEDGER.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def upsert_order(ledger, payload):
    """根据 seq 查找已存在订单：存在则覆盖填入的字段；否则追加为新订单（补全 None）"""
    seq = payload.get("seq")
    if seq is None:
        raise SystemExit("ERROR: payload 缺少 seq 字段")

    # 推断 strategy_version
    if "strategy_version" not in payload:
        payload["strategy_version"] = "v3" if seq >= 12 else "v1_v2"

    existing = next((o for o in ledger["orders"] if o["seq"] == seq), None)
    if existing:
        for k, v in payload.items():
            existing[k] = v
        return "updated", seq
    else:
        complete = {f: None for f in ORDER_FIELDS}
        complete.update(payload)
        if "state" not in payload:
            complete["state"] = "open"
        ledger["orders"].append(complete)
        ledger["orders"].sort(key=lambda x: x["seq"])
        return "created", seq


def add_snapshot(ledger, payload):
    """追加每日快照（ts 必填）"""
    if "ts" not in payload:
        payload["ts"] = datetime.datetime.now(TZ_CN).isoformat(timespec="seconds")
    ledger["snapshots"].append(payload)
    return "appended", payload["ts"]


def add_injection(ledger, payload):
    """追加注资/提取（date + ccy + amount 必填）"""
    for required in ("date", "ccy", "amount"):
        if required not in payload:
            raise SystemExit(f"ERROR: injection 缺少 {required} 字段")
    if "amount_usd_eq" not in payload:
        if payload["ccy"] in ("USDT", "USDG"):
            payload["amount_usd_eq"] = payload["amount"]
        else:
            raise SystemExit(
                f"ERROR: 非稳定币({payload['ccy']}) 必须显式指定 amount_usd_eq"
            )
    if "source" not in payload:
        payload["source"] = (
            "external_deposit" if payload["amount"] > 0 else "external_withdrawal"
        )
    ledger["injections"].append(payload)
    ledger["injections"].sort(key=lambda x: x["date"])
    return "appended", payload["date"]


HANDLERS = {
    "open": upsert_order,
    "settle": upsert_order,
    "order": upsert_order,
    "snapshot": add_snapshot,
    "injection": add_injection,
}


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in HANDLERS:
        print(f"Usage: {sys.argv[0]} {{open|settle|order|snapshot|injection}}")
        print("payload 从 stdin 读取 JSON")
        sys.exit(2)

    op = sys.argv[1]
    raw = sys.stdin.read().strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: stdin 不是合法 JSON: {e}", file=sys.stderr)
        sys.exit(3)

    ledger = load()
    result, key = HANDLERS[op](ledger, payload)
    save(ledger)
    print(f"✅ {op}: {result} (key={key})")


if __name__ == "__main__":
    main()
