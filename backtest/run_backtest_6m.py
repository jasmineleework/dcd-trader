#!/usr/bin/env python3
"""
DCD PUT 策略回测 — v2 vs v3 算法 6个月版
2025-10-01 ~ 2026-03-31，每天模拟 16:00 UTC+8 下单1天产品
自动从 OKX 公开 API 拉取 BTC-USDT 4H/1D K线数据

v2: 双速 ATR（4H period 7/14）、置信缓冲、自适应事件乘数、IV 用 30天 HV 代理（权重 0.45）
v3: v2 + 回撤安全垫 + 暴跌动量 + 动态ATR权重
"""

import math
import time
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

UTC8 = timezone(timedelta(hours=8))

# ====== 事件日历（2025-10 ~ 2026-03）======
EVENTS = {
    "2025-10-10": ("CPI", "CPI"),
    "2025-10-29": ("FOMC", "FOMC"),
    "2025-10-31": ("CME交割", "CME"),
    "2025-11-07": ("NFP", "NFP"),
    "2025-11-12": ("CPI", "CPI"),
    "2025-11-28": ("CME交割", "CME"),
    "2025-12-10": ("CPI", "CPI"),
    "2025-12-17": ("FOMC", "FOMC"),
    "2025-12-19": ("多重到期", "MULTI"),
    "2025-12-26": ("CME交割", "CME"),
    "2026-01-01": ("元旦", "CME"),
    "2026-01-10": ("NFP+CPI", "MULTI"),
    "2026-01-13": ("PPI", "CPI"),
    "2026-01-14": ("CPI", "CPI"),
    "2026-01-27": ("FOMC", "FOMC"),
    "2026-01-28": ("FOMC", "FOMC"),
    "2026-01-29": ("CME交割", "CME"),
    "2026-01-30": ("CME+Deribit", "MULTI"),
    "2026-01-31": ("Deribit到期", "CME"),
    "2026-02-06": ("NFP", "NFP"),
    "2026-02-07": ("NFP+1", "NFP"),
    "2026-02-10": ("PPI", "CPI"),
    "2026-02-11": ("CPI", "CPI"),
    "2026-02-24": ("CME交割前", "CME"),
    "2026-02-25": ("CME交割", "CME"),
    "2026-02-26": ("FOMC纪要", "FOMC"),
    "2026-02-27": ("Deribit到期", "CME"),
    "2026-03-03": ("ISM制造业", "CPI"),
    "2026-03-04": ("CME交割", "CME"),
    "2026-03-06": ("NFP前", "NFP"),
    "2026-03-07": ("NFP", "NFP"),
    "2026-03-11": ("PPI", "CPI"),
    "2026-03-12": ("CPI", "CPI"),
    "2026-03-16": ("零售销售", "CPI"),
    "2026-03-17": ("FOMC前", "FOMC"),
    "2026-03-18": ("FOMC", "FOMC"),
    "2026-03-26": ("PCE前", "CPI"),
    "2026-03-27": ("CME交割", "CME"),
    "2026-03-28": ("PCE", "CME"),
}

# v2 自适应事件乘数表
EVENT_MULTIPLIERS = {
    "FOMC": {"day": None, "pre": 1.8, "post": 1.3},
    "CPI":  {"day": None, "pre": 1.5, "post": 1.2},
    "CME":  {"day": None, "pre": 1.5, "post": 1.3},
    "NFP":  {"day": None, "pre": 1.4, "post": 1.2},
    "MULTI": {"day": None, "pre": 1.8, "post": 1.5},
}

# 事件邻近日集合
EVENT_ADJACENT = set()
for ds in EVENTS:
    d = datetime.strptime(ds, "%Y-%m-%d")
    for delta in [-1, 0, 1]:
        EVENT_ADJACENT.add((d + timedelta(days=delta)).strftime("%Y-%m-%d"))


# ══════════════════════════════════════════
#  数据获取
# ══════════════════════════════════════════

