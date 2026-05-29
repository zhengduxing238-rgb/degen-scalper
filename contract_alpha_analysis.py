#!/usr/bin/env python3
"""
🦴 合约版 Alpha 土狗 — 全市场盘面分析 + 策略回测
==============================================
Step 1: 盘面数据 (24h成交量/波动/资金费率/持仓量)
Step 2: 1m K线波动率分析
Step 3: 多空双向回测
Step 4: 输出策略参数
"""
import requests, time, math, json
from datetime import datetime, timezone, timedelta
from collections import Counter

# ═══════════ 15只Alpha合约币 ═══════════
ALPHA_CONTRACTS = [
    "ALLOUSDT","SKYAIUSDT","MOODENGUSDT","FARTCOINUSDT",
    "JELLYJELLYUSDT","POPCATUSDT","GOATUSDT","SPXUSDT",
    "MEWUSDT","GRASSUSDT","ALCHUSDT","DOODUSDT",
    "MERLUSDT","MYXUSDT","VELODROMEUSDT",
]

TRADE_USD = 5.0
HOLD_MIN  = 15
LEVERAGE  = 1  # 1x 起步

FAPI = "https://fapi.binance.com/fapi/v1"
API  = "https://api.binance.com/api/v3"

def fetch_contract_stats():
    """拉取所有合约盘面数据"""
    print("  📡 拉取合约盘面...")
    resp = requests.get(f"{FAPI}/ticker/24hr", timeout=15)
    all_tickers = {t["symbol"]: t for t in resp.json()}
    
    # 资金费率
    resp2 = requests.get(f"{FAPI}/premiumIndex", timeout=15)
    funding = {p["symbol"]: p for p in resp2.json()}
    
    # 未平仓合约 (需要单独查询，跳过)
    oi = {}
    
    stats = []
    for sym in ALPHA_CONTRACTS:
        t = all_tickers.get(sym, {})
        f = funding.get(sym, {})
        o = oi.get(sym, {})
        
        price = float(t.get("lastPrice", 0) or 0)
        if price == 0: continue
        
        stats.append({
            "symbol": sym,
            "name": sym.replace("USDT",""),
            "price": price,
            "change_24h": float(t.get("priceChangePercent", 0) or 0),
            "high_24h": float(t.get("highPrice", 0) or 0),
            "low_24h": float(t.get("lowPrice", 0) or 0),
            "volume_24h": float(t.get("quoteVolume", 0) or 0),
            "trades_24h": int(t.get("count", 0) or 0),
            "funding_rate": float(f.get("lastFundingRate", 0) or 0) * 100,
            "open_interest": float(o.get("openInterest", 0) or 0),
        })
    
    return stats

def fetch_contract_klines(symbol, hours=24):
    """拉取合约 1m K线"""
    r = []
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)
    while True:
        resp = requests.get(f"{FAPI}/klines", params={
            "symbol": symbol, "interval": "1m",
            "startTime": start_ms, "endTime": end_ms, "limit": 1500,
        }, timeout=15)
        batch = resp.json()
        if not isinstance(batch, list): break
        for k in batch:
            r.append({"ts":int(k[0]),"open":float(k[1]),"high":float(k[2]),
                       "low":float(k[3]),"close":float(k[4]),"vol":float(k[5]),
                       "qv":float(k[7]),"trades":int(k[8])})
        if len(batch) < 1500: break
        start_ms = int(batch[-1][0]) + 1
        time.sleep(0.15)
    return sorted(r, key=lambda x: x["ts"])

