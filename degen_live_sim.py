#!/usr/bin/env python3
"""
土狗动量剥头皮 v1.0
===================
选币: CMC Alpha ∩ 币安合约 → 低价高波动过滤
信号: 多周期ROC加速检测 + 量价确认
出场: 固定SL/TP + 移动止盈 + 超时
支持: live / backtest 双模式
"""

import requests, json, time, math, sys, os, signal
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

# ═══════════════════ 配置 ═══════════════════
BALANCE       = 40.0          # 本金
POSITION_USDT = 10.0          # 单笔保证金
LEVERAGE      = 10            # 杠杆
MAX_HOLD_MIN  = 15            # 超时(分钟)
SL_ATR_MULT   = 1.0           # 止损 = ATR × 1.0
SL_MIN        = 1.0           # 止损下限(%)
SL_MAX        = 4.0           # 止损上限(%)
TP_ATR_MULT   = 1.5           # 止盈 = ATR × 1.5
TP_MIN        = 1.5           # 止盈下限(%)
TP_MAX        = 8.0           # 止盈上限(%)
TRAIL_TRIGGER = 1.0           # 移动止盈激活(%)
TRAIL_GAP     = 1.0           # 回撤距离(%)
SIGNAL_MIN    = 3.5           # 最低信号分
SLIPPAGE      = 0.08          # 滑点(%)
SPREAD_MAX    = 0.20          # 最大价差(%)
COOLDOWN_SL   = 8             # 止损冷却(分)
COOLDOWN_TP   = 3             # 止盈冷却(分)
COOLDOWN_TO   = 5             # 超时冷却(分)
SCAN_INTERVAL = 30            # 扫描间隔(秒)
MAX_WORKERS   = 10            # 分析线程

# API
REST     = "https://fapi.binance.com"
CMC_URL  = "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listing?start=1&limit=500&sortBy=market_cap&sortType=desc&tagSlugs=binance-alpha"

# 文件
STATE_FILE = os.path.expanduser("~/.degen_state.json")
LOG_FILE   = os.path.expanduser("~/.degen_log.txt")

# ═══════════════════ 工具 ═══════════════════
def log(msg): 
    line = f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE,"a") as f: f.write(line+"\n")

def api(path, params=None):
    for i in range(3):
        try:
            r = requests.get(f"{REST}{path}", params=params, timeout=10)
            r.raise_for_status()
            d = r.json()
            if isinstance(d,dict) and d.get("code",0) < 0: raise Exception(str(d))
            return d
        except:
            if i==2: raise
            time.sleep(1)

def get_klines(sym, interval="1m", limit=60):
    data = api("/fapi/v1/klines", {"symbol":sym,"interval":interval,"limit":limit})
    return [{"ts":k[0],"o":float(k[1]),"h":float(k[2]),"l":float(k[3]),
             "c":float(k[4]),"v":float(k[5]),"qv":float(k[7]),"tbv":float(k[10])} for k in data]

def get_orderbook(sym, limit=20):
    d = api("/fapi/v1/depth", {"symbol":sym,"limit":limit})
    return ([(float(b[0]),float(b[1])) for b in d["bids"][:limit]],
            [(float(a[0]),float(a[1])) for a in d["asks"][:limit]])

def get_price(sym):
    try:
        d = api("/fapi/v1/ticker/bookTicker", {"symbol":sym})
        b,a = float(d["bidPrice"]), float(d["askPrice"])
        return (b+a)/2, b, a
    except: return 0,0,0

# ═══════════════════ ATR ═══════════════════
def calc_atr(kl, p=14):
    if len(kl) < p+1: return 1.0
    tr = []
    for i in range(1,len(kl)):
        h,l,pc = kl[i]["h"],kl[i]["l"],kl[i-1]["c"]
        tr.append(max(h-l,abs(h-pc),abs(l-pc))/pc*100)
    a = [sum(tr[:p])/p]
    for i in range(p,len(tr)): a.append((a[-1]*(p-1)+tr[i])/p)
    return a[-1]

