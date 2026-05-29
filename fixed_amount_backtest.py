#!/usr/bin/env python3
"""
固定金额 TP/SL 回测 — 5 只土狗
==============================
仓位 $5，测多组固定金额组合，找最优
"""
import requests, math, time
from datetime import datetime, timezone, timedelta
from collections import Counter

TARGETS = ["ALLOUSDT", "AIUSDT", "IDUSDT", "HBARUSDT", "ADXUSDT"]
TRADE_USD = 5.0

# 固定金额测试组 (TP金额, SL金额)
FIXED_COMBOS = [
    (0.20, 0.08),   # TP$0.20/4%  SL$0.08/1.6%
    (0.25, 0.10),   # TP$0.25/5%  SL$0.10/2%
    (0.30, 0.12),   # TP$0.30/6%  SL$0.12/2.4%
    (0.35, 0.12),   # TP$0.35/7%  SL$0.12/2.4%
    (0.25, 0.08),   # TP$0.25/5%  SL$0.08/1.6%
    (0.30, 0.10),   # TP$0.30/6%  SL$0.10/2%
    (0.20, 0.10),   # TP$0.20/4%  SL$0.10/2%
]

HOLD_MIN = 15
MOM_THRESHOLD = 0.1  # 1m动量 > 0.1%
VOL_BOOST = 1.2       # 量能放大

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
                "low": float(k[3]), "close": float(k[4]), "vol": float(k[5]),
                "quote_vol": float(k[7]),
            })
        if len(batch) < 1000: break
        start_ms = batch[-1][0] + 1
        time.sleep(0.15)
    return sorted(all_klines, key=lambda x: x["ts"])

def backtest_fixed(candles, tp_usd, sl_usd, hold):
    """
    固定金额回测:
    - $5 入场 → qty = 5 / price
    - 止盈价 = entry + tp_usd / qty
    - 止损价 = entry - sl_usd / qty
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
        tp_price = entry + tp_usd / qty
        sl_price = entry - sl_usd / qty
        
        for offset in range(1, hold + 1):
            idx = i + offset
            if idx >= len(candles): break
            k = candles[idx]
            
            hit_tp = k["high"] >= tp_price
            hit_sl = k["low"] <= sl_price
            
            if hit_tp and hit_sl:
                # 同根触发，用开盘判断
                if abs(k["open"] - tp_price) < abs(k["open"] - sl_price):
                    trades.append({"reason": "TP", "pnl": tp_usd, "hold": offset})
                else:
                    trades.append({"reason": "SL", "pnl": -sl_usd, "hold": offset})
                break
            elif hit_tp:
                trades.append({"reason": "TP", "pnl": tp_usd, "hold": offset})
                break
            elif hit_sl:
                trades.append({"reason": "SL", "pnl": -sl_usd, "hold": offset})
                break
        else:
            last = candles[min(i + hold, len(candles)-1)]
            pnl = (last["close"] - entry) * qty
            trades.append({"reason": "TO", "pnl": pnl, "hold": hold})
        i += 1
    return trades

def score(trades):
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
        "tp_count": reasons.get("TP", 0),
        "sl_count": reasons.get("SL", 0),
        "to_count": reasons.get("TO", 0),
    }

# ====================== 主 ======================
print("═" * 60)
print("  💵 固定金额 TP/SL 回测")
print(f"  ⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
print("═" * 60)
print(f"\n  仓位: ${TRADE_USD}/笔 | 持仓: {HOLD_MIN}min")
print(f"  信号: 1m动量 > {MOM_THRESHOLD}% + 量能放大 {VOL_BOOST}x")
print(f"  测试 {len(FIXED_COMBOS)} 组金额组合\n")

all_best = []

for sym in TARGETS:
    name = sym.replace("USDT", "")
    print(f"\n{'─'*50}")
    print(f"  🪙 {name}")
    print(f"  📡 拉K线...", end=" ", flush=True)
    candles = fetch_klines(sym, 24)
    print(f"{len(candles)}根")
    
    if len(candles) < 60:
        print(f"  ⚠️ 跳过")
        continue
    
    print(f"\n  {'TP':>6s}  {'SL':>6s}  {'交易':>5s}  {'胜率':>5s}  {'TP次数':>6s}  {'SL次数':>6s}  {'PnL':>8s}  {'RR':>5s}  {'评分':>6s}")
    print(f"  {'─'*65}")
    
    best_score = -999
    best_combo = None
    best_stats = None
    
    for tp_usd, sl_usd in FIXED_COMBOS:
        trades = backtest_fixed(candles, tp_usd, sl_usd, HOLD_MIN)
        s, stats = score(trades)
        
        if stats["trades"] < 10: continue
        
        icon = "📍" if s == max([score(backtest_fixed(candles, tp2, sl2, HOLD_MIN))[0] 
                                 for tp2, sl2 in FIXED_COMBOS 
                                 if score(backtest_fixed(candles, tp2, sl2, HOLD_MIN))[1].get("trades",0) >= 10]) else "  "
        icon = ""
        print(f"  {icon} ${tp_usd:<5.2f} ${sl_usd:<5.2f}  {stats['trades']:>4d}笔  "
              f"{stats['wr']:>4.0f}%  {stats['tp_count']:>5d}次  {stats['sl_count']:>5d}次  "
              f"${stats['total_pnl']:>+6.2f}  {stats['rr']:>4.1f}  {s:>5.1f}")
        
        if s > best_score:
            best_score = s
            best_combo = (tp_usd, sl_usd)
            best_stats = stats
    
    if best_combo:
        tp, sl = best_combo
        print(f"\n  ✅ 最优: TP ${tp:.2f}({tp/TRADE_USD*100:.0f}%) / SL ${sl:.2f}({sl/TRADE_USD*100:.0f}%)")
        print(f"     交易{best_stats['trades']}笔 | WR{best_stats['wr']:.0f}% | PnL ${best_stats['total_pnl']:+.2f}")
        all_best.append({
            "name": name, "tp": tp, "sl": sl,
            **best_stats,
        })
    
    time.sleep(0.3)

# 汇总
print(f"\n{'═'*60}")
print(f"  📋 固定金额最优参数汇总")
print(f"{'═'*60}\n")

total_pnl = sum(b["total_pnl"] for b in all_best)
print(f"  {'币':<8s} {'TP':>7s} {'SL':>7s} {'交易':>5s} {'胜率':>5s} {'PnL':>8s} {'RR':>5s}")
print(f"  {'─'*50}")
for b in sorted(all_best, key=lambda x: -x["total_pnl"]):
    print(f"  {b['name']:<8s} ${b['tp']:<6.2f} ${b['sl']:<6.2f} "
          f"{b['trades']:>4d}笔  {b['wr']:>4.0f}%  ${b['total_pnl']:>+6.2f}  {b['rr']:>4.1f}")

print(f"\n  💰 总盈亏: ${total_pnl:+.2f}")
print(f"\n  ⚙️ 实盘参数:")
print(f"     仓位: ${TRADE_USD}/笔")
print(f"     持仓: {HOLD_MIN}分钟")
print(f"     信号: 1m动量 > {MOM_THRESHOLD}% + 量能放大 {VOL_BOOST}x")
