#!/usr/bin/env python3
"""
币安土狗猎手 — 低市值高波动币 动量剥头皮回测
=============================================
策略: 扫币安全部 USDT 对 → 筛选土狗特征 → 1m 动量回测
信号: 1m动量 > 0.1% + 量能放大 + 短期趋势向上
"""
import requests, math, time
from datetime import datetime, timezone, timedelta
from collections import Counter

# ====================== 配置 ======================
TRADE_AMOUNT   = 5.0           # $5/笔
SIGNAL_MOMENTUM= 0.1           # 1m动量 > 0.1% (放松!)
MIN_VOL_24H    = 200_000       # 24h最低成交量
MAX_MCAP       = 50_000_000    # 最大市值 (土狗标准)
MAX_PRICE      = 1.0           # 价格 < $1 (低价=高波动潜力)
MIN_CHANGE_24  = 5             # 24h波动 > 5% (有活性)

TP_PCT  = 3.0                  # 止盈 +3%
SL_PCT  = 1.5                  # 止损 -1.5%
MAX_HOLD= 10                   # 持仓 10 分钟
FEE_PCT = 0.00005              # 币安 BAW 0.005%

# ====================== 发现候选币 ======================
def find_degen_coins():
    """从币安找出土狗特征的代币"""
    try:
        resp = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=15)
        all_tickers = resp.json()
    except:
        print("  ⚠️ 币安 API 失败")
        return []
    
    if not isinstance(all_tickers, list):
        return []
    
    candidates = []
    for t in all_tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue
        
        price       = float(t["lastPrice"])
        change_24h  = float(t["priceChangePercent"])
        vol_24h     = float(t["quoteVolume"])
        high_24h    = float(t["highPrice"])
        low_24h     = float(t["lowPrice"])
        
        # 土狗过滤
        if price > MAX_PRICE:       # 价格太高
            continue
        if vol_24h < MIN_VOL_24H:   # 没量
            continue
        if abs(change_24h) < MIN_CHANGE_24:  # 不够活跃
            continue
        
        # 振幅
        if low_24h > 0:
            amplitude = (high_24h - low_24h) / low_24h * 100
        else:
            amplitude = 0
        if amplitude < 10:          # 24h振幅太低
            continue
        
        name = sym.replace("USDT", "")
        candidates.append({
            "symbol": sym,
            "name": name,
            "price": price,
            "change_24h": change_24h,
            "amplitude_24h": amplitude,
            "vol_24h": vol_24h,
        })
    
    # 按成交量 + 波动排序
    candidates.sort(key=lambda x: -(x["amplitude_24h"] * x["vol_24h"]**0.3))
    return candidates[:30]  # 最多30个

# ====================== K线 + 回测 ======================
def fetch_klines(symbol, hours=24):
    """币安 1m K线"""
    all_klines = []
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)
    
    while True:
        resp = requests.get("https://api.binance.com/api/v3/klines", params={
            "symbol": symbol, "interval": "1m",
            "startTime": start_ms, "endTime": end_ms, "limit": 1000
        }, timeout=15)
        batch = resp.json()
        if not isinstance(batch, list):
            break
        for k in batch:
            all_klines.append({
                "ts": k[0], "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]), "vol": float(k[5]),
                "quote_vol": float(k[7]),
            })
        if len(batch) < 1000:
            break
        start_ms = batch[-1][0] + 1
        time.sleep(0.15)
    return sorted(all_klines, key=lambda x: x["ts"])

def backtest_degen(candles, name, tp, sl, hold, mom_threshold=0.1):
    """
    土狗回测:
    - 信号: 1m动量 > mom_threshold + 量能放大(当前量 > 前5根均量*1.5)
    - 入场: close 价
    - 出场: TP/SL/超时
    """
    trades = []
    
    for i in range(6, len(candles) - hold):
        c = candles[i]
        prev = candles[i-1]
        momentum = (c["close"] - prev["close"]) / prev["close"] * 100
        
        if momentum <= mom_threshold:
            continue
        
        # 量能放大检查
        avg_vol = sum(k["quote_vol"] for k in candles[i-5:i]) / 5
        if c["quote_vol"] < avg_vol * 1.2:  # 放松到1.2倍
            continue
        
        # 入场
        entry = c["close"]
        
        # 模拟持仓
        for offset in range(1, hold + 1):
            idx = i + offset
            if idx >= len(candles):
                break
            k = candles[idx]
            
            pnl_high = (k["high"] - entry) / entry * 100
            pnl_low  = (k["low"]  - entry) / entry * 100
            
            if pnl_high >= tp and pnl_low <= -sl:
                open_pnl = (k["open"] - entry) / entry * 100
                if abs(open_pnl - tp) < abs(open_pnl + sl):
                    trades.append({"reason": "✅止盈", "pnl_pct": tp, "hold": offset})
                else:
                    trades.append({"reason": "🛑止损", "pnl_pct": -sl, "hold": offset})
                break
            elif pnl_high >= tp:
                trades.append({"reason": "✅止盈", "pnl_pct": tp, "hold": offset})
                break
            elif pnl_low <= -sl:
                trades.append({"reason": "🛑止损", "pnl_pct": -sl, "hold": offset})
                break
        else:
            last = candles[min(i + hold, len(candles)-1)]
            pnl = (last["close"] - entry) / entry * 100
            trades.append({"reason": "⏰超时", "pnl_pct": pnl, "hold": hold})
        
        i += 1  # 允许重叠信号
    
    return trades

