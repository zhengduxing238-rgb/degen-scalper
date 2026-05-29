#!/usr/bin/env python3
"""
OKX 数据源 + Solana 生态全量回测
================================
数据: OKX REST K线 (不墙、稳定)
覆盖: SOL BONK WIF JTO JUP BOME JITOSOL OKSOL RESOLV (9个)
执行: 模拟币安 BAW (0.005% 手续费, 毫秒撮合)
"""
import requests, math, time
from datetime import datetime, timezone, timedelta

# ====================== 配置 ======================
TRADE_AMOUNT   = 5.0
STOP_LOSS_PCT  = -0.008       # -0.8%
PROFIT_TARGET  = 0.015        # +1.5%
MAX_HOLD       = 5            # 5 分钟
FEE_PCT        = 0.00005       # 币安 BAW
SIGNAL_MOMENTUM= 0.3           # 1m 动量 > 0.3%
MIN_VOLUME     = 200_000       # 24h 最低成交量

SYMBOLS_OKX = [
    "SOL-USDT","BONK-USDT","WIF-USDT","JTO-USDT",
    "JUP-USDT","BOME-USDT","JITOSOL-USDT","OKSOL-USDT","RESOLV-USDT",
]

# ====================== OKX K线 ======================
def fetch_okx_klines(inst_id, hours=24):
    """拉取 OKX 1m K线，自动分页"""
    all_klines = []
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(hours=hours)).timestamp() * 1000)
    
    after = None  # 首次不传 after，从最新开始
    while True:
        params = {"instId": inst_id, "bar": "1m", "limit": "300"}
        if after:
            params["after"] = after
        resp = requests.get("https://www.okx.com/api/v5/market/candles",
            params=params, timeout=15)
        js = resp.json()
        if js.get("code") != "0":
            break
        
        batch = js["data"]
        for k in batch:
            ts = int(k[0])
            if ts < start_ms:
                continue
            all_klines.append({
                "ts": ts,
                "open": float(k[1]), "high": float(k[2]),
                "low": float(k[3]), "close": float(k[4]),
                "vol": float(k[5]), "quote_vol": float(k[6]),
            })
        
        if len(batch) < 300:
            break
        after = batch[-1][0]
        time.sleep(0.15)
    
    # OKX 返回倒序，反转为正序
    return sorted(all_klines, key=lambda x: x["ts"])

# ====================== 真实K线持仓模拟 ======================
def simulate_hold(candles, entry_idx, entry_price):
    for offset in range(1, MAX_HOLD + 1):
        idx = entry_idx + offset
        if idx >= len(candles):
            break
        
        c = candles[idx]
        pnl_high = (c["high"] - entry_price) / entry_price
        pnl_low  = (c["low"]  - entry_price) / entry_price
        
        # 同根同时触发
        if pnl_high >= PROFIT_TARGET and pnl_low <= STOP_LOSS_PCT:
            open_pnl = (c["open"] - entry_price) / entry_price
            if abs(open_pnl - PROFIT_TARGET) < abs(open_pnl - STOP_LOSS_PCT):
                return ("✅止盈", (PROFIT_TARGET - FEE_PCT*2) * 100, offset)
            else:
                return ("🛑止损", (STOP_LOSS_PCT - FEE_PCT*2) * 100, offset)
        
        if pnl_high >= PROFIT_TARGET:
            return ("✅止盈", (PROFIT_TARGET - FEE_PCT*2) * 100, offset)
        if pnl_low <= STOP_LOSS_PCT:
            return ("🛑止损", (STOP_LOSS_PCT - FEE_PCT*2) * 100, offset)
    
    last_idx = min(entry_idx + MAX_HOLD, len(candles) - 1)
    final_pnl = (candles[last_idx]["close"] - entry_price) / entry_price
    return ("⏰超时", (final_pnl - FEE_PCT*2) * 100, MAX_HOLD)