# ═══════════════════ 信号分析 ═══════════════════
def analyze(sym):
    """多周期ROC加速度 + 量价确认"""
    try:
        k1 = get_klines(sym,"1m",30)
        if len(k1) < 20: return None
        
        # 动量: ROC(1m) + ROC(5m) + 加速度
        c = k1[-1]["c"]
        roc1 = (c/k1[-2]["c"]-1)*100
        roc5 = (c/k1[-6]["c"]-1)*100 if len(k1)>=7 else 0
        accel = roc1 - (k1[-2]["c"]/k1[-3]["c"]-1)*100  # 1m加速度
        
        # 量: 最近5根放量?
        vol5 = [k["v"] for k in k1[-5:]]
        vol20 = [k["v"] for k in k1[-20:]]
        vol_surge = sum(vol5)/5 > sum(vol20)/20 * 1.5
        
        # 价: 阳线率
        bull = sum(1 for k in k1[-5:] if k["c"]>=k["o"]) / 5
        
        # 订单簿
        try:
            bids,asks = get_orderbook(sym,5)
            bv = sum(b[1] for b in bids[:5])
            av = sum(a[1] for a in asks[:5])
            ob = (bv-av)/(bv+av) if bv+av>0 else 0
        except: ob = 0
        
        # 吃单比
        tbv = sum(k["tbv"] for k in k1[-5:])
        qv = sum(k["qv"] for k in k1[-5:])
        taker = (tbv/qv-0.5)*2 if qv>0 else 0
        
        # 综合得分: 动量×0.3 + 加速度×0.2 + 放量(仅方向一致时激活) + 阳线×2 + OB×2 + 吃单×1.5
        mom_dir = roc1*0.3 + roc5*0.2  # 动量方向
        accel_dir = accel * 0.2          # 加速度方向
        
        # 放量维度: 仅当加速度与动量同向时激活
        vol_bonus = 0
        if vol_surge and (mom_dir * accel_dir > 0):  # 同向才给分
            vol_bonus = 1.5
        
        score = (mom_dir + accel_dir + vol_bonus
                + (bull-0.5)*2
                + ob*2 + taker*1.5)
        
        atr = calc_atr(k1)
        direction = "LONG" if score > 0 else "SHORT"
        
        return {
            "symbol": sym, "direction": direction, "score": abs(score),
            "atr": atr, "vol_surge": vol_surge, "bull": round(bull,2)
        }
    except Exception as e:
        return None

# ═══════════════════ 选币 ═══════════════════
_alpha_cache = {"tokens":None,"ts":0}