def print_token_report(name, trades, price, change_24h, amp_24h, vol_24h):
    """单币报告"""
    if not trades:
        return None
    
    wins   = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    total  = len(trades)
    total_pnl = sum(t["pnl_pct"] for t in trades)
    wr = len(wins)/total*100 if total else 0
    reasons = Counter(t["reason"] for t in trades)
    avg_pnl = total_pnl / total
    net_usd = TRADE_AMOUNT * total_pnl / 100
    
    # 盈亏比
    avg_w = sum(t["pnl_pct"] for t in wins)/max(len(wins),1)
    avg_l = sum(abs(t["pnl_pct"]) for t in losses)/max(len(losses),1)
    rr = avg_w/max(avg_l, 0.001)
    
    icon = "🟢" if net_usd > 0.5 else "🟡" if net_usd > 0 else "🔴"
    print(f"  {icon} {name:10s} ${price:<10.6f}  "
          f"{total:>3d}笔  WR{wr:>4.0f}%  "
          f"止盈{reasons.get('✅止盈',0):>2d} 止损{reasons.get('🛑止损',0):>2d} 超时{reasons.get('⏰超时',0):>2d}  "
          f"PnL{net_usd:>+.2f}$  RR{rr:.1f}")
    
    return {
        "name": name, "trades": total, "wr": wr, "net_usd": net_usd,
        "total_pnl": total_pnl, "rr": rr, "reasons": dict(reasons),
        "change_24h": change_24h, "amplitude_24h": amp_24h, "vol_24h": vol_24h,
    }

# ====================== 主程序 ======================
def run():
    print("═" * 60)
    print("  🦴 币安土狗猎手 — 低市值高波动币回测")
    print(f"  ⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("═" * 60)
    print(f"\n  数据: 币安 1m K线 (24h)")
    print(f"  执行: BAW 模拟 (费 {FEE_PCT*100:.3f}%)")
    print(f"  参数: TP +{TP_PCT}% / SL -{SL_PCT}% / 持仓 {MAX_HOLD}min")
    print(f"  信号: 1m动量 > {SIGNAL_MOMENTUM}% + 量能放大 1.2x")
    print(f"\n  📡 扫描币安全部 USDT 对 → 筛选土狗特征...")
    
    # Step 1: 找土狗
    candidates = find_degen_coins()
    print(f"     找到 {len(candidates)} 个候选土狗币\n")
    
    if not candidates:
        print("  ⚠️ 没有符合条件的币")
        return
    
    # 打印候选列表
    print(f"  {'候选币':<12s} {'价格':>12s} {'24h涨':>8s} {'振幅':>8s} {'24h量':>12s}")
    print(f"  {'─'*55}")
    for c in candidates[:15]:
        print(f"  {c['name']:<12s} ${c['price']:<11.6f} {c['change_24h']:>+7.1f}% {c['amplitude_24h']:>7.1f}% ${c['vol_24h']:>10,.0f}")
    
    # Step 2: 逐一拉 K 线回测
    print(f"\n  🔬 开始回测 (拉 {len(candidates)} 个币的 1m K线)...\n")
    
    all_results = []
    processed = 0
    
    for c in candidates:
        name = c["name"]
        print(f"  🔽 {name:10s}...", end=" ", flush=True)
        
        try:
            candles = fetch_klines(c["symbol"], 24)
        except:
            print("K线失败")
            continue
        
        if len(candles) < 60:
            print(f"数据不足({len(candles)}根)")
            continue
        
        trades = backtest_degen(candles, name, TP_PCT, SL_PCT, MAX_HOLD, SIGNAL_MOMENTUM)
        result = print_token_report(name, trades, c["price"], c["change_24h"], c["amplitude_24h"], c["vol_24h"])
        if result:
            all_results.append(result)
        
        processed += 1
        time.sleep(0.2)
    
    # Step 3: 汇总
    if not all_results:
        print("\n  ⚠️ 无交易信号")
        return
    
    total_trades  = sum(r["trades"] for r in all_results)
    total_profit  = sum(r["net_usd"] for r in all_results)
    total_win_t   = sum(r["reasons"].get("✅止盈",0) for r in all_results)
    total_sl_t    = sum(r["reasons"].get("🛑止损",0) for r in all_results)
    total_to_t    = sum(r["reasons"].get("⏰超时",0) for r in all_results)
    
    winners = [r for r in all_results if r["net_usd"] > 0]
    
    print(f"\n{'═'*60}")
    print(f"  📊 币安土狗汇总报告")
    print(f"{'═'*60}\n")
    print(f"  扫描币种: {len(candidates)} | 回测: {processed} | 有交易: {len(all_results)}")
    print(f"  总交易: {total_trades}笔 | 止盈:{total_win_t} 止损:{total_sl_t} 超时:{total_to_t}")
    print(f"  盈利币: {len(winners)}/{len(all_results)}")
    print(f"  💰 总盈亏: ${total_profit:+.2f}")
    if total_trades > 0:
        print(f"  📈 胜率: {sum(r['trades']*r['wr']/100 for r in all_results)/total_trades*100:.0f}%")
    print(f"\n  🏆 盈利 TOP5:")
    for r in sorted(all_results, key=lambda x: -x["net_usd"])[:5]:
        print(f"    {r['name']:10s} {r['trades']:>3d}笔 WR{r['wr']:.0f}% PnL{r['net_usd']:+.2f}$ RR{r['rr']:.1f}")
    
    print(f"\n  📉 亏损 TOP5:")
    for r in sorted(all_results, key=lambda x: x["net_usd"])[:5]:
        print(f"    {r['name']:10s} {r['trades']:>3d}笔 WR{r['wr']:.0f}% PnL{r['net_usd']:+.2f}$ RR{r['rr']:.1f}")

if __name__ == "__main__":
    run()
