# BTC 双币赢（DCD）双线策略

> **策略 A（PUT 低买）**：用 USDG 赚 USDG，尽量不被行权
> **策略 B（CALL 高卖）**：被行权拿到的 BTC，尽快卖回 USDG
> 两个策略可同时运行，资金互不冲突（A 用 USDG，B 用 BTC）。

```
  USDG ──PUT低买──┬── ✅ 未行权 → USDG+权利金 → 继续PUT
                  └── ❌ 被行权 → 收到BTC ──┐
                                             ▼
  BTC ──CALL高卖──┬── ✅ 被行权 → USDG回收 → 回到PUT
                  └── ❌ 未行权 → BTC+权利金 → 降低有效成本 → 继续CALL
```

---

## 策略 A：PUT 低买

### 硬性规则

1. **行权价 < 安全下界**（安全下界含 √到期天数 缩放，见公式）
2. **安全距离**：距现价 >= 3.5%（日常），>= 5%（事件日及前后 1 天）
3. **每天都交易**：事件日不跳过，用事件乘数加大安全距离（回测验证：89天全交易，安全率 98.9%）
4. **投入规则**：先选品再投入（不因投入而放松安全标准）。资金账户有 USDG/USDT 时按"资金拆份轮动"规则投入；资金账户无 USDG/USDT 时从交易账户划转（`account_transfer` from: 18, to: 6）后投入
5. **资金拆份轮动（2026-04-23 起启用）**：
   - **USDG 池**：总额（资金账户可用 + 在仓） >= 3,000 USDG 时拆为 **N 份**（N = min(5, max(3, floor(池额 / 2000))))，每份金额由公式动态决定（无硬性上限）。本金大幅变动时（注资/提取）需重新计算 N 与每份金额，并在 `交易记录.md` 顶部"本金注资记录"中登记时点（用于年化收益率统计）
   - **USDT 池**：总额 < 3,000 USDT 时仍单仓全仓滚动；>= 3,000 USDT 时同 USDG 规则拆份
   - **目标**：N 份错开到期（每 1 份 1 天间隔），任意一天至多 1 笔到期，避免单笔行权风险集中
   - **每日 cron 执行**：① 结算当日到期仓位 → ② 用"该份回收资金"下新 PUT（优先 2D，使其与其他份到期日错开）→ ③ 若当日无到期但有闲置份（如新注资首日），则按缺失的错开槽位下新单直至分份数满
   - **分份初始化**：USDG 池刚满足拆份阈值时，首日全仓投入一个 2D 产品，次日起拆份逻辑自然接管（结算回收即按错开槽位下单）
6. **交错到期（旧单仓模式，仅 USDT < 3,000 时适用）**：保持 2 笔 PUT 在仓、到期日错开 1 天
   - 每天 cron 执行时：结算到期仓位 → 立即用回收资金下新 PUT（优先 2 天期，与另一仓位错开）
   - 只有 1 笔资金时（如策略 B 刚释放的 USDT）：正常下单，下一轮自然形成交错
6. **币种选择**：选品时同时比较 USDG 和 USDT 版本的产品，**用手上持有的币种下单**
   - 不做 USDG↔USDT 互换（现货手续费 ~0.1%，远超短期 APY 差额带来的收益）
   - 同时持有 USDG 和 USDT 时：各自独立选品下单（交错到期的天然资金池）
   - 仅在一侧完全无合格产品、另一侧有时，才考虑互换（极少发生）

### 软性规则
- FOMC 周只选 1 天产品，平时优先 1 天（√N 缩放下 1 天产品安全下界最宽松）
- 事件乘数按自适应事件乘数表（见波动率预测 v3）
- BTC 跌至距行权价 < 1% 时提醒用户关注提前赎回
- 连续被行权 2 次后暂停 1 天
- 1D 和 2D 均不满足安全距离时，尝试 3D-5D 产品（不轻易跳过）

### 波动率预测（v3）

> **完整算法实现**：`calc_volatility.py`（唯一代码来源，含 CLI 和可导入函数）
> **回测验证**：`backtest/run_backtest_3m.py`（3 个月，89 天全交易，v3 安全率 98.9%，仅 1 次行权）

#### 核心思路

v3 = v2 基础 + 三项危机感知：

| 层级 | 成分 | 作用 |
|------|------|------|
| 基础 | IV×w + ATR×w + BB×0.15 + 资金费率×0.10 | 综合多源波动率 |
| 置信缓冲 | vol_accel 或临近事件 → +0.3~1.0% | 防低估 |
| **回撤安全垫** | 距30天高点回撤 >10%→+1.5%, >20%→+2.5%, >30%→+4.0% | 识别脆弱状态 |
| **暴跌动量** | 7天跌幅 >5%→+1.5%, >10%→+3.0% | 捕捉下跌趋势 |
| **动态权重** | ATR > 2×IV 时，ATR 权重升至 0.55（IV 降至 0.20） | 急跌时 IV 滞后补偿 |

