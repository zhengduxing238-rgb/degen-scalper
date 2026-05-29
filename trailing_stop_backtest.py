#!/usr/bin/env python3
"""
移动止盈 vs 固定止盈 对比回测
=============================
策略: 固定止损 + 移动止盈
- 亏到止损线 → 无条件止损
- 盈利超过激活阈值 → 启动移动止盈 (追踪最高点, 回撤N就卖)
- 超时未触发 → 市价平仓
"""
import requests, math, time
from datetime import datetime, timezone, timedelta
from collections import Counter

TARGETS = ["ALLOUSDT", "AIUSDT", "IDUSDT", "HBARUSDT", "ADXUSDT"]
TRADE_USD = 5.0
HOLD_MIN  = 15
MOM_THRESHOLD = 0.1
VOL_BOOST = 1.2

# ═══════════ 测试参数 ═══════════
# 固定止损 (美元)
SL_OPTIONS = [0.08, 0.10, 0.12]
# 移动止盈: (激活利润, 回撤幅度)
TRAILING_OPTIONS = [
    # (激活$, 回撤$)
    (0.15, 0.05),   # 赚$0.15后, 回撤$0.05就卖
    (0.20, 0.05),
    (0.25, 0.05),
    (0.15, 0.08),   # 赚$0.15后, 回撤$0.08就卖
    (0.20, 0.08),
    (0.25, 0.08),
    (0.15, 0.03),   # 赚$0.15后, 回撤$0.03就卖 (紧)
    (0.20, 0.03),
]

# 固定止盈对照组
FIXED_TP_OPTIONS = [0.20, 0.25, 0.30, 0.35]

def fetch_klines(symbol, hours=24):
    all_klines = []
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)
    while True:
        resp = requests.get("https://api.binance.com/api/v3/klines", params={
            "symbol": symbol, "interval": "1m",
            "startTime": start_ms, "endTime": end_ms, "limit": 1000
        }, timeout=15)
        batch = resp.json()
        if not isinstance(batch, list): break
        for k in batch:
            all_klines.append({
                "ts": k[0], "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]),
                "quote_vol": float(k[7]),
            })
        if len(batch) < 1000: break
        start_ms = batch[-1][0] + 1
        time.sleep(0.15)
    return sorted(all_klines, key=lambda x: x["ts"])

def backtest_trailing(candles, sl_usd, activate_usd, trail_usd, hold):
    """
    移动止盈回测:
    - 固定止损: 价格 <= entry - sl_usd/qty → 止损
    - 移动止盈: 如果最高价 - entry >= activate_usd/qty → 启动
                 如果价格 <= 最高价 - trail_usd/qty → 止盈
    - 超时平仓
    """
    trades = []
    for i in range(6, len(candles) - hold):
        c = candles[i]
        prev = candles[i-1]
        mom = (c["close"] - prev["close"]) / prev["close"] * 100
        if mom <= MOM_THRESHOLD: continue
        
        avg_vol = sum(k["quote_vol"] for k in candles[i-5:i]) / 5
        if c["quote_vol"] < avg_vol * VOL_BOOST: continue
        
        entry = c["close"]
        qty = TRADE_USD / entry
        sl_price = entry - sl_usd / qty
        activated = False
        highest = entry
        
        for offset in range(1, hold + 1):
            idx = i + offset
            if idx >= len(candles): break
            k = candles[idx]
            
            # 更新最高价
            if k["high"] > highest:
                highest = k["high"]
            
            # 检查移动止盈激活
            if not activated and (highest - entry) * qty >= activate_usd:
                activated = True
            
            # 移动止盈触发
            if activated and k["low"] <= highest - trail_usd / qty:
                pnl = (highest - trail_usd / qty - entry) * qty
                trades.append({"reason": "移动止盈", "pnl": pnl, "hold": offset})
                break
            
            # 固定止损 (优先)
            if k["low"] <= sl_price:
                trades.append({"reason": "止损", "pnl": -sl_usd, "hold": offset})
                break
        else:
            last = candles[min(i + hold, len(candles)-1)]
            pnl = (last["close"] - entry) * qty
            trades.append({"reason": "超时", "pnl": pnl, "hold": hold})
        i += 1
    return trades

def backtest_fixed(candles, tp_usd, sl_usd, hold):
    trades = []
    for i in range(6, len(candles) - hold):
        c = candles[i]
        prev = candles[i-1]
        mom = (c["close"] - prev["close"]) / prev["close"] * 100
        if mom <= MOM_THRESHOLD: continue
        
        avg_vol = sum(k["quote_vol"] for k in candles[i-5:i]) / 5
        if c["quote_vol"] < avg_vol * VOL_BOOST: continue
        
        entry = c["close"]
        qty = TRADE_USD / entry
        tp_price = entry + tp_usd / qty
        sl_price = entry - sl_usd / qty
        
        for offset in range(1, hold + 1):
            idx = i + offset
            if idx >= len(candles): break
            k = candles[idx]
            
            hit_tp = k["high"] >= tp_price
            hit_sl = k["low"]  <= sl_price
            
            if hit_tp and hit_sl:
                if abs(k["open"] - tp_price) < abs(k["open"] - sl_price):
                    trades.append({"reason": "固定止盈", "pnl": tp_usd, "hold": offset})
                else:
                    trades.append({"reason": "止损", "pnl": -sl_usd, "hold": offset})
                break
            elif hit_tp:
                trades.append({"reason": "固定止盈", "pnl": tp_usd, "hold": offset})
                break
            elif hit_sl:
                trades.append({"reason": "止损", "pnl": -sl_usd, "hold": offset})
                break
        else:
            last = candles[min(i + hold, len(candles)-1)]
            pnl = (last["close"] - entry) * qty
            trades.append({"reason": "超时", "pnl": pnl, "hold": hold})
        i += 1
    return trades