def get_candidates():
    global _alpha_cache
    now = time.time()
    if _alpha_cache["tokens"] and now-_alpha_cache["ts"]<3600:
        return _alpha_cache["tokens"]
    
    # Alpha tokens
    try:
        cmc = requests.get(CMC_URL, timeout=10).json()
        alpha = {t["symbol"].upper() for t in cmc["data"]["cryptoCurrencyList"]}
    except: return _alpha_cache["tokens"] or []
    
    # Futures filter
    try:
        info = api("/fapi/v1/exchangeInfo")
        futures = {s["symbol"] for s in info["symbols"] 
                   if s["symbol"].endswith("USDT") and s["status"]=="TRADING" and s["contractType"]=="PERPETUAL"}
    except: return []
    
    cross = sorted(s+"USDT" for s in alpha if s+"USDT" in futures)
    
    # 24h成交量前20 (低价优先)
    tickers = api("/fapi/v1/ticker/24hr")
    tmap = {t["symbol"]:t for t in tickers}
    
    candidates = []
    for sym in cross:
        t = tmap.get(sym,{})
        count = int(t.get("count",0))
        if count < 200: continue
        price = float(t.get("lastPrice",0))
        if price > 5: continue  # 只做低价土狗
        spread = get_spread(sym) if "get_spread" in dir() else 0.08
        if spread > SPREAD_MAX: continue
        candidates.append({"symbol":sym, "trades_1h":count//24, "price":price})
    
    candidates.sort(key=lambda x:-x["trades_1h"])
    tokens = candidates[:15]
    _alpha_cache = {"tokens":tokens,"ts":now}
    log(f"  🪙 候选: {len(tokens)}只")
    return tokens

def get_spread(sym):
    try:
        d = api("/fapi/v1/ticker/bookTicker",{"symbol":sym})
        b,a = float(d["bidPrice"]), float(d["askPrice"])
        return (a-b)/b*100 if b>0 else 999
    except: return 999

# ═══════════════════ 状态 ═══════════════════
def load_state():
    try:
        with open(STATE_FILE) as f: s = json.load(f)
    except:
        s = {"balance":BALANCE,"pos":None,"history":[],"stats":{"trades":0,"wins":0,"losses":0,"pnl":0}}
    return s

def save_state(s):
    with open(STATE_FILE,"w") as f: json.dump(s,f,indent=2)

# ═══════════════════ 主引擎 ═══════════════════
class DegenScalper:
    def __init__(self):
        self.state = load_state()
        self.pos = None
        self.cooldown = {}
        self.running = True
    
    def run(self):
        log("🚀 土狗动量剥头皮 v1.0")
        log(f"💰 ${self.state['balance']:.2f} | {POSITION_USDT}U/笔 | {LEVERAGE}x | 超时{MAX_HOLD_MIN}min")
        log(f"🎯 SL=ATR×{SL_ATR_MULT}({SL_MIN}-{SL_MAX}%) TP=ATR×{TP_ATR_MULT}({TP_MIN}-{TP_MAX}%)")
        
        last_scan = 0
        while self.running:
            now = time.time()
            
            if self.pos:
                self._monitor()
                time.sleep(0.1)
                continue
            
            if now - last_scan < SCAN_INTERVAL:
                time.sleep(1)
                continue
            
            last_scan = now
            self._scan()
        
        log("🛑 引擎停止")
    
    def _monitor(self):
        p = self.pos
        price, bid, ask = get_price(p["symbol"])
        if price <= 0: return
        
        elapsed = (time.time() - p["ts"]) / 60
        direction = p["direction"]
        entry = p["entry"]
        lev = p["leverage"]
        
        # 盈亏(杠杆后)
        if direction == "LONG":
            pnl_pct = (price-entry)/entry*100*lev
        else:
            pnl_pct = (entry-price)/entry*100*lev
        
        # 移动止盈
        if pnl_pct >= TRAIL_TRIGGER:
            peak = max(p.get("peak",entry), price) if direction=="LONG" else min(p.get("peak",entry), price)
            p["peak"] = peak
            if direction == "LONG":
                trail_sl = peak * (1-TRAIL_GAP/100)
                if price <= trail_sl:
                    self._close(price, "TRAIL", "移动止盈")
                    return
            else:
                trail_sl = peak * (1+TRAIL_GAP/100)
                if price >= trail_sl:
                    self._close(price, "TRAIL", "移动止盈")
                    return
        
        # 固定SL/TP
        if direction == "LONG":
            if price >= p["tp"]: self._close(price, "TP", "止盈")
            elif price <= p["sl"]: self._close(price, "SL", "止损")
        else:
            if price <= p["tp"]: self._close(price, "TP", "止盈")
            elif price >= p["sl"]: self._close(price, "SL", "止损")
        
        if elapsed >= MAX_HOLD_MIN:
            self._close(price, "TIMEOUT", "超时")
    
    def _close(self, price, reason, cn):
        p = self.pos
        direction = p["direction"]
        entry = p["entry"]
        lev = p["leverage"]
        
        if direction == "LONG":
            raw = (price-entry)/entry*100*lev
        else:
            raw = (entry-price)/entry*100*lev
        
        slip = SLIPPAGE/100*lev*100
        pnl_pct = raw - slip
        pnl_usd = POSITION_USDT * pnl_pct / 100
        
        self.state["balance"] += pnl_usd
        self.state["stats"]["trades"] += 1
        if pnl_usd > 0: self.state["stats"]["wins"] += 1
        else: self.state["stats"]["losses"] += 1
        self.state["stats"]["pnl"] += pnl_usd
        
        self.state["history"].append({
            "symbol":p["symbol"], "dir":direction,
            "entry":entry, "exit":price, "reason":reason,
            "pnl":round(pnl_usd,4), "bal":round(self.state["balance"],2),
            "ts":datetime.now().isoformat()
        })
        
        emoji = "🟢" if pnl_usd >= 0 else "🔴"
        log(f"{emoji} {p['symbol']} {direction} {cn} ${pnl_usd:+.4f} | 💰${self.state['balance']:.2f}")
        
        self.state["pos"] = None
        self.pos = None
        save_state(self.state)
        
        # 冷却
        ct = COOLDOWN_SL if reason=="SL" else (COOLDOWN_TO if reason=="TIMEOUT" else COOLDOWN_TP)
        self.cooldown[p["symbol"]] = time.time() + ct*60
    
    def _scan(self):
        candidates = get_candidates()
        if not candidates: return
        
        # 过滤冷却
        syms = [c["symbol"] for c in candidates if time.time()>self.cooldown.get(c["symbol"],0)]
        if not syms: return
        
        log(f"  🧵 分析 {len(syms)}只...")
        results = []
        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS,len(syms))) as ex:
            futures = {ex.submit(analyze,s):s for s in syms}
            for f in as_completed(futures):
                r = f.result()
                if r and r["score"] >= SIGNAL_MIN:
                    results.append(r)
        
        if not results:
            log(f"  无信号 (≥{SIGNAL_MIN})")
            return
        
        results.sort(key=lambda r:-r["score"])
        
        for r in results[:3]:
            emoji = "📈" if r["direction"]=="LONG" else "📉"
            log(f"    {emoji} {r['symbol']:<12s} {r['direction']:<5s} score={r['score']:.1f} ATR={r['atr']:.2f}%")
        
        # 开最强信号
        best = results[0]
        sym = best["symbol"]
        price, bid, ask = get_price(sym)
        if price <= 0: return
        
        direction = best["direction"]
        atr = best["atr"]
        sl_pct = max(SL_MIN, min(atr*SL_ATR_MULT, SL_MAX))
        tp_pct = max(TP_MIN, min(atr*TP_ATR_MULT, TP_MAX))
        
        if direction == "LONG":
            entry = ask * (1+max(0.08,SLIPPAGE)/100)
            sl_price = entry * (1-sl_pct/100)
            tp_price = entry * (1+tp_pct/100)
        else:
            entry = bid * (1-max(0.08,SLIPPAGE)/100)
            sl_price = entry * (1+sl_pct/100)
            tp_price = entry * (1-tp_pct/100)
        
        self.pos = {
            "symbol":sym, "direction":direction,
            "entry":entry, "sl":sl_price, "tp":tp_price,
            "sl_pct":sl_pct, "tp_pct":tp_pct,
            "leverage":LEVERAGE, "ts":time.time(), "peak":entry
        }
        self.state["pos"] = {
            "symbol":sym, "direction":direction, "entry":entry,
            "sl_pct":sl_pct, "tp_pct":tp_pct, "ts":datetime.now().isoformat()
        }
        save_state(self.state)
        
        log(f"✅ 开仓 {sym} {direction} @${entry:.6f} SL={sl_pct:.1f}% TP={tp_pct:.1f}%")
    
    def stop(self):
        self.running = False