def fetch_candles_page(inst_id, bar, before_ts=None, after_ts=None, limit=100, retries=3):
    """从 OKX API 获取一页K线数据，带重试"""
    url = f"https://www.okx.com/api/v5/market/history-candles?instId={inst_id}&bar={bar}&limit={limit}"
    if before_ts is not None:
        url += f"&before={before_ts}"
    if after_ts is not None:
        url += f"&after={after_ts}"

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
                if data.get("code") == "0" and data.get("data"):
                    # 返回格式: [[ts, o, h, l, c, vol, ...], ...]
                    return data["data"]
                else:
                    print(f"  API返回异常: code={data.get('code')}, msg={data.get('msg')}")
                    return []
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            print(f"  请求失败 (尝试 {attempt+1}/{retries}): {e}")
            if attempt < retries - 1:
                time.sleep(2)
    return []


def fetch_all_candles(inst_id, bar, start_ts_ms, end_ts_ms):
    """
    分页获取所有K线数据。
    OKX API: before = 返回时间戳 > before 的数据, after = 返回时间戳 < after 的数据
    数据按时间倒序返回，我们从 end 向 start 回溯。
    """
    all_candles = []
    current_after = end_ts_ms
    page = 0

    while True:
        page += 1
        data = fetch_candles_page(inst_id, bar, before_ts=None, after_ts=current_after, limit=100)
        if not data:
            break

        # 解析并筛选
        added = 0
        for row in data:
            ts = int(row[0])
            if ts < start_ts_ms:
                continue
            all_candles.append([ts, float(row[1]), float(row[2]), float(row[3]), float(row[4])])
            added += 1

        # data 按时间倒序，最后一条是最老的
        oldest_ts = int(data[-1][0])
        if oldest_ts <= start_ts_ms:
            break
        current_after = oldest_ts  # 下一页获取比这更老的

        # 防止无限循环
        if page > 50:
            print(f"  警告: 分页超过50页，停止获取")
            break

        time.sleep(0.2)  # 控制频率

    # 去重 + 按时间排序
    seen = set()
    unique = []
    for c in all_candles:
        if c[0] not in seen:
            seen.add(c[0])
            unique.append(c)
    unique.sort(key=lambda x: x[0])
    return unique


def load_data():
    """从 OKX API 获取 4H 和 1D K线数据"""
    # 时间范围
    # 4H: 2025-09-01 ~ 2026-03-31 (需要提前1个月用于预热ATR等指标)
    ts_4h_start = 1756598400000   # 2025-09-01 00:00 UTC
    ts_4h_end   = 1775001600000   # 2026-04-01 00:00 UTC

    # 1D: 2025-08-01 ~ 2026-03-31 (需要提前2个月用于30天HV等)
    ts_1d_start = 1753920000000   # 2025-08-01 00:00 UTC
    ts_1d_end   = 1775001600000   # 2026-04-01 00:00 UTC

    print("━" * 80)
    print("  正在从 OKX API 获取数据...")
    print("━" * 80)

    print(f"\n  获取 4H K线 (2025-09-01 ~ 2026-04-01)...")
    candles_4h = fetch_all_candles("BTC-USDT", "4H", ts_4h_start, ts_4h_end)
    print(f"  ✓ 获取 {len(candles_4h)} 根 4H K线")

    if candles_4h:
        first_dt = datetime.fromtimestamp(candles_4h[0][0] / 1000, tz=UTC8)
        last_dt = datetime.fromtimestamp(candles_4h[-1][0] / 1000, tz=UTC8)
        print(f"    范围: {first_dt.strftime('%Y-%m-%d %H:%M')} ~ {last_dt.strftime('%Y-%m-%d %H:%M')}")

    print(f"\n  获取 1D K线 (2025-08-01 ~ 2026-04-01)...")
    candles_1d = fetch_all_candles("BTC-USDT", "1Dutc", ts_1d_start, ts_1d_end)
    # 如果 1Dutc 不行就用 1D
    if len(candles_1d) < 10:
        print(f"  1Dutc 数据不足({len(candles_1d)}根), 尝试 1D...")
        candles_1d = fetch_all_candles("BTC-USDT", "1D", ts_1d_start, ts_1d_end)
    print(f"  ✓ 获取 {len(candles_1d)} 根 1D K线")

    if candles_1d:
        first_dt = datetime.fromtimestamp(candles_1d[0][0] / 1000, tz=UTC8)
        last_dt = datetime.fromtimestamp(candles_1d[-1][0] / 1000, tz=UTC8)
        print(f"    范围: {first_dt.strftime('%Y-%m-%d %H:%M')} ~ {last_dt.strftime('%Y-%m-%d %H:%M')}")

    print()
    return candles_4h, candles_1d


