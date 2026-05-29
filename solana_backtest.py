#!/usr/bin/env python3
"""
Solana 链上环境回测 🔗
========================
数据源: DexScreener (代币发现) + 币安 K线 (价格, 已上线币) 
        + DexScreener 价格对 (新币/土狗)
执行模拟: Solana 链上 Jupiter swap (Gas 0.000005 SOL, 滑点 0.5%)
手续费: 链上 Jupiter 路由费 ~0.1% (非币安的 0.005%)
"""
import requests, math, time, subprocess, json
from datetime import datetime, timezone, timedelta

# ====================== 配置 ======================
TRADE_AMOUNT_USD  = 5.0         # $5/笔
STOP_LOSS_PCT     = -0.05       # -5%
PROFIT_TARGET     = 0.10        # +10%
MAX_HOLD          = 15          # 分钟
GAS_SOL           = 0.000005    # Solana Gas
JUPITER_FEE_BPS   = 10          # Jupiter 路由费 0.1%
SLIPPAGE_BPS      = 50          # 滑点 0.5%
SOL_PRICE         = 82.0        # 默认 SOL 价格

SYMBOLS_CEX = ["BONKUSDT","WIFUSDT","JTOUSDT","JUPUSDT","BOMEUSDT"]
TOKEN_MINTS = {
    "BONK": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
    "WIF":  "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
    "JTO":  "jtojtomepa8beP8AuQc6eXt5FriJwfFMwQx2v2f9mCL",
    "JUP":  "JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN",
    "BOME": "ukHH6c7mMyiWCf1b9pnWe25TSpkDDt3H5pQZgZ74J82",
}

# ====================== 钱包 ======================
def solana_wallet_info():
    """Solana CLI 获取钱包信息"""
    try:
        bal = subprocess.run(["solana","balance","--url","mainnet-beta"],
            capture_output=True, text=True, timeout=10)
        pubkey = subprocess.run(["solana","address","--url","mainnet-beta"],
            capture_output=True, text=True, timeout=10)
        return {
            "balance": bal.stdout.strip(),
            "pubkey": pubkey.stdout.strip()[:12] + "...",
            "rpc": "https://api.mainnet-beta.solana.com",
        }
    except:
        return {"balance": "N/A", "pubkey": "N/A", "rpc": "N/A"}

# ====================== DexScreener ======================
def dexscreener_trending(limit=20):
    """获取 Solana 链上热门代币"""
    try:
        resp = requests.get("https://api.dexscreener.com/token-profiles/latest/v1", timeout=15)
        tokens = [t for t in resp.json() if t.get("chainId") == "solana"]
        return tokens[:limit]
    except:
        return []

def dexscreener_token(address):
    """获取单个代币的 DexScreener 数据"""
    try:
        resp = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{address}", timeout=10)
        pairs = resp.json().get("pairs", [])
        if pairs:
            p = pairs[0]
            return {
                "price": float(p.get("priceUsd",0)),
                "change_24h": float(p.get("priceChange",{}).get("h24",0)),
                "volume_24h": float(p.get("volume",{}).get("h24",0)),
                "fdv": float(p.get("fdv",0)),
                "dex": p.get("dexId","?"),
                "liquidity": float(p.get("liquidity",{}).get("usd",0)),
            }
    except:
        pass
    return None

# ====================== 币安 K线 (价格数据源) ======================
def fetch_binance_klines(symbol, hours=24):
    """获取币安 1m K线"""
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
        time.sleep(0.2)
    return sorted(all_klines, key=lambda x: x["ts"])

# ====================== 链上回测 (真实K线) ======================
def simulate_solana_hold(candles, entry_idx, entry_price, sol_price):
    """模拟 Solana 链上持仓 - 含 Gas + Jupiter 路由费 + 滑点"""
    total_fee_pct = (JUPITER_FEE_BPS + SLIPPAGE_BPS) / 10000  # ~0.6%
    
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
                return ("✅止盈", PROFIT_TARGET * 100 - total_fee_pct*100, offset)
            else:
                return ("🛑止损", STOP_LOSS_PCT * 100 - total_fee_pct*100, offset)
        
        if pnl_high >= PROFIT_TARGET:
            return ("✅止盈", PROFIT_TARGET * 100 - total_fee_pct*100, offset)
        if pnl_low <= STOP_LOSS_PCT:
            return ("🛑止损", STOP_LOSS_PCT * 100 - total_fee_pct*100, offset)
    
    last_idx = min(entry_idx + MAX_HOLD, len(candles) - 1)
    final_pnl = (candles[last_idx]["close"] - entry_price) / entry_price
    return ("⏰超时", final_pnl * 100 - total_fee_pct*100, MAX_HOLD)