#### 数据源

| 工具 | 参数 | 用途 |
|------|------|------|
| `option_get_greeks` | BTC 近月 ATM 期权 | 真实期权 IV |
| `market_get_indicator` | `indicator: ATR, period: 7, bar: 1H` | 快速 ATR |
| `market_get_indicator` | `indicator: ATR, period: 14, bar: 1H` | 慢速 ATR |
| `market_get_indicator` | `indicator: BOLLINGER, period: 20, bar: 1H` | 1H BB |
| `market_get_funding_rate` | `instId: BTC-USD-SWAP` | 资金费率 |
| `market_get_index_candles` | `bar: 1D, limit: 30` | 30天高点 + 7天前价格（v3） |

> **IV 降级**：若 `option_get_greeks` 不可用，降级为 30 天 HV（权重从 0.45 降至 0.35，差额补入 confidence_buffer）。

#### 安全下界计算

```
buy_dist = max(vol_24h × √到期天数 × 事件乘数, 最小安全距离)
行权价 = BTC价格 × (1 - buy_dist)，向下取整到 500
```

> **每天交易，永不跳过**：v3 的回撤安全垫 + 暴跌动量在危机时自动大幅抬高 vol，配合事件乘数，安全距离远超实际跌幅。回测验证 89 天全交易仅 1 次行权（98.9%）。

#### 自适应事件乘数表

| 事件类型 | 当天 | 前1天 | 后1天 | 最低安全距离 |
|---------|------|-------|-------|-------------|
| FOMC 决议 | 1.8× | 1.8× | 1.3× | >= 5.0% |
| CPI 数据 | 1.5× | 1.5× | 1.2× | >= 5.0% |
| CME 交割 | 1.5× | 1.5× | 1.3× | >= 5.0% |
| 就业数据(NFP) | 1.4× | 1.4× | 1.2× | >= 5.0% |
| 多重到期日 | 1.8× | 1.8× | 1.5× | >= 5.0% |
| 无事件 | 1.0× | — | — | >= 3.5% |

### PUT 选品逻辑

1. 只选风险评级 ✅（行权价 < 安全下界）的产品
2. 同一行权价+到期日，比较 BTC-USDT 和 BTC-USDG 两个产品，选 APY 更高的
3. ✅ 中选 APY 最高（但 <= 150%）；全部 APY < 5% 则跳过
4. 优先 1 天产品
5. 同 APY 选距安全下界更远的

---

## 策略 B：CALL 高卖

> 目标：行权价 >= 合并有效成本时被行权，保本卖出全部 BTC，回收 USDG。
> 绝不主动亏损卖出。每轮权利金持续降低有效成本，时间站在我们这边。

### 合并有效成本

多次 PUT 被行权时，所有 BTC 合并管理：

```
合并有效成本 = 所有批次总投入USDG ÷ 所有批次总持有BTC（含累计权利金）

每轮 CALL 未行权后更新：
新合并有效成本 = 总投入USDG ÷ (总持有BTC + 本轮权利金BTC)

新 PUT 被行权时：新增 BTC 并入合并池，重新计算。
```

全部 BTC 投入同一个 CALL，不分批。交易记录保留各批次原始成本便于归因。

### 硬性规则

1. **行权价 >= 合并有效成本**：绝不低于有效成本，绝不主动亏损卖出
2. **不做市价止损**：BTC 下跌时持续通过 CALL 积累权利金，耐心等待
3. **每天交易**：与 PUT 一致，事件日不跳过

> **距现价不设下限**：行权价距现价越近 = 行权概率越高 = 越有利于退出。

### CALL 选品逻辑（分级：CLOSE / FAR）

> **决策分级**：根据 BTC 现价距合并有效成本的 gap，分两档选 strike。
> 代码实现：`calc_volatility.py` 中的 `calc_sell_strike()`。

**分级阈值**：
- `gap_pct = (breakeven - price) / price`
- `one_sigma_1d = vol_24h`（即 v3 引擎返回的 1 天预测波动率）
- **gap_pct > one_sigma_1d → FAR**
- **gap_pct <= one_sigma_1d → CLOSE**（含 BTC 高于回本的情况）

#### CLOSE 档（BTC 接近或高于回本）

**目标**：保本行权。