# ====================== 报告 ======================
def print_report(trades, hours):
    wins   = [t for t in trades if t["net_usd"] > 0]
    losses = [t for t in trades if t["net_usd"] <= 0]
    total  = len(trades)
    total_pnl = sum(t["net_usd"] for t in trades)
    wr = len(wins)/total*100 if total else 0
    avg_w = sum(t["net_usd"] for t in wins)/max(len(wins),1)
    avg_l = sum(abs(t["net_usd"]) for t in losses)/max(len(losses),1)
    pf = sum(t["net_usd"] for t in wins)/max(sum(abs(t["net_usd"]) for t in losses), 0.01)
    
    equity = [40.0]
    for t in trades:
        equity.append(equity[-1] + t["net_usd"])
    peak, max_dd = 40.0, 0
    for e in equity:
        peak = max(peak, e)
        max_dd = max(max_dd, peak - e)
    
    rets = [t["net_usd"] for t in trades]
    avg_r = total_pnl/max(total,1)
    std_r = (sum((r-avg_r)**2 for r in rets)/max(total,1))**0.5
    sharpe = avg_r/max(std_r,0.001) * math.sqrt(len(trades))
    
    # 止盈/止损触发情况
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    tp_count = reasons.get("✅止盈", 0)
    sl_count = reasons.get("🛑止损", 0)
    to_count = reasons.get("⏰超时", 0)
    
    print(f"\n{'═'*60}")
    print(f"  📊 OKX 数据源回测报告")
    print(f"  🕐 回溯 {hours} 小时 | 覆盖 9 个 Solana 生态代币")
    print(f"{'═'*60}\n")
    print(f"  ┌──────────────────────────────────────┐")
    print(f"  │ 总交易:  {total:>3} 笔                        │")
    print(f"  │ 止盈:    {tp_count:>3} | 止损: {sl_count:<3} | 超时: {to_count:<3}      │")
    print(f"  │ 胜率:    {wr:.0f}%                         │")
    print(f"  │ 净盈亏:  {total_pnl:+.2f}$                      │")
    print(f"  │ 均盈利:  {avg_w:+.3f}$                      │")
    print(f"  │ 均亏损:  {avg_l:.3f}$                      │")
    print(f"  │ 盈亏比:  {avg_w/max(avg_l,0.001):.1f}:1                       │")
    print(f"  │ 盈利因子:{pf:.2f}                        │")
    print(f"  │ 最大回撤:{max_dd:.2f}$                       │")
    print(f"  │ 夏普比率:{sharpe:+.2f}                        │")
    print(f"  └──────────────────────────────────────┘")
    print(f"\n  💰 $40 → ${40+total_pnl:.2f} ({(total_pnl/40*100):+.1f}%)")
    
    # 各币详细
    by_token = {}
    for t in trades:
        tok = t["token"]
        if tok not in by_token:
            by_token[tok] = {"cnt":0, "pnl":0, "w":0}
        by_token[tok]["cnt"] += 1
        by_token[tok]["pnl"] += t["net_usd"]
        if t["net_usd"] > 0: by_token[tok]["w"] += 1
    
    print(f"\n  🪙 各币详情 (按盈亏排序):")
    print(f"  {'代币':<10s} {'交易':>4s} {'胜率':>5s} {'PnL':>8s} {'均/笔':>8s}")
    print(f"  {'─'*40}")
    total_sig = 0
    for tok in sorted(by_token, key=lambda x: -by_token[x]["pnl"]):
        d = by_token[tok]
        wr_t = d['w']/d['cnt']*100 if d['cnt'] else 0
        print(f"  {tok:<10s} {d['cnt']:>3d}笔  {wr_t:>4.0f}%  {d['pnl']:>+7.2f}$  {d['pnl']/d['cnt']:>+7.3f}$")
        total_sig += d['cnt']

# ====================== 主程序 ======================
def run(hours=24):
    print("═" * 60)
    print("  🔗 OKX 数据源 → Solana 动量剥头皮回测")
    print(f"  ⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("═" * 60)
    print(f"\n  数据源: OKX REST API (https://www.okx.com)")
    print(f"  执行: 币安 BAW 模拟 (手续费 {FEE_PCT*100:.3f}%)")
    print(f"  参数: ${TRADE_AMOUNT}/笔 | TP +{int(PROFIT_TARGET*100)}% | SL {abs(STOP_LOSS_PCT)*100:.0f}%")
    print(f"  持仓: 最多 {MAX_HOLD} 分钟 | 信号: 1m动量 > +{SIGNAL_MOMENTUM}%\n")
    
    all_trades = []
    
    for inst_id in SYMBOLS_OKX:
        name = inst_id.replace("-USDT","")
        print(f"  🔽 {name:8s} 拉取K线...", end=" ", flush=True)
        candles = fetch_okx_klines(inst_id, hours=hours)
        print(f"{len(candles):>4d}根 ({len(candles)/60:.1f}h)", end="", flush=True)
        
        if len(candles) < 60:
            print(" → ⚠️ 数据不足，跳过")
            continue
        
        signals = 0
        for i in range(2, len(candles) - MAX_HOLD):
            c = candles[i]
            prev = candles[i-1]
            momentum = (c["close"] - prev["close"]) / prev["close"] * 100
            
            if momentum <= SIGNAL_MOMENTUM:
                continue
            
            # 24h 涨幅 + 量过滤
            lookback = min(i, 1440)
            if lookback < 60:
                continue
            change_24h = (c["close"] - candles[i-lookback]["close"]) / candles[i-lookback]["close"] * 100
            recent_vol = sum(k["quote_vol"] for k in candles[max(0,i-1400):i+1])
            if recent_vol < MIN_VOLUME:
                continue
            
            signals += 1
            
            reason, pnl_pct, hold_mins = simulate_hold(candles, i, c["close"])
            net_usd = TRADE_AMOUNT * pnl_pct / 100
            
            all_trades.append({
                "token": name, "entry": c["close"],
                "reason": reason, "pnl_pct": pnl_pct,
                "net_usd": net_usd, "hold": hold_mins,
                "momentum": momentum, "change_24h": change_24h,
            })
            i += hold_mins
        
        print(f" → 信号{signals}次")
        time.sleep(0.2)
    
    if all_trades:
        print_report(all_trades, hours)
    else:
        print("\n  ⚠️ 无交易信号 — 市场太冷清")
    
    return all_trades

if __name__ == "__main__":
    import sys
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    run(hours)