def print_solana_report(trades, wallet_info, sol_price):
    """打印含链上费用的报告"""
    wins   = [t for t in trades if t["net_usd"] > 0]
    losses = [t for t in trades if t["net_usd"] <= 0]
    total  = len(trades)
    total_pnl  = sum(t["net_usd"] for t in trades)
    total_fees = sum(t["fee_usd"] for t in trades)
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
    sharpe = avg_r/max(std_r,0.001) * math.sqrt(50)
    
    print(f"{'═'*60}")
    print(f"  📊 Solana 链上环境回测报告")
    print(f"{'═'*60}\n")
    print(f"  🔗 钱包: {wallet_info['pubkey']} | 余额: {wallet_info['balance']}")
    print(f"  ⛽ Gas: {GAS_SOL} SOL/笔 | Jupiter费: {JUPITER_FEE_BPS/100:.1f}% | 滑点: {SLIPPAGE_BPS/100:.1f}%")
    print(f"  💰 SOL: ${sol_price:.2f}")
    print(f"\n  ┌──────────────────────────────────────┐")
    print(f"  │ 总交易:    {total:>3} 笔                      │")
    print(f"  │ 盈利:      {len(wins):>3} 笔 ({wr:.0f}%)                 │")
    print(f"  │ 亏损:      {len(losses):>3} 笔 ({100-wr:.0f}%)                 │")
    print(f"  │ 净盈亏:    {total_pnl:+.2f}$                    │")
    print(f"  │ 链上费用:  {total_fees:.3f}$                    │")
    print(f"  │ 均盈利:    {avg_w:+.3f}$                    │")
    print(f"  │ 均亏损:    {avg_l:.3f}$                    │")
    print(f"  │ 盈亏比:    {avg_w/max(avg_l,0.001):.1f}:1                     │")
    print(f"  │ 盈利因子:  {pf:.2f}                      │")
    print(f"  │ 最大回撤:  {max_dd:.2f}$                     │")
    print(f"  │ 夏普比率:  {sharpe:+.2f}                      │")
    print(f"  └──────────────────────────────────────┘")
    print(f"\n  💰 $40 → ${40+total_pnl:.2f} ({(total_pnl/40*100):+.1f}%)")
    print(f"  ⛽ 累计Gas: {total_fees:.4f} SOL ≈ ${total_fees*sol_price:.3f}")
    
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    print(f"\n  📋 出场: " + " | ".join(f"{r}:{c}" for r,c in reasons.items()))
    
    by_token = {}
    for t in trades:
        tok = t["token"]
        if tok not in by_token:
            by_token[tok] = {"cnt":0,"pnl":0,"w":0}
        by_token[tok]["cnt"] += 1
        by_token[tok]["pnl"] += t["net_usd"]
        if t["net_usd"] > 0: by_token[tok]["w"] += 1
    
    print(f"\n  🪙 各币表现:")
    for tok in sorted(by_token, key=lambda x: -by_token[x]["pnl"]):
        d = by_token[tok]
        wr_tok = d['w']/d['cnt']*100 if d['cnt'] else 0
        print(f"    {tok:6s} {d['cnt']:>3d}笔  WR{wr_tok:.0f}%  PnL{d['pnl']:+.2f}$")