- **行权价 = ceil(breakeven / 500) × 500**（回本价向上取整到 500）
- 从可用产品中选最接近且 >= 该 strike 的实际产品
- 优先 1 天产品，同档比 USDT/USDG 选 APY 高的

#### FAR 档（BTC 远低于回本）

**目标**：最大化权利金降本，行权概率压到 10-15%。
**尾部风险**：strike 必然 < breakeven，BTC 意外暴涨到 strike 会小幅亏损卖出（可接受）。

- `sell_dist = max(vol_24h × √到期天数 × 事件乘数, min_dist)`
- `upper_bound = price × (1 + sell_dist)`
- **行权价 = ceil(upper_bound / 500) × 500 + 500**（上界向上取整 + $500 缓冲）
- 从可用产品中选最接近且 >= 该 strike 的实际产品
- 优先 1 天产品，同档比 USDT/USDG 选 APY 高的

#### 通用规则

- BTC 可用量 <= 0.0001 则跳过
- 低 APY 完全可接受（低 APY 也是正权利金）
- 尽量与策略 A 同一天到期
- 每轮复盘后更新合并有效成本

### 执行规则

- 全仓投入全部 BTC，向下取整到 0.0001
- 低 APY 完全可接受（低 APY 也是正权利金）
- 尽量与策略 A 同一天到期
- 每轮复盘后更新合并有效成本

### 退出条件

1. **被行权** → BTC 卖出换回 USDG → 回到策略 A
2. **有效成本降至当前价以下** → 可市价卖出或继续 CALL 赚权利金

---

## 建仓窗口

> **每天 16:03 UTC+8 自动交易，无跳过日。** 事件日通过乘数加大安全距离。

| 时机 | PUT | CALL |
|------|-----|------|
| 普通非事件日 | ✅ mult=1.0, min_dist=3.5% | ✅ |
| 事件前 1 天 | ✅ 对应乘数, min_dist=5% | ✅ |
| 事件当天 | ✅ 对应乘数, min_dist=5% | ✅ |
| 事件后 1 天 | ✅ 对应乘数, min_dist=5% | ✅ |

---

## 执行流程（PUT + CALL 统一）

### Step 1：并行获取数据

| 工具 | 参数 | 用途 |
|------|------|------|
| `market_get_index_ticker` | `instId: BTC-USD` | BTC 现价 |
| `market_get_index_candles` | `instId: BTC-USD, bar: 1D, limit: 30` | 30天高点+7天前价格（v3） |
| `market_get_indicator` | `instId: BTC-USD-SWAP, indicator: ATR, period: 7, bar: 1H` | 快速 ATR |
| `market_get_indicator` | `instId: BTC-USD-SWAP, indicator: ATR, period: 14, bar: 1H` | 慢速 ATR |
| `market_get_indicator` | `instId: BTC-USD-SWAP, indicator: BOLLINGER, period: 20, bar: 1H` | 1H BB |
| `option_get_greeks` | BTC 近月 ATM 期权 | 真实 IV |
| `market_get_funding_rate` | `instId: BTC-USD-SWAP` | 资金费率 |
| `dcd_get_products` | `baseCcy: BTC, quoteCcy: USDG, optType: P` + `C` | DCD 产品（USDG） |
| `dcd_get_products` | `baseCcy: BTC, quoteCcy: USDT, optType: P` + `C` | DCD 产品（USDT，对比用） |
| `account_get_asset_balance` | `ccy: USDG,BTC` | 余额 |
| WebSearch | FOMC / CPI / CME 近期日历 | 事件风险 |

> 资金账户无 USDG 时，从交易账户划转 1000 USDG：`account_transfer`（from: 18, to: 6, ccy: USDG, amt: 1000）。
> BTC 如在交易账户也需先划转。

### Step 2：计算波动率 + 安全下界

调用 `calc_volatility.py` 中的 `calc_vol_v3()` 和 `calc_strike_and_dist()`：
1. 用 Step 1 获取的数据计算 `vol_24h`
2. `buy_dist = max(vol_24h × √到期天数 × 事件乘数, 最小安全距离)`
3. 行权价向下取整到 500，确保行权价 < 安全下界

### Step 3：下单

- **USDG >= 10**（资金账户无 USDG 则先从交易账户划转 1000）→ 按 PUT 选品逻辑下单 `dcd_subscribe`（notionalCcy: USDG）
- **BTC >= 0.0001** → 按 CALL 选品逻辑下单 `dcd_subscribe`（notionalCcy: BTC）
- 下单失败不重试，记录原因

### Step 4：记录

更新 `交易记录.md`，包含订单详情、合并有效成本、资产快照。