def score_trades(trades):
    if not trades: return -999, {}
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total = len(trades)
    total_pnl = sum(t["pnl"] for t in trades)
    wr = len(wins)/total*100 if total else 0
    avg_w = sum(t["pnl"] for t in wins)/max(len(wins), 1)
    avg_l = sum(abs(t["pnl"]) for t in losses)/max(len(losses), 1)
    rr = avg_w/max(avg_l, 0.001)
    reasons = Counter(t["reason"] for t in trades)
    s = total_pnl * (1 + wr/100) * (1 + min(rr, 5)/5)
    return s, {
        "trades": total, "wr": wr, "total_pnl": total_pnl,
        "avg_w": avg_w, "avg_l": avg_l, "rr": rr,
        "reasons": dict(reasons),
    }

# ═══════════ 主 ═══════════
print("═" * 60)
print("  🎯 移动止盈 vs 固定止盈 对比回测")
print(f"  ⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
print("═" * 60)

all_results = {}

for sym in TARGETS:
    name = sym.replace("USDT", "")
    print(f"\n{'─'*55}")
    print(f"  🪙 {name}")
    
    print(f"  📡 拉K线...", end=" ", flush=True)
    candles = fetch_klines(sym, 24)
    print(f"{len(candles)}根")
    if len(candles) < 60: continue
    
    # ── 移动止盈 ──
    print(f"\n  🔄 移动止盈:")
    print(f"  {'激活$':>6s} {'回撤$':>6s} {'止损$':>6s} {'交易':>5s} {'胜率':>5s} {'PnL':>8s} {'RR':>5s} {'出场':>20s}")
    print(f"  {'─'*70}")
    
    best_trail = None
    best_trail_score = -999
    
    for sl in SL_OPTIONS:
        for activate, trail in TRAILING_OPTIONS:
            trades = backtest_trailing(candles, sl, activate, trail, HOLD_MIN)
            s, stats = score_trades(trades)
            if stats["trades"] < 10: continue
            
            reasons_str = " ".join(f"{k}:{v}" for k,v in stats["reasons"].items())
            print(f"   ${activate:<5.2f} ${trail:<5.2f} ${sl:<5.2f}  "
                  f"{stats['trades']:>4d}笔  {stats['wr']:>4.0f}%  "
                  f"${stats['total_pnl']:>+7.2f}  {stats['rr']:>4.1f}  {reasons_str}")
            
            if s > best_trail_score:
                best_trail_score = s
                best_trail = {"sl": sl, "activate": activate, "trail": trail, **stats}
    
    # ── 固定止盈 ──
    best_fixed = None
    best_fixed_score = -999
    
    for sl in SL_OPTIONS:
        for tp in FIXED_TP_OPTIONS:
            trades = backtest_fixed(candles, tp, sl, HOLD_MIN)
            s, stats = score_trades(trades)
            if stats["trades"] < 10: continue
            
            if s > best_fixed_score:
                best_fixed_score = s
                best_fixed = {"tp": tp, "sl": sl, **stats}
    
    # ── 对比 ──
    print(f"\n  {'📊 对比':─^50}")
    if best_trail and best_fixed:
        trail_pnl = best_trail["total_pnl"]
        fixed_pnl = best_fixed["total_pnl"]
        winner = "🔄 移动止盈胜" if trail_pnl > fixed_pnl else "📌 固定止盈胜"
        diff = trail_pnl - fixed_pnl
        
        print(f"  移动止盈: 激活${best_trail['activate']} 回撤${best_trail['trail']} 止损${best_trail['sl']}")
        print(f"            {best_trail['trades']}笔 WR{best_trail['wr']:.0f}% PnL ${best_trail['total_pnl']:+.2f}")
        print(f"  固定止盈: TP${best_fixed['tp']} SL${best_fixed['sl']}")
        print(f"            {best_fixed['trades']}笔 WR{best_fixed['wr']:.0f}% PnL ${best_fixed['total_pnl']:+.2f}")
        print(f"  → {winner} (差额 ${diff:+.2f})")
    
    all_results[name] = {
        "trail_best": best_trail,
        "fixed_best": best_fixed,
    }
    
    time.sleep(0.3)

# ═══════════ 全量汇总 ═══════════
print(f"\n{'═'*60}")
print(f"  📋 全量对比汇总")
print(f"{'═'*60}\n")

trail_total = 0
fixed_total = 0

print(f"  {'币':<8s} {'移动止盈参数':>20s} {'PnL':>8s} │ {'固定止盈参数':>20s} {'PnL':>8s} │ {'胜者':>6s}")
print(f"  {'─'*80}")

for name, r in all_results.items():
    tb = r["trail_best"]
    fb = r["fixed_best"]
    
    if tb and fb:
        trail_str = f"激活${tb['activate']} 回撤${tb['trail']} 止损${tb['sl']}"
        fixed_str = f"TP${fb['tp']} SL${fb['sl']}"
        winner = "移动" if tb["total_pnl"] > fb["total_pnl"] else "固定"
        trail_total += tb["total_pnl"]
        fixed_total += fb["total_pnl"]
        
        print(f"  {name:<8s} {trail_str:<20s} ${tb['total_pnl']:>+6.2f} │ "
              f"{fixed_str:<20s} ${fb['total_pnl']:>+6.2f} │ {winner:>6s}")

print(f"\n  💰 移动止盈总PnL: ${trail_total:+.2f}")
print(f"  💰 固定止盈总PnL: ${fixed_total:+.2f}")
print(f"  🏆 胜者: {'移动止盈' if trail_total > fixed_total else '固定止盈'} (差 ${abs(trail_total-fixed_total):.2f})")