def vol_analysis(candles):
    """波动率分析"""
    if len(candles) < 60: return {}
    amps = sorted((k["high"]-k["low"])/k["open"]*100 for k in candles)
    # 5m / 10m / 15m 涨跌幅
    rets = {}
    for period, name in [(5,"5m"),(10,"10m"),(15,"15m")]:
        r = []
        for i in range(period, len(candles)):
            r.append((candles[i]["close"]-candles[i-period]["close"])/candles[i-period]["close"]*100)
        r.sort()
        rets[name] = {"p50": r[len(r)//2], "p75": r[int(len(r)*.75)], "p90": r[int(len(r)*.9)]}
    
    return {
        "amp": {"p50": amps[len(amps)//2], "p75": amps[int(len(amps)*.75)], "p90": amps[int(len(amps)*.9)]},
        "rets": rets,
        "count": len(candles),
    }

def backtest_dual(candles, tp_pct, sl_pct, mom_threshold=0.15):
    """
    多空双向回测:
    - 做多: 1m动量 > +mom_threshold + 放量
    - 做空: 1m动量 < -mom_threshold + 放量
    - 出场: TP/SL/超时
    """
    trades = []
    
    for i in range(6, len(candles) - HOLD_MIN):
        c = candles[i]
        prev = candles[i-1]
        mom = (c["close"] - prev["close"]) / prev["close"] * 100
        
        avg_vol = sum(k["qv"] for k in candles[i-5:i]) / 5
        vol_ok = c["qv"] > avg_vol * 1.2
        
        direction = None
        if mom > mom_threshold and vol_ok:
            direction = "LONG"
        elif mom < -mom_threshold and vol_ok:
            direction = "SHORT"
        else:
            continue
        
        entry = c["close"]
        
        for offset in range(1, HOLD_MIN + 1):
            idx = i + offset
            if idx >= len(candles): break
            k = candles[idx]
            
            if direction == "LONG":
                pnl_high = (k["high"] - entry) / entry * 100
                pnl_low  = (k["low"]  - entry) / entry * 100
            else:  # SHORT
                pnl_high = (entry - k["low"])  / entry * 100
                pnl_low  = (entry - k["high"]) / entry * 100
            
            if pnl_high >= tp_pct and pnl_low <= -sl_pct:
                open_pnl = (entry - k["open"]) / entry * 100 if direction == "SHORT" else (k["open"] - entry) / entry * 100
                if abs(open_pnl) > 0:
                    r = "TP" if abs(open_pnl - tp_pct/100) < abs(open_pnl + sl_pct/100) else "SL"
                else:
                    r = "TP"
                trades.append({"d": direction, "r": r, "pnl": tp_pct/100 if r=="TP" else -sl_pct/100, "h": offset})
                break
            elif pnl_high >= tp_pct:
                trades.append({"d": direction, "r": "TP", "pnl": tp_pct/100, "h": offset})
                break
            elif pnl_low <= -sl_pct:
                trades.append({"d": direction, "r": "SL", "pnl": -sl_pct/100, "h": offset})
                break
        else:
            last = candles[min(i + HOLD_MIN, len(candles)-1)]
            if direction == "SHORT":
                pnl = (entry - last["close"]) / entry
            else:
                pnl = (last["close"] - entry) / entry
            trades.append({"d": direction, "r": "TO", "pnl": pnl, "h": HOLD_MIN})
        i += 1
    
    return trades

# ═══════════ 主程序 ═══════════
print("═" * 60)
print("  📊 合约版 Alpha 土狗 — 全市场盘面分析")
print(f"  ⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
print("═" * 60)

# Step 1: 盘面
print(f"\n{'─'*55}")
print(f"  📋 Step 1: 盘面数据")
stats = fetch_contract_stats()
stats.sort(key=lambda x: -(x["volume_24h"] + abs(x["change_24h"]) * 10000))

print(f"\n  {'币':<12s} {'价格':>10s} {'24h涨':>8s} {'24h量':>12s} {'振幅':>8s} {'资金费':>8s} {'持仓':>12s} {'笔数':>6s}")
print(f"  {'─'*80}")
for s in stats:
    amp = (s["high_24h"] - s["low_24h"]) / s["low_24h"] * 100 if s["low_24h"] > 0 else 0
    print(f"  {s['name']:<12s} ${s['price']:<9.6f} {s['change_24h']:>+7.1f}% "
          f"\${s['volume_24h']:>10,.0f} {amp:>7.1f}% {s['funding_rate']:>+7.3f}% "
          f"\${s['open_interest']:>10,.0f} {s['trades_24h']:>5d}")

# Step 2: 选 TOP8 拉K线分析
print(f"\n{'─'*55}")
print(f"  📈 Step 2: 1m K线波动率分析 (TOP8)")

# 综合评分：成交量 + 波动率
for s in stats:
    s["_score"] = s["volume_24h"] * (1 + abs(s["change_24h"])/100)
stats.sort(key=lambda x: -x["_score"])
top8 = stats[:8]

vol_results = []
for s in top8:
    name = s["name"]
    print(f"\n  🔽 {name}...", end=" ", flush=True)
    candles = fetch_contract_klines(s["symbol"], 24)
    print(f"{len(candles)}根", end="", flush=True)
    
    va = vol_analysis(candles)
    if va:
        a = va["amp"]
        print(f"  振幅P50:{a['p50']:.2f}% P90:{a['p90']:.2f}%")
        print(f"    5m涨跌 P75:{va['rets']['5m']['p75']:.2f}% P90:{va['rets']['5m']['p90']:.2f}%")
        print(f"    15m涨跌 P75:{va['rets']['15m']['p75']:.2f}% P90:{va['rets']['15m']['p90']:.2f}%")
        vol_results.append({"name": name, "symbol": s["symbol"], "va": va, "stats": s})
    else:
        print("  数据不足")
    time.sleep(0.2)

# Step 3: 双向回测
print(f"\n{'─'*55}")
print(f"  🧪 Step 3: 多空双向回测")

best_all = []

for vr in vol_results:
    name = vr["name"]
    va = vr["va"]
    candles = fetch_contract_klines(vr["symbol"], 24)
    
    # 基于波动率生成参数
    tp_options = [round(va["rets"]["15m"]["p90"], 1), round(va["rets"]["15m"]["p75"] * 1.5, 1)]
    sl_options = [round(va["amp"]["p50"], 1), round(va["amp"]["p75"] * 0.7, 1)]
    tp_options = sorted(set(max(0.3, min(8, x)) for x in tp_options if x > 0.1))
    sl_options = sorted(set(max(0.1, min(4, x)) for x in sl_options if x > 0.05))
    if not tp_options: tp_options = [1.0, 2.0, 3.0]
    if not sl_options: sl_options = [0.5, 1.0, 1.5]
    
    print(f"\n  {name} | 测试 TP{tp_options} / SL{sl_options}")
    
    best_pnl = -999
    best_combo = None
    
    for tp in tp_options:
        for sl in sl_options:
            if sl >= tp: continue
            trades = backtest_dual(candles, tp, sl)
            if not trades: continue
            
            long_t  = [t for t in trades if t["d"] == "LONG"]
            short_t = [t for t in trades if t["d"] == "SHORT"]
            wins    = [t for t in trades if t["pnl"] > 0]
            total_pnl = sum(t["pnl"] * TRADE_USD for t in trades)
            wr = len(wins)/len(trades)*100
            
            tp_count = sum(1 for t in trades if t["r"] == "TP")
            sl_count = sum(1 for t in trades if t["r"] == "SL")
            avg_w = sum(t["pnl"] for t in wins)/max(len(wins), 1) * TRADE_USD
            avg_l = sum(abs(t["pnl"]) for t in trades if t["pnl"] <= 0)/max(len(trades)-len(wins), 1) * TRADE_USD
            rr = avg_w/max(avg_l, 0.001)
            
            if total_pnl > best_pnl:
                best_pnl = total_pnl
                best_combo = {
                    "tp": tp, "sl": sl, "trades": len(trades),
                    "long": len(long_t), "short": len(short_t),
                    "wr": wr, "pnl": total_pnl,
                    "tp_c": tp_count, "sl_c": sl_count,
                    "rr": rr,
                }
    
    if best_combo:
        bc = best_combo
        icon = "🟢" if bc["pnl"] > 2 else "🟡" if bc["pnl"] > 0 else "🔴"
        print(f"  {icon} TP+{bc['tp']}% / SL-{bc['sl']}% → "
              f"{bc['trades']}笔(多{bc['long']}/空{bc['short']}) WR{bc['wr']:.0f}% "
              f"PnL\${bc['pnl']:+.2f} RR{bc['rr']:.1f}")
        best_all.append({"name": name, **bc})

# Step 4: 汇总
print(f"\n{'═'*60}")
print(f"  📋 合约策略参数汇总")
print(f"{'═'*60}\n")

total_pnl = sum(b["pnl"] for b in best_all)
print(f"  {'币':<12s} {'TP':>6s} {'SL':>6s} {'交易':>5s} {'多':>4s} {'空':>4s} {'胜率':>5s} {'PnL':>8s} {'RR':>5s}")
print(f"  {'─'*65}")
for b in sorted(best_all, key=lambda x: -x["pnl"]):
    print(f"  {b['name']:<12s} +{b['tp']}%   -{b['sl']}%   "
          f"{b['trades']:>4d}笔 {b['long']:>3d} {b['short']:>3d} "
          f"{b['wr']:>4.0f}%  \${b['pnl']:>+6.2f}  {b['rr']:>4.1f}")

print(f"\n  💰 总盈亏: \${total_pnl:+.2f}")
print(f"  ⚙️ 杠杆: {LEVERAGE}x | 仓位: \${TRADE_USD}/笔 | 持仓: {HOLD_MIN}min")
print(f"  📡 信号: 1m动量 >0.15%做多 / <-0.15%做空 + 放量1.2x")
print(f"  🛡️ 风控: 同时最多2笔 | 多空可并存")

# 输出JSON供GitHub
with open("/home/admin/contract_strategy.json", "w") as f:
    json.dump({
        "time": datetime.now(timezone.utc).isoformat(),
        "leverage": LEVERAGE,
        "position_size": TRADE_USD,
        "hold_min": HOLD_MIN,
        "coins": best_all,
        "total_pnl": total_pnl,
    }, f, indent=2, ensure_ascii=False)
print(f"\n  📄 策略参数已保存: contract_strategy.json")