# ═══════════════════ 入口 ═══════════════════
def main():
    engine = DegenScalper()
    
    def handler(s,f): engine.stop()
    signal.signal(signal.SIGINT, handler)
    signal.signal(signal.SIGTERM, handler)
    
    try:
        engine.run()
    except KeyboardInterrupt: engine.stop()
    except Exception as e:
        log(f"💥 {e}")
        import traceback; traceback.print_exc()

# ═══════════════════ 回测 ═══════════════════
def run_backtest(hours=24):
    """用本地 DuckDB 1m K线回测"""
    try:
        import duckdb
    except ImportError:
        print("需要 duckdb: pip install duckdb")
        return
    
    db = os.path.expanduser("~/.hermes/crypto_klines.duckdb")
    if not os.path.exists(db):
        print(f"未找到 {db}，请先采集 1m K线数据")
        return
    
    d = duckdb.connect(db, read_only=True)
    max_ts = d.execute("SELECT MAX(open_time) FROM klines WHERE tf='1m'").fetchone()[0]
    min_ts = max_ts - hours*3600*1000
    
    symbols = [r[0] for r in d.execute(f"""
        SELECT DISTINCT symbol FROM klines 
        WHERE tf='1m' AND open_time>={min_ts}
        GROUP BY symbol HAVING COUNT(*)>=100
    """).fetchall()]
    print(f"回测: {len(symbols)}币种 × {hours}h ({datetime.fromtimestamp(min_ts/1000)} ~ {datetime.fromtimestamp(max_ts/1000)})")
    
    # 加载数据
    data = {}
    for sym in symbols:
        df = d.execute(f"""
            SELECT open_time,open,high,low,close,volume
            FROM klines WHERE tf='1m' AND symbol=? AND open_time>={min_ts}
            ORDER BY open_time
        """, [sym]).fetchdf()
        if len(df) >= 60:
            data[sym] = df.reset_index(drop=True)
    d.close()
    print(f"有效: {len(data)}币种")
    
    # 模拟
    all_ts = sorted(set().union(*[set(df['open_time']) for df in data.values()]))
    balance = BALANCE
    pos = None  # {symbol, direction, entry, sl, tp, ts, peak}
    trades = []
    cooldowns = {}
    
    # 简化版 analyze_backtest (跳过订单簿/吃单)
    def analyze_bt(sym, idx):
        df = data[sym]
        if idx < 30: return None
        c = df.iloc[idx]['close']
        roc1 = (c/df.iloc[idx-1]['close']-1)*100
        roc5 = (c/df.iloc[idx-5]['close']-1)*100 if idx>=5 else 0
        accel = roc1 - (df.iloc[idx-1]['close']/df.iloc[idx-2]['close']-1)*100
        
        v5 = df.iloc[max(0,idx-4):idx+1]['volume'].values
        v20 = df.iloc[max(0,idx-19):idx+1]['volume'].values
        vs = sum(v5)/len(v5) > sum(v20)/len(v20)*1.5
        
        bull = sum(1 for i in range(max(0,idx-4),idx+1) 
                   if df.iloc[i]['close']>=df.iloc[i]['open'])/min(5,idx+1)
        
        score = abs(roc1*0.3 + roc5*0.2 + accel*0.2 + (1.5 if vs else 0) + (bull-0.5)*2)
        direction = "LONG" if (roc1*0.3+roc5*0.2+accel*0.2) > 0 else "SHORT"
        
        # ATR
        trs = []
        for i in range(max(1,idx-20), idx+1):
            h,l,pc = df.iloc[i]['high'],df.iloc[i]['low'],df.iloc[i-1]['close']
            trs.append(max(h-l,abs(h-pc),abs(l-pc))/pc*100)
        atr = sum(trs)/len(trs) if trs else 1.0
        
        return {"symbol":sym,"direction":direction,"score":score,"atr":atr} if score>=SIGNAL_MIN else None
    
    for ts in all_ts:
        if pos:
            sym = pos["symbol"]
            df = data.get(sym)
            if df is None: pos=None; continue
            row = df[df['open_time']==ts]
            if len(row)==0: continue
            price = row.iloc[0]['close']
            high = row.iloc[0]['high']
            low = row.iloc[0]['low']
            elapsed = (ts-pos["ts"])/60000
            
            d_ = pos["direction"]; entry = pos["entry"]
            if d_=="LONG": pnl = (price-entry)/entry*100*LEVERAGE
            else: pnl = (entry-price)/entry*100*LEVERAGE
            
            # 移动止盈
            if pnl >= TRAIL_TRIGGER:
                peak = max(pos.get("peak",entry), high) if d_=="LONG" else min(pos.get("peak",entry), low)
                pos["peak"]=peak
                if d_=="LONG":
                    if low <= peak*(1-TRAIL_GAP/100):
                        trades.append({"s":sym,"d":d_,"e":entry,"x":peak*(1-TRAIL_GAP/100),"r":"TRAIL"})
                        balance += POSITION_USDT*pnl/100; pos=None; continue
                else:
                    if high >= peak*(1+TRAIL_GAP/100):
                        trades.append({"s":sym,"d":d_,"e":entry,"x":peak*(1+TRAIL_GAP/100),"r":"TRAIL"})
                        balance += POSITION_USDT*pnl/100; pos=None; continue
            
            # 固定SL/TP
            closed = False
            if d_=="LONG":
                if high>=pos["tp"]:
                    trades.append({"s":sym,"d":d_,"e":entry,"x":pos["tp"],"r":"TP"})
                    balance+=POSITION_USDT*((pos["tp"]-entry)/entry*100*LEVERAGE)/100; closed=True
                elif low<=pos["sl"]:
                    trades.append({"s":sym,"d":d_,"e":entry,"x":pos["sl"],"r":"SL"})
                    balance+=POSITION_USDT*((pos["sl"]-entry)/entry*100*LEVERAGE)/100; closed=True
            else:
                if low<=pos["tp"]:
                    trades.append({"s":sym,"d":d_,"e":entry,"x":pos["tp"],"r":"TP"})
                    balance+=POSITION_USDT*((entry-pos["tp"])/entry*100*LEVERAGE)/100; closed=True
                elif high>=pos["sl"]:
                    trades.append({"s":sym,"d":d_,"e":entry,"x":pos["sl"],"r":"SL"})
                    balance+=POSITION_USDT*((entry-pos["sl"])/entry*100*LEVERAGE)/100; closed=True
            
            if closed: pos=None; continue
            
            if elapsed >= MAX_HOLD_MIN:
                trades.append({"s":sym,"d":d_,"e":entry,"x":price,"r":"TO"})
                balance += POSITION_USDT*pnl/100; pos=None; cooldowns[sym]=ts+COOLDOWN_TO*60000
            continue
        
        # 扫描 (每30min)
        if not hasattr(run_backtest,"last_scan"): run_backtest.last_scan=0
        if ts-run_backtest.last_scan<30*60000: continue
        run_backtest.last_scan=ts
        
        candidates = [s for s in data if ts>cooldowns.get(s,0)]
        results = []
        for sym in candidates:
            idx = data[sym][data[sym]['open_time']==ts].index
            if len(idx)==0: continue
            r = analyze_bt(sym, idx[0])
            if r: results.append(r)
        
        if not results: continue
        results.sort(key=lambda r:-r["score"])
        
        best = results[0]; sym=best["symbol"]
        row = data[sym][data[sym]['open_time']==ts].iloc[0]
        price = row['close']
        atr = best["atr"]
        sl_pct = max(SL_MIN, min(atr*SL_ATR_MULT, SL_MAX))
        tp_pct = max(TP_MIN, min(atr*TP_ATR_MULT, TP_MAX))
        
        d_ = best["direction"]
        if d_=="LONG":
            entry=price*(1+SLIPPAGE/100)
            pos={"symbol":sym,"direction":d_,"entry":entry,
                 "sl":entry*(1-sl_pct/100),"tp":entry*(1+tp_pct/100),"ts":ts,"peak":entry}
        else:
            entry=price*(1-SLIPPAGE/100)
            pos={"symbol":sym,"direction":d_,"entry":entry,
                 "sl":entry*(1+sl_pct/100),"tp":entry*(1-tp_pct/100),"ts":ts,"peak":entry}
    
    # 结果
    wins = sum(1 for t in trades if t["r"] in ("TP","TRAIL") or (t["r"]=="TO" and 
        (t["d"]=="LONG" and t["x"]>t["e"] or t["d"]=="SHORT" and t["x"]<t["e"])))
    total = len(trades)
    
    print(f"\n{'='*50}")
    print(f"回测结果 ({hours}h)")
    print(f"${BALANCE:.2f} → ${balance:.2f} | {total}笔 | 胜率{wins/total*100:.0f}%" if total else "无交易")
    
    rs = {}
    for t in trades: rs[t["r"]] = rs.get(t["r"],0)+1
    print(f"出场: " + " ".join(f"{k}:{v}" for k,v in rs.items()))
    
    return {"balance":balance, "trades":total, "wins":wins}

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--backtest", type=int, nargs="?", const=24, help="回测小时数(默认24)")
    args = p.parse_args()
    if args.backtest:
        run_backtest(args.backtest)
    else:
        main()