# ══════════════════════════════════════════
#  工具函数（同 run_backtest_3m.py）
# ══════════════════════════════════════════

def get_v2_event_mult(ds):
    """v2: 获取自适应事件乘数。事件当天返回 None（不交易）"""
    d = datetime.strptime(ds, "%Y-%m-%d")
    if ds in EVENTS:
        return None  # 不交易
    # 事件后1天？
    prev_ds = (d - timedelta(days=1)).strftime("%Y-%m-%d")
    if prev_ds in EVENTS:
        _, etype = EVENTS[prev_ds]
        m = EVENT_MULTIPLIERS.get(etype, {"post": 1.3})
        return m["post"]
    # 事件前1天？（到期日是事件日 → 也不交易）
    next_ds = (d + timedelta(days=1)).strftime("%Y-%m-%d")
    if next_ds in EVENTS:
        return None  # 到期跨事件 = 不交易
    return 1.0


def ts_to_utc8(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000, tz=UTC8)


def find_4h_idx(candles, target_ts):
    best_i, best_d = 0, float("inf")
    for i, c in enumerate(candles):
        d = abs(c[0] - target_ts)
        if d < best_d:
            best_d = d
            best_i = i
    return best_i


def get_close_at(candles, target_ts):
    i = find_4h_idx(candles, target_ts)
    return candles[i][4]


def calc_atr(candles, end_idx, period):
    """计算 ATR（可变周期）"""
    start = max(1, end_idx - period + 1)
    trs = []
    for i in range(start, end_idx + 1):
        h, l, pc = candles[i][2], candles[i][3], candles[i-1][4]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else 0


def calc_bb_std20(candles, end_idx):
    start = max(0, end_idx - 19)
    closes = [candles[i][4] for i in range(start, end_idx + 1)]
    if len(closes) < 5:
        return 0
    m = sum(closes) / len(closes)
    return math.sqrt(sum((c - m)**2 for c in closes) / len(closes))


def get_period_low(candles, start_ts, end_ts):
    """获取 start_ts ~ end_ts 之间的最低价"""
    low = float("inf")
    for c in candles:
        if start_ts <= c[0] <= end_ts:
            if c[3] < low:
                low = c[3]
    return low if low < float("inf") else None


