#!/usr/bin/env python3
"""
🦴 Degen Scalper Pro — 自适应杠杆合约土狗策略
==============================================
策略规则:
  1. 每笔开仓前必须分析盘面 → 确定方向
  2. 自适应杠杆: 10-50x (波动越大杠杆越低)
  3. 固定止盈 $1.00 / 止损 $0.50
  4. 同时最多 1 笔持仓
  5. 严格按 TP/SL 执行

信号流程:
  Step 1: 拉取盘面数据 (24h趋势/资金费率/波动率)
  Step 2: 检测1m动量信号 (>0.2%做多 / <-0.2%做空)
  Step 3: 量能确认 (当前量 > 前5根均量×1.5)
  Step 4: 自适应杠杆 (基于振幅反推)
  Step 5: 输出交易信号
"""
import requests, time, json, math
from datetime import datetime, timezone, timedelta
from collections import Counter

# ═══════════ 配置 ═══════════
MARGIN_USD    = 5.0       # 保证金
TP_USD        = 1.00      # 固定止盈
SL_USD        = 0.50      # 固定止损
MAX_LEVERAGE  = 50        # 最大杠杆
MIN_LEVERAGE  = 10        # 最小杠杆
HOLD_MIN      = 15        # 持仓时间
MOM_UP        = 0.2       # 做多动量阈值(%)
MOM_DOWN      = -0.2      # 做空动量阈值(%)
VOL_BOOST     = 1.5       # 量能放大倍数
MAX_SL_RATIO  = 0.5    # SL不能超过振幅中位数的50% (放宽)

WATCHLIST = [
    # (名称, 合约symbol, 固定杠杆)
    ("ALLO",   "ALLOUSDT",   10),   # 振幅0.95% → 10x
    ("SKYAI",  "SKYAIUSDT",  20),   # 振幅0.38% → 20x
    ("MOODENG","MOODENGUSDT",35),   # 振幅0.10% → 35x
    ("GRASS",  "GRASSUSDT",  30),   # 振幅0.28% → 30x
    ("SPX",    "SPXUSDT",    40),   # 振幅0.12% → 40x
    ("MYX",    "MYXUSDT",    30),   # 振幅0.20% → 30x
]

FAPI = "https://fapi.binance.com/fapi/v1"

# ═══════════ 数据获取 ═══════════
def fetch_klines(symbol, hours=6):
    """拉取合约1m K线"""
    r = []
    end = int(datetime.now(timezone.utc).timestamp()*1000)
    start = int((datetime.now(timezone.utc)-timedelta(hours=hours)).timestamp()*1000)
    for _ in range(3):
        resp = requests.get(f"{FAPI}/klines", params={
            "symbol":symbol,"interval":"1m",
            "startTime":start,"endTime":end,"limit":1500
        }, timeout=15)
        batch = resp.json()
        if not isinstance(batch,list): break
        for k in batch:
            r.append({"ts":int(k[0]),"open":float(k[1]),"high":float(k[2]),
                       "low":float(k[3]),"close":float(k[4]),
                       "vol":float(k[5]),"qv":float(k[7]),"trades":int(k[8])})
        if len(batch)<1500: break
        start=int(batch[-1][0])+1; time.sleep(0.15)
    return sorted(r,key=lambda x:x["ts"])

def fetch_market_data(symbol):
    """拉取盘面数据"""
    try:
        # 24hr ticker
        t = requests.get(f"{FAPI}/ticker/24hr", params={"symbol":symbol}, timeout=10).json()
        # 资金费率
        f = requests.get(f"{FAPI}/premiumIndex", params={"symbol":symbol}, timeout=10).json()
        return {
            "price": float(t.get("lastPrice",0) or 0),
            "change_24h": float(t.get("priceChangePercent",0) or 0),
            "high_24h": float(t.get("highPrice",0) or 0),
            "low_24h": float(t.get("lowPrice",0) or 0),
            "volume_24h": float(t.get("quoteVolume",0) or 0),
            "trades_24h": int(t.get("count",0) or 0),
            "funding_rate": float(f.get("lastFundingRate",0) or 0) * 100,
        }
    except:
        return None

