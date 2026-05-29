#!/usr/bin/env python3
"""
🦴 土狗猎手 — DexScreener 热榜 Solana 新币实时监控
================================================
策略: 抓 DexScreener 热榜 → 分析波动/动量 → 信号 → 模拟交易
数据: DexScreener API (免费, 实时)
执行: 模拟 (先不真金白银)

信号条件 (针对土狗):
  24h涨幅 > 100%      ← 已爆发的
  短期动量持续        ← 还在涨
  流动性 > $10k       ← 能进出
  成交量 > $50k/24h   ← 有人在玩
"""
import requests, time, json, math
from datetime import datetime, timezone
from collections import defaultdict

# ====================== 配置 ======================
TRADE_AMOUNT  = 5.0         # $5/笔 (≈0.06 SOL)
MIN_LIQUIDITY = 10_000      # 最低流动性
MIN_VOLUME_24 = 50_000      # 最低24h成交量
MIN_CHANGE_24 = 50          # 最低24h涨幅(%)
MAX_FDV       = 50_000_000  # 最大FDV (别买太大的)
MAX_PAIRS     = 30          # 最多分析几个币

FEE_SOLANA    = 0.006       # Solana DEX 费用 ~0.6%

def fetch_dexscreener_trending():
    """DexScreener 热榜"""
    try:
        resp = requests.get(
            "https://api.dexscreener.com/token-profiles/latest/v1",
            timeout=15)
        return [t for t in resp.json() if t.get("chainId") == "solana"]
    except Exception as e:
        print(f"  ⚠️ DexScreener 热榜错误: {e}")
        return []

def fetch_token_detail(address):
    """获取单个代币详情"""
    try:
        resp = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{address}",
            timeout=10)
        pairs = resp.json().get("pairs", [])
        if not pairs:
            return None
        
        # 取流动性最高的 pair
        best = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0)))
        
        return {
            "address": address,
            "name": best.get("baseToken", {}).get("name", "?"),
            "symbol": best.get("baseToken", {}).get("symbol", "?"),
            "price": float(best.get("priceUsd", 0)),
            "price_native": float(best.get("priceNative", 0)),
            "change_5m": float(best.get("priceChange", {}).get("m5", 0)),
            "change_1h": float(best.get("priceChange", {}).get("h1", 0)),
            "change_6h": float(best.get("priceChange", {}).get("h6", 0)),
            "change_24h": float(best.get("priceChange", {}).get("h24", 0)),
            "volume_5m": float(best.get("volume", {}).get("m5", 0)),
            "volume_1h": float(best.get("volume", {}).get("h1", 0)),
            "volume_24h": float(best.get("volume", {}).get("h24", 0)),
            "liquidity": float(best.get("liquidity", {}).get("usd", 0)),
            "fdv": float(best.get("fdv", 0)),
            "dex": best.get("dexId", "?"),
            "txns_5m": int(best.get("txns", {}).get("m5", {}).get("buys", 0)) + 
                       int(best.get("txns", {}).get("m5", {}).get("sells", 0)),
            "txns_1h": int(best.get("txns", {}).get("h1", {}).get("buys", 0)) + 
                       int(best.get("txns", {}).get("h1", {}).get("sells", 0)),
            "buys_5m": int(best.get("txns", {}).get("m5", {}).get("buys", 0)),
            "sells_5m": int(best.get("txns", {}).get("m5", {}).get("sells", 0)),
        }
    except Exception as e:
        return None

def score_token(t):
    """打分 (0-10)，越高越好"""
    score = 0.0
    
    # 正在涨
    if t["change_5m"] > 5:
        score += 2.0
    elif t["change_5m"] > 0:
        score += 1.0
    elif t["change_5m"] < -10:
        score -= 1.0  # 暴跌扣分
    
    # 24h 涨幅 (太高=风险, 太低=没意思)
    if 100 < t["change_24h"] < 1000:
        score += 2.0
    elif 50 < t["change_24h"] <= 100:
        score += 1.5
    elif t["change_24h"] > 1000:
        score += 0.5  # 太高了，可能是钓鱼
    
    # 买盘强
    if t["buys_5m"] > 0 and t["sells_5m"] > 0:
        buy_ratio = t["buys_5m"] / max(t["sells_5m"], 1)
        if buy_ratio > 2:
            score += 2.0
        elif buy_ratio > 1.2:
            score += 1.0
    
    # 流动性
    if t["liquidity"] > 100_000:
        score += 1.0
    elif t["liquidity"] > 30_000:
        score += 0.5
    
    # 成交活跃
    if t["txns_5m"] > 50:
        score += 1.5
    elif t["txns_5m"] > 20:
        score += 0.5
    
    # FDV 不要太大
    if t["fdv"] < 5_000_000:
        score += 1.0
    elif t["fdv"] < 20_000_000:
        score += 0.5
    
    return min(score, 10)