# ====================== 主程序 ======================
def run(hours=24):
    print("═" * 60)
    print("  🔗 Solana 链上动量剥头皮 — 链上环境回测")
    print(f"  时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("═" * 60)
    
    # 1. 钱包
    wallet = solana_wallet_info()
    print(f"\n  🔑 钱包: {wallet['pubkey']}")
    print(f"  💰 余额: {wallet['balance']}")
    print(f"  🌐 RPC: {wallet['rpc']}")
    
    # 2. DexScreener 热门
    print(f"\n  📡 DexScreener: 获取 Solana 热门代币...")
    trending = dexscreener_trending(20)
    
    # 获取热门代币的详细数据
    sol_tokens = []
    for t in trending[:10]:
        addr = t.get("tokenAddress","")
        data = dexscreener_token(addr)
        if data and data["price"] > 0 and data["volume_24h"] > 10000:
            sol_tokens.append({
                "name": t.get("description","?")[:20],
                "address": addr[:8]+"...",
                "price": data["price"],
                "change_24h": data["change_24h"],
                "volume_24h": data["volume_24h"],
                "fdv": data["fdv"],
            })
    
    print(f"  📊 Solana 链上热门代币 (DexScreener):")
    for t in sol_tokens[:8]:
        print(f"    {t['name']:22s} ${t['price']:.8f}  "
              f"24h{t['change_24h']:+.1f}%  量${t['volume_24h']:,.0f}")
    
    # 3. 回测（用币安K线，模拟链上费用）
    print(f"\n{'─'*55}")
    print(f"  📈 回测参数: ${TRADE_AMOUNT_USD}/笔 | TP +{int(PROFIT_TARGET*100)}% | SL {abs(STOP_LOSS_PCT)*100:.0f}%")
    print(f"  ⛽ 链上费用: Jupiter {JUPITER_FEE_BPS/100:.1f}% + 滑点 {SLIPPAGE_BPS/100:.1f}% = ~{(JUPITER_FEE_BPS+SLIPPAGE_BPS)/100:.1f}%/笔")
    print(f"  📡 价格数据: 币安K线 (套利对齐 Solana 链上价)")
    print(f"  ⚠️  Jupiter API 被墙, 仅模拟链上费用结构\n")
    
    # 获取 SOL 价格
    try:
        resp = requests.get("https://api.binance.com/api/v3/ticker/price",
            params={"symbol":"SOLUSDT"}, timeout=5)
        sol_price = float(resp.json()["price"])
    except:
        sol_price = SOL_PRICE
    
    all_trades = []
    
    for sym in SYMBOLS_CEX:
        name = sym.replace("USDT","")
        print(f"  🔽 {name} 拉取K线...", end=" ", flush=True)
        candles = fetch_binance_klines(sym, hours=hours)
        print(f"{len(candles)}根 ({len(candles)/60:.1f}h)")
        
        if len(candles) < 60:
            continue
        
        sym_trades = 0
        signals = 0
        
        for i in range(2, len(candles) - MAX_HOLD):
            c = candles[i]
            prev = candles[i-1]
            momentum = (c["close"] - prev["close"]) / prev["close"] * 100
            
            if momentum <= 0.3:
                continue
            
            # 滚动24h涨幅 + 量过滤
            lookback = min(i, 1440)
            if lookback < 60:
                continue
            change_24h = (c["close"] - candles[i-lookback]["close"]) / candles[i-lookback]["close"] * 100
            recent_vol = sum(k["quote_vol"] for k in candles[max(0,i-1400):i+1])
            if recent_vol < 200_000:
                continue
            
            signals += 1
            
            # 模拟链上持仓
            reason, pnl_pct, hold_mins = simulate_solana_hold(candles, i, c["close"], sol_price)
            
            # 链上费用
            fee_usd = (JUPITER_FEE_BPS + SLIPPAGE_BPS) / 10000 * TRADE_AMOUNT_USD
            net_usd = TRADE_AMOUNT_USD * pnl_pct / 100
            
            all_trades.append({
                "token": name, "entry": c["close"],
                "reason": reason, "pnl_pct": pnl_pct,
                "net_usd": net_usd, "fee_usd": fee_usd,
                "hold": hold_mins, "momentum": momentum,
            })
            sym_trades += 1
            i += hold_mins
        
        print(f"    信号{signals}次 → 交易{sym_trades}笔")
        time.sleep(0.3)
    
    if all_trades:
        print_solana_report(all_trades, wallet, sol_price)
    else:
        print("\n  ⚠️ 无交易信号")
    
    return all_trades

if __name__ == "__main__":
    import sys
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    run(hours)