def round_strike(price, step=500):
    return int(price // step) * step


def calc_vol_v2(candles, idx, price, iv_daily, ds):
    """v2 波动率算法（原版，保留对比用）"""
    atr_fast_raw = calc_atr(candles, idx, 7)
    atr_slow_raw = calc_atr(candles, idx, 14)
    atr_fast = atr_fast_raw * math.sqrt(6) / price   # 4H ATR 日化: sqrt(6)
    atr_slow = atr_slow_raw * math.sqrt(6) / price
    atr_component = max(atr_fast, atr_slow * 0.9)
    bb_std = calc_bb_std20(candles, idx)
    bb_1h_approx = bb_std / 2
    bb_comp = bb_1h_approx * math.sqrt(24) / price

    comp_iv = 0.45 * iv_daily
    comp_atr = 0.30 * atr_component
    comp_bb = 0.15 * bb_comp
    comp_funding = 0.0
    vol_base = comp_iv + comp_atr + comp_bb + comp_funding

    vol_accel = abs(atr_fast - atr_slow) / atr_slow if atr_slow > 0 else 0
    is_near_event = ds in EVENT_ADJACENT
    if vol_accel > 0.3 or is_near_event:
        conf_buffer = 0.01
    elif vol_accel > 0.1:
        conf_buffer = 0.005
    else:
        conf_buffer = 0.003

    vol = vol_base + conf_buffer
    return vol, comp_iv, comp_atr, comp_bb, comp_funding, conf_buffer, vol_accel


def calc_vol_v3(candles, idx, price, iv_daily, ds, daily, daily_idx):
    """v3 波动率算法：v2 + 回撤安全垫 + 暴跌动量 + 动态ATR权重"""

    # ── A. 双速 ATR ──
    atr_fast_raw = calc_atr(candles, idx, 7)
    atr_slow_raw = calc_atr(candles, idx, 14)
    atr_fast = atr_fast_raw * math.sqrt(6) / price   # 4H 一天有 6 根
    atr_slow = atr_slow_raw * math.sqrt(6) / price
    atr_component = max(atr_fast, atr_slow * 0.9)

    # ── B. BB ──
    bb_std = calc_bb_std20(candles, idx)
    bb_1h_approx = bb_std / 2
    bb_comp = bb_1h_approx * math.sqrt(24) / price

    # ── C. 动态 ATR 权重 ──
    if iv_daily > 0 and atr_component > 2 * iv_daily:
        w_iv, w_atr = 0.20, 0.55  # ATR 主导
    else:
        w_iv, w_atr = 0.45, 0.30  # 标准权重

    comp_iv = w_iv * iv_daily
    comp_atr = w_atr * atr_component
    comp_bb = 0.15 * bb_comp
    comp_funding = 0.0
    vol_base = comp_iv + comp_atr + comp_bb + comp_funding

    # ── D. 置信缓冲（同 v2）──
    vol_accel = abs(atr_fast - atr_slow) / atr_slow if atr_slow > 0 else 0
    is_near_event = ds in EVENT_ADJACENT
    if vol_accel > 0.3 or is_near_event:
        conf_buffer = 0.01
    elif vol_accel > 0.1:
        conf_buffer = 0.005
    else:
        conf_buffer = 0.003

    # ── E. 回撤安全垫 ──
    start_30d = max(0, daily_idx - 30)
    high_30d = max(daily[i][2] for i in range(start_30d, daily_idx + 1))
    drawdown_30d = (price - high_30d) / high_30d  # 负值
    abs_dd = abs(drawdown_30d)
    if abs_dd > 0.30:
        drawdown_adj = 0.040
    elif abs_dd > 0.20:
        drawdown_adj = 0.025
    elif abs_dd > 0.10:
        drawdown_adj = 0.015
    else:
        drawdown_adj = 0.0

    # ── F. 暴跌动量 ──
    if daily_idx >= 7:
        drop_7d = (daily[daily_idx][4] - daily[daily_idx - 7][4]) / daily[daily_idx - 7][4]
    else:
        drop_7d = 0
    if drop_7d < -0.10:
        momentum_adj = 0.030
    elif drop_7d < -0.05:
        momentum_adj = 0.015
    else:
        momentum_adj = 0.0

    vol = vol_base + conf_buffer + drawdown_adj + momentum_adj

    extras = {
        "w_iv": w_iv, "w_atr": w_atr,
        "drawdown_30d": drawdown_30d, "drawdown_adj": drawdown_adj,
        "drop_7d": drop_7d, "momentum_adj": momentum_adj,
        "atr_fast": atr_fast, "atr_slow": atr_slow,
        "vol_base": vol_base, "conf_buffer": conf_buffer,
    }
    return vol, comp_iv, comp_atr, comp_bb, comp_funding, conf_buffer, vol_accel, extras


def calc_rolling_iv(daily, end_idx):
    """计算滚动30天 HV 作为 IV 代理"""
    start = max(1, end_idx - 29)
    rets = []
    for i in range(start, end_idx + 1):
        if i > 0:
            rets.append((daily[i][4] - daily[i-1][4]) / daily[i-1][4])
    if not rets:
        return 0.03 / math.sqrt(365)  # fallback
    rv_daily = math.sqrt(sum(r**2 for r in rets) / len(rets))
    return rv_daily


# ══════════════════════════════════════════
#  主回测
# ══════════════════════════════════════════

def run():
    candles, daily = load_data()

    if len(candles) < 50:
        print(f"  错误: 4H K线数据不足 ({len(candles)} 根)，无法回测")
        return
    if len(daily) < 30:
        print(f"  错误: 1D K线数据不足 ({len(daily)} 根)，无法回测")
        return

    start_date = datetime(2025, 10, 1, tzinfo=UTC8)
    end_date = datetime(2026, 3, 31, tzinfo=UTC8)

    # 全局 IV
    all_rets = []
    for i in range(1, len(daily)):
        all_rets.append((daily[i][4] - daily[i-1][4]) / daily[i-1][4])
    global_rv = math.sqrt(sum(r**2 for r in all_rets) / len(all_rets)) if all_rets else 0.03
    global_iv_annual = global_rv * math.sqrt(365)

    total_days = 0
    v2_traded, v2_exercised = 0, 0
    v3_traded, v3_exercised = 0, 0
    v3_skipped = 0
    v3_skip_reasons = {}
    results = []
    v2_ex_details, v3_ex_details = [], []

    current = start_date
    while current <= end_date:
        ds = current.strftime("%Y-%m-%d")
        total_days += 1

        # 16:00 UTC+8 = 08:00 UTC
        entry_ts = int(datetime(current.year, current.month, current.day,
                                8, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
        expiry_ts = entry_ts + 24 * 3600 * 1000
        expiry_ds = (current + timedelta(days=1)).strftime("%Y-%m-%d")

        idx = find_4h_idx(candles, entry_ts)
        price = candles[idx][4]

        # 滚动 IV
        daily_idx = 0
        for di, dc in enumerate(daily):
            if dc[0] <= entry_ts:
                daily_idx = di
        iv_daily = calc_rolling_iv(daily, daily_idx)

        # 事件处理
        mult_raw = get_v2_event_mult(ds)
        is_event_day = ds in EVENTS
        is_expiry_event = expiry_ds in EVENTS
        skip_trade = is_event_day or (mult_raw is None)  # 事件日 or 到期跨事件
        mult = 1.0 if skip_trade else mult_raw
        is_near = mult > 1.0
        min_dist = 0.05 if is_near else 0.035

        # 到期价和区间最低价
        expiry_price = get_close_at(candles, expiry_ts)
        period_low = get_period_low(candles, entry_ts, expiry_ts)
        actual_drop = (price - period_low) / price if period_low else 0

        # ── v2 计算 ──
        vol2, *_ = calc_vol_v2(candles, idx, price, iv_daily, ds)
        buy_dist_v2 = max(vol2 * mult, min_dist)
        strike_v2 = round_strike(price * (1 - buy_dist_v2))
        dist_v2 = (price - strike_v2) / price if price > 0 else 0

        skip_v2 = skip_trade
        if not skip_v2:
            v2_traded += 1
            if expiry_price is not None and expiry_price < strike_v2:
                v2_exercised += 1

        # ── v3 计算 ──
        vol3, c_iv, c_atr, c_bb, c_fund, c_buf, vol_acc, extras = calc_vol_v3(
            candles, idx, price, iv_daily, ds, daily, daily_idx)
        buy_dist_v3 = max(vol3 * mult, min_dist)
        strike_v3 = round_strike(price * (1 - buy_dist_v3))
        dist_v3 = (price - strike_v3) / price if price > 0 else 0

        skip_v3 = False
        reason = ""
        if is_event_day:
            skip_v3, reason = True, f"事件日:{EVENTS[ds][0]}"
        elif mult_raw is None:
            skip_v3, reason = True, f"到期跨:{EVENTS.get(expiry_ds, ('?',))[0]}"

        exercised_v3 = False
        if not skip_v3:
            v3_traded += 1
            if expiry_price is not None and expiry_price < strike_v3:
                exercised_v3 = True
                v3_exercised += 1
                v3_ex_details.append({
                    "ds": ds, "price": price, "strike": strike_v3,
                    "expiry_price": expiry_price, "dist": dist_v3,
                    "vol": vol3, "mult": mult, "extras": extras,
                })
        else:
            v3_skipped += 1
            key = reason.split(":")[0] if ":" in reason else reason[:6]
            v3_skip_reasons[key] = v3_skip_reasons.get(key, 0) + 1

        # v2 行权详情
        if not skip_v2 and expiry_price is not None and expiry_price < strike_v2:
            v2_ex_details.append({
                "ds": ds, "price": price, "strike": strike_v2,
                "expiry_price": expiry_price, "dist": dist_v2,
                "vol": vol2, "mult": mult,
            })

        results.append({
            "ds": ds, "price": price, "mult": mult,
            "vol_v2": vol2, "strike_v2": strike_v2, "dist_v2": dist_v2,
            "vol_v3": vol3, "strike_v3": strike_v3, "dist_v3": dist_v3,
            "expiry_price": expiry_price, "period_low": period_low,
            "actual_drop": actual_drop,
            "skip": skip_v3, "reason": reason, "exercised_v3": exercised_v3,
            "extras": extras,
            "skip_v2": skip_v2,
            "exercised_v2": (not skip_v2 and expiry_price is not None and expiry_price < strike_v2),
        })

        current += timedelta(days=1)

    # ══════════════════════════════════════════
    #  输出
    # ══════════════════════════════════════════
    print("=" * 160)
    print("  DCD PUT 策略回测 — v2 vs v3 对比（2025-10-01 ~ 2026-03-31，UTC+8 16:00，1天产品）")
    print("  v3 新增：回撤安全垫 + 暴跌动量 + 动态ATR权重")
    print("=" * 160)
    print(f"  全局30天HV: 日 {global_rv*100:.2f}% | 年化 {global_iv_annual*100:.1f}%")
    print()

    # 逐日表格
    hdr = (
        f"  {'日期':<11} {'BTC价':>8} |"
        f" {'v2vol':>6} {'v2行权价':>8} |"
        f" {'v3vol':>6} {'v3行权价':>8} {'DD%':>6} {'7d%':>6} |"
        f" {'到期价':>8} {'实跌%':>6} | {'v2':>4} {'v3':>4}"
    )
    print(hdr)
    print("  " + "-" * 155)

    for r in results:
        ds = r["ds"]
        ex = r["extras"]

        if r["skip"]:
            v2_res = "SKIP"
            v3_res = "SKIP"
        else:
            v2_res = "EX!" if r["exercised_v2"] else "OK"
            v3_res = "EX!" if r["exercised_v3"] else "OK"

        dd_s = f"{ex['drawdown_30d']*100:.0f}%"
        d7_s = f"{ex['drop_7d']*100:.1f}%"
        actual_s = f"{r['actual_drop']*100:.1f}%"

        marker = " <<" if (r["exercised_v2"] or r["exercised_v3"]) and not r["skip"] else ""

        print(
            f"  {ds:<11} {r['price']:>8,.0f} |"
            f" {r['vol_v2']*100:>5.2f}% {r['strike_v2']:>8,} |"
            f" {r['vol_v3']*100:>5.2f}% {r['strike_v3']:>8,} {dd_s:>6} {d7_s:>6} |"
            f" {r['expiry_price']:>8,.0f} {actual_s:>6} | {v2_res:>4} {v3_res:>4}{marker}"
        )

    # ══ 汇总对比 ══
    print()
    print("  " + "=" * 80)
    print("  v2 vs v3 汇总对比")
    print("  " + "=" * 80)
    print(f"  总天数:       {total_days}")
    print()
    print(f"  {'':15} {'v2':>10} {'v3':>10} {'变化':>10}")
    print(f"  {'-'*45}")
    print(f"  {'交易天数':15} {v2_traded:>10} {v3_traded:>10} {'':>10}")
    print(f"  {'行权次数':15} {v2_exercised:>10} {v3_exercised:>10} {v3_exercised - v2_exercised:>+10}")
    v2_safe_rate = (v2_traded - v2_exercised) / v2_traded * 100 if v2_traded else 0
    v3_safe_rate = (v3_traded - v3_exercised) / v3_traded * 100 if v3_traded else 0
    print(f"  {'安全率':15} {v2_safe_rate:>9.1f}% {v3_safe_rate:>9.1f}% {v3_safe_rate - v2_safe_rate:>+9.1f}%")

    # v3 跳过原因
    print()
    print(f"  v3 跳过 {v3_skipped} 天:")
    for k, v in sorted(v3_skip_reasons.items(), key=lambda x: -x[1]):
        print(f"    - {k}: {v}")

    # v2 行权详情
    if v2_ex_details:
        print()
        print("  " + "=" * 80)
        print("  v2 行权日详情")
        print("  " + "=" * 80)
        for d in v2_ex_details:
            print(f"  {d['ds']}  入场${d['price']:,.0f}  行权价${d['strike']:,}  到期${d['expiry_price']:,.0f}  vol={d['vol']*100:.2f}%  dist={d['dist']*100:.1f}%")

    # v3 行权详情
    if v3_ex_details:
        print()
        print("  " + "=" * 80)
        print("  v3 行权日详情（含新增缓冲分解）")
        print("  " + "=" * 80)
        for d in v3_ex_details:
            ex = d["extras"]
            print(f"  {d['ds']}  入场${d['price']:,.0f}  行权价${d['strike']:,}  到期${d['expiry_price']:,.0f}")
            print(f"    vol={d['vol']*100:.2f}%  dist={d['dist']*100:.1f}%  mult={d['mult']:.1f}x")
            print(f"    vol_base={ex['vol_base']*100:.2f}%  conf={ex['conf_buffer']*100:.1f}%  "
                  f"DD_adj={ex['drawdown_adj']*100:.1f}%  mom_adj={ex['momentum_adj']*100:.1f}%")
            print(f"    权重: IV={ex['w_iv']:.2f} ATR={ex['w_atr']:.2f}  "
                  f"DD30d={ex['drawdown_30d']*100:.0f}%  7d变动={ex['drop_7d']*100:.1f}%")
    else:
        print()
        print("  v3 零行权！")

    # 按月分段统计
    print()
    print("  " + "=" * 80)
    print("  按月统计")
    print("  " + "=" * 80)
    months = ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03"]
    for month in months:
        mr = [r for r in results if r["ds"].startswith(month)]
        if not mr:
            continue
        m_traded = sum(1 for r in mr if not r["skip"])
        m_exercised_v2 = sum(1 for r in mr if r.get("exercised_v2"))
        m_exercised_v3 = sum(1 for r in mr if r.get("exercised_v3"))
        m_skipped = sum(1 for r in mr if r["skip"])
        m_safe_v3 = m_traded - m_exercised_v3
        m_rate_v2 = f"{(m_traded-m_exercised_v2)/m_traded*100:.1f}%" if m_traded > 0 else "N/A"
        m_rate_v3 = f"{m_safe_v3/m_traded*100:.1f}%" if m_traded > 0 else "N/A"
        print(f"  {month}: 天数={len(mr)} 跳过={m_skipped} 交易={m_traded} | v2行权={m_exercised_v2}({m_rate_v2}) | v3行权={m_exercised_v3}({m_rate_v3})")


if __name__ == "__main__":
    run()