# ═══════════ 盘面分析 ═══════════
def analyze_market(candles, mkt):
    """
    分析盘面 → 输出: 方向, 杠杆, 置信度
    """
    if len(candles) < 60 or not mkt:
        return None
    
    # 计算波动率
    amps = sorted((k["high"]-k["low"])/k["open"]*100 for k in candles[-60:])
    amp_p50 = amps[len(amps)//2]    # 中位数振幅
    amp_p90 = amps[int(len(amps)*0.9)]  # 90分位振幅
    
    # 计算自适应杠杆
    # 规则: SL不能超过振幅中位数的30%，否则杠杆太高容易被震出去
    max_sl_pct = amp_p50 * MAX_SL_RATIO  # SL可用百分比空间
    if max_sl_pct < 0.05: max_sl_pct = 0.05  # 最小0.05%
    
    # SL $0.50 / 名义仓位 = SL百分比 → 名义仓位 = $0.50 / SL百分比
    nominal = SL_USD / (max_sl_pct / 100)
    leverage = min(MAX_LEVERAGE, max(MIN_LEVERAGE, int(nominal / MARGIN_USD)))
    nominal = MARGIN_USD * leverage
    
    tp_pct = TP_USD / nominal * 100
    sl_pct = SL_USD / nominal * 100
    
    # 当前动量
    latest = candles[-1]
    prev = candles[-2]
    momentum = (latest["close"] - prev["close"]) / prev["close"] * 100
    
    # 短期趋势 (最近5分钟)
    if len(candles) >= 5:
        trend_5m = (candles[-1]["close"] - candles[-5]["close"]) / candles[-5]["close"] * 100
    else:
        trend_5m = 0
    
    # 量能检测
    avg_vol_5 = sum(k["qv"] for k in candles[-6:-1]) / 5
    vol_spike = latest["qv"] / avg_vol_5 if avg_vol_5 > 0 else 1
    
    # 方向判断
    direction = None
    confidence = 0
    
    if momentum > MOM_UP and vol_spike > VOL_BOOST:
        # 做多信号
        direction = "LONG"
        confidence += 2
        if trend_5m > 0: confidence += 1
        if mkt["change_24h"] > 0: confidence += 0.5
        if mkt["funding_rate"] > -0.01: confidence += 0.5  # 资金费率不太负
    elif momentum < MOM_DOWN and vol_spike > VOL_BOOST:
        # 做空信号
        direction = "SHORT"
        confidence += 2
        if trend_5m < 0: confidence += 1
        if mkt["change_24h"] < 0: confidence += 0.5
        if mkt["funding_rate"] < 0.05: confidence += 0.5  # 资金费率不太高
    
    if direction is None:
        return None
    
    return {
        "direction": direction,
        "leverage": leverage,
        "nominal": nominal,
        "confidence": min(5, confidence),
        "tp_pct": round(tp_pct, 2),
        "sl_pct": round(sl_pct, 2),
        "momentum": round(momentum, 3),
        "trend_5m": round(trend_5m, 3),
        "vol_spike": round(vol_spike, 1),
        "amp_p50": round(amp_p50, 3),
        "amp_p90": round(amp_p90, 3),
        "price": mkt["price"],
        "change_24h": mkt["change_24h"],
        "funding_rate": round(mkt["funding_rate"], 4),
        "volume_24h": mkt["volume_24h"],
    }

# ═══════════ 回测 ═══════════
def backtest_fixed_lev(candles, leverage):
    """固定杠杆回测"""
    trades = []
    
    for i in range(60, len(candles) - HOLD_MIN):  # 需要60根做分析
        # 模拟盘面分析
        window = candles[:i+1]
        c = candles[i]; prev = candles[i-1]
        mom = (c["close"] - prev["close"]) / prev["close"] * 100
        
        # 量能
        avg_vol = sum(k["qv"] for k in candles[i-5:i]) / 5
        vol_ok = c["qv"] > avg_vol * VOL_BOOST
        
        # 方向
        direction = None
        if mom > MOM_UP and vol_ok:
            direction = "LONG"
        elif mom < MOM_DOWN and vol_ok:
            direction = "SHORT"
        else:
            continue
        
        nominal = MARGIN_USD * leverage
        
        entry = c["close"]
        qty = nominal / entry
        tp_price = entry + TP_USD/qty if direction=="LONG" else entry - TP_USD/qty
        sl_price = entry - SL_USD/qty if direction=="LONG" else entry + SL_USD/qty
        
        for offset in range(1, HOLD_MIN+1):
            idx = i+offset
            if idx >= len(candles): break
            k = candles[idx]
            
            if direction == "LONG":
                hit_tp = k["high"] >= tp_price
                hit_sl = k["low"]  <= sl_price
            else:
                hit_tp = k["low"]  <= tp_price
                hit_sl = k["high"] >= sl_price
            
            if hit_tp and hit_sl:
                if abs(k["open"]-tp_price) < abs(k["open"]-sl_price):
                    trades.append({"d":direction,"r":"TP","p":TP_USD,"lev":leverage,"h":offset}); break
                else:
                    trades.append({"d":direction,"r":"SL","p":-SL_USD,"lev":leverage,"h":offset}); break
            elif hit_tp:
                trades.append({"d":direction,"r":"TP","p":TP_USD,"lev":leverage,"h":offset}); break
            elif hit_sl:
                trades.append({"d":direction,"r":"SL","p":-SL_USD,"lev":leverage,"h":offset}); break
        else:
            last = candles[min(i+HOLD_MIN,len(candles)-1)]
            pnl = (last["close"]-entry)*qty if direction=="LONG" else (entry-last["close"])*qty
            trades.append({"d":direction,"r":"TO","p":pnl,"lev":leverage,"h":HOLD_MIN})
        i += 1
    
    return trades

# ═══════════ 实时信号扫描 ═══════════
def live_scan():
    """实时扫描 → 输出交易信号"""
    print("═"*60)
    print("  🦴 Degen Scalper Pro — 实时盘面扫描")
    print(f"  ⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("═"*60)
    
    signals = []
    
    for name, sym, base_lev in WATCHLIST:
        print(f"\n  🔍 {name} ...", end=" ", flush=True)
        
        # 拉数据
        candles = fetch_klines(sym, 1)  # 1小时够分析
        mkt = fetch_market_data(sym)
        
        if not candles or not mkt:
            print("数据获取失败")
            continue
        
        # 分析
        result = analyze_market(candles, mkt)
        
        if result:
            icon = "🟢" if result["direction"]=="LONG" else "🔴"
            stars = "⭐" * int(result["confidence"])
            print(f"{icon} {result['direction']} {stars}")
            print(f"     价格:\${result['price']:.6f} | {result['change_24h']:+.1f}%")
            print(f"     杠杆:{result['leverage']}x | 名义\${result['nominal']:.0f}")
            print(f"     TP:{result['tp_pct']}% SL:{result['sl_pct']}% | "
                  f"动量:{result['momentum']:+.3f}% | 放量:{result['vol_spike']}x")
            print(f"     振幅P50:{result['amp_p50']}% | 资金费率:{result['funding_rate']:.4f}%")
            result["name"] = name
            result["symbol"] = sym
            signals.append(result)
        else:
            print("无信号")
        
        time.sleep(0.3)
    
    if signals:
        # 按置信度排序，只推荐最强的一个
        signals.sort(key=lambda x: -x["confidence"])
        best = signals[0]
        print(f"\n{'═'*60}")
        print(f"  🎯 推荐交易: {best['name']} {best['direction']} {best['leverage']}x")
        print(f"     置信度: {best['confidence']}/5 | TP\${TP_USD} SL\${SL_USD}")
        print(f"{'═'*60}")
    
    return signals

# ═══════════ 主程序 ═══════════
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "backtest":
        # 回测模式
        print("═"*60)
        print("  📊 自适应杠杆回测 (24h)")
        print(f"  ⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
        print(f"  杠杆: {MIN_LEVERAGE}-{MAX_LEVERAGE}x | 保证金${MARGIN_USD}")
        print(f"  TP${TP_USD} SL${SL_USD} | 持仓{HOLD_MIN}min")
        print("═"*60)
        
        total_pnl = 0
        all_results = []
        
        for name, sym, base_lev in WATCHLIST:
            print(f"\n🪙 {name}")
            candles = fetch_klines(sym, 24)
            print(f"  K线:{len(candles)}根")
            if len(candles) < 60: continue
            
            trades = backtest_fixed_lev(candles, base_lev)
            if not trades: print("  无交易"); continue
            
            wins   = [t for t in trades if t["p"]>0]
            tw     = len(trades)
            tp_s   = sum(t["p"] for t in trades)
            wr     = len(wins)/tw*100
            rs     = Counter(t["r"] for t in trades)
            levs   = Counter(t["lev"] for t in trades)
            most_lev = levs.most_common(1)[0][0] if levs else 0
            long_t = sum(1 for t in trades if t["d"]=="LONG")
            short_t= sum(1 for t in trades if t["d"]=="SHORT")
            aw = sum(t["p"] for t in wins)/max(len(wins),1)
            al = sum(abs(t["p"]) for t in trades if t["p"]<=0)/max(tw-len(wins),1)
            
            icon = "🟢" if tp_s>3 else "🟡" if tp_s>0 else "🔴"
            print(f"  {icon} {tw}笔 多{long_t}/空{short_t} WR{wr:.0f}%")
            print(f"     TP:{rs.get('TP',0)} SL:{rs.get('SL',0)} TO:{rs.get('TO',0)} "
                  f"杠杆:{most_lev}x PnL\${tp_s:+.2f} RR{aw/max(al,0.001):.1f}")
            
            total_pnl += tp_s
            all_results.append({"name":name,"trades":tw,"pnl":tp_s,"wr":wr,"lev":most_lev})
        
        print(f"\n{'═'*60}")
        print(f"  💰 总盈亏: \${total_pnl:+.2f}")
        for r in sorted(all_results, key=lambda x:-x["pnl"]):
            print(f"  {r['name']:8s} {r['trades']:>3d}笔 {r['lev']}x PnL\${r['pnl']:+.2f}")
    
    else:
        # 实时扫描模式
        live_scan()