def generate_signal(t, score):
    """根据评分生成交易信号"""
    if score < 4:
        return None
    
    # 入场价
    entry = t["price"]
    
    # 动态 TP/SL (基于波动率调整)
    if t["change_5m"] > 20:
        tp_pct, sl_pct = 30, 15   # 高波动: 宽止损
    elif t["change_5m"] > 10:
        tp_pct, sl_pct = 20, 10
    elif t["change_5m"] > 3:
        tp_pct, sl_pct = 12, 6
    else:
        tp_pct, sl_pct = 8, 4
    
    # 扣除链上费用后的净收益预估
    gross = tp_pct
    net = gross - FEE_SOLANA * 2 * 100  # 往返费用
    
    return {
        "token": t["symbol"],
        "name": t["name"][:25],
        "address": t["address"][:10] + "...",
        "entry": entry,
        "tp_pct": tp_pct,
        "sl_pct": sl_pct,
        "score": score,
        "change_5m": t["change_5m"],
        "change_24h": t["change_24h"],
        "liquidity": t["liquidity"],
        "volume_24h": t["volume_24h"],
        "fdv": t["fdv"],
        "buys_5m": t["buys_5m"],
        "sells_5m": t["sells_5m"],
        "dex": t["dex"],
        "gross_profit": gross,
        "net_profit": net,
    }

# ====================== 主程序 ======================
def hunt():
    print("═" * 60)
    print("  🦴 土狗猎手 — Solana 热榜扫描")
    print(f"  ⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("═" * 60)
    
    # Step 1: 拉热榜
    print(f"\n  📡 拉取 DexScreener 热榜...")
    trending = fetch_dexscreener_trending()
    print(f"     获取到 {len(trending)} 个 Solana 代币")
    
    # Step 2: 逐一分析
    candidates = []
    analyzed = 0
    for t in trending[:MAX_PAIRS]:
        addr = t.get("tokenAddress", "")
        if not addr:
            continue
        
        detail = fetch_token_detail(addr)
        if not detail:
            continue
        analyzed += 1
        
        # 基础过滤
        if detail["volume_24h"] < MIN_VOLUME_24:
            continue
        if detail["liquidity"] < MIN_LIQUIDITY:
            continue
        if detail["change_24h"] < MIN_CHANGE_24:
            continue
        
        score = score_token(detail)
        detail["_score"] = score
        candidates.append(detail)
        
        # 打印基本信息
        icon = "🟢" if score >= 6 else "🟡" if score >= 4 else "⚪"
        print(f"  {icon} {detail['symbol']:8s} ${detail['price']:.8f}  "
              f"5m:{detail['change_5m']:+.1f}%  24h:{detail['change_24h']:+.0f}%  "
              f"LQ:${detail['liquidity']:,.0f}  评分:{score:.1f}")
        time.sleep(0.1)
    
    print(f"\n  📊 分析: {analyzed}个 → 通过过滤: {len(candidates)}个")
    
    # Step 3: 信号生成
    signals = []
    for c in sorted(candidates, key=lambda x: -x["_score"]):
        sig = generate_signal(c, c["_score"])
        if sig:
            signals.append(sig)
    
    # Step 4: 打印信号
    if signals:
        print(f"\n{'═'*60}")
        print(f"  🎯 交易信号 ({len(signals)} 个)")
        print(f"{'═'*60}\n")
        
        for i, s in enumerate(signals[:5], 1):
            print(f"  #{i} {s['token']} ({s['name']})")
            print(f"     地址: {s['address']}  交易所: {s['dex']}")
            print(f"     入场: ${s['entry']:.8f}  |  评分: {s['score']:.1f}/10")
            print(f"     5m: {s['change_5m']:+.1f}%  |  24h: {s['change_24h']:+.0f}%")
            print(f"     买/卖: {s['buys_5m']}/{s['sells_5m']}  |  流动性: ${s['liquidity']:,.0f}")
            print(f"     FDV: ${s['fdv']:,.0f}  |  Vol24h: ${s['volume_24h']:,.0f}")
            print(f"     TP: +{s['tp_pct']}%  |  SL: -{s['sl_pct']}%")
            print(f"     预估毛利: +{s['gross_profit']:.1f}%  |  净利(扣费): {s['net_profit']:+.1f}%")
            print(f"     ${TRADE_AMOUNT}/笔 → 净赚: ${TRADE_AMOUNT * s['net_profit']/100:+.3f}")
            
            if s['net_profit'] > 0:
                print(f"     ✅ 费用后仍有利可图")
            else:
                print(f"     ⚠️ 费用吃掉全部利润")
            print()
    else:
        print(f"\n  ⚠️ 无符合条件的信号 — 这波土狗不够猛")
    
    return signals

if __name__ == "__main__":
    hunt()
