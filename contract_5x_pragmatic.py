#!/usr/bin/env python3
"""
5x合约务实版 — ALLO + SKYAI 专测
================================
TP $1.00 (4% nominal) / SL $0.50 (2% nominal)
自动多空 + 15min持仓
"""
import requests, time
from datetime import datetime, timezone, timedelta
from collections import Counter

TARGETS = [("ALLO","ALLOUSDT"),("SKYAI","SKYAIUSDT")]
MARGIN=5.0; LEV=5; NOTIONAL=MARGIN*LEV  # $25
TP=1.00; SL=0.50; HOLD=15
MOM=0.2; VB=1.5

FAPI="https://fapi.binance.com/fapi/v1"

def fetch(sym,h=24):
    r=[]; end=int(datetime.now(timezone.utc).timestamp()*1000)
    start=int((datetime.now(timezone.utc)-timedelta(hours=h)).timestamp()*1000)
    while True:
        resp=requests.get(f"{FAPI}/klines",params={
            "symbol":sym,"interval":"1m","startTime":start,"endTime":end,"limit":1500},timeout=15)
        b=resp.json()
        if not isinstance(b,list): break
        for k in b:
            r.append({"ts":int(k[0]),"open":float(k[1]),"high":float(k[2]),
                       "low":float(k[3]),"close":float(k[4]),"qv":float(k[7]),"tr":int(k[8])})
        if len(b)<1500: break
        start=int(b[-1][0])+1; time.sleep(0.15)
    return sorted(r,key=lambda x:x["ts"])

def bt(c):
    t=[]
    for i in range(6,len(c)-HOLD):
        ci,pr=c[i],c[i-1]
        mom=(ci["close"]-pr["close"])/pr["close"]*100
        av=sum(k["qv"] for k in c[i-5:i])/5
        vk=ci["qv"]>av*VB
        d=None
        if mom>MOM and vk: d="LONG"
        elif mom<-MOM and vk: d="SHORT"
        else: continue
        e=ci["close"]; q=NOTIONAL/e
        tp_p=e+TP/q if d=="LONG" else e-TP/q
        sl_p=e-SL/q if d=="LONG" else e+SL/q
        for o in range(1,HOLD+1):
            idx=i+o
            if idx>=len(c): break
            k=c[idx]
            ht=k["high"]>=tp_p if d=="LONG" else k["low"]<=tp_p
            hs=k["low"]<=sl_p if d=="LONG" else k["high"]>=sl_p
            if ht and hs:
                if abs(k["open"]-tp_p)<abs(k["open"]-sl_p):
                    t.append({"d":d,"r":"TP","p":TP,"h":o}); break
                else:
                    t.append({"d":d,"r":"SL","p":-SL,"h":o}); break
            elif ht: t.append({"d":d,"r":"TP","p":TP,"h":o}); break
            elif hs: t.append({"d":d,"r":"SL","p":-SL,"h":o}); break
        else:
            last=c[min(i+HOLD,len(c)-1)]
            pnl=(last["close"]-e)*q if d=="LONG" else (e-last["close"])*q
            t.append({"d":d,"r":"TO","p":pnl,"h":HOLD})
        i+=1
    return t

print("═"*55)
print(f"  ⚡ 5x务实版: TP${TP}/SL${SL} | ALLO + SKYAI")
print(f"  ⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
print(f"  {LEV}x | 保证金${MARGIN}→名义${NOTIONAL}")
print("═"*55)

all_r=[]
for name,sym in TARGETS:
    print(f"\n🪙 {name}")
    c=fetch(sym,24); print(f"  K线:{len(c)}根")
    amps=sorted((k["high"]-k["low"])/k["open"]*100 for k in c)
    print(f"  振幅P50:{amps[len(amps)//2]:.2f}% P90:{amps[int(len(amps)*.9)]:.2f}%")
    t=bt(c)
    if not t: print("  无信号"); continue
    w=[x for x in t if x["p"]>0]; tw=len(t)
    tp=sum(x["p"] for x in t); wr=len(w)/tw*100
    rs=Counter(x["r"] for x in t)
    long=sum(1 for x in t if x["d"]=="LONG")
    short=sum(1 for x in t if x["d"]=="SHORT")
    aw=sum(x["p"] for x in w)/max(len(w),1)
    al=sum(abs(x["p"]) for x in t if x["p"]<=0)/max(tw-len(w),1)
    rr=aw/max(al,0.001)
    tp_c=rs.get("TP",0); sl_c=rs.get("SL",0); to_c=rs.get("TO",0)
    tp_pnl=sum(x["p"] for x in t if x["r"]=="TP")
    sl_pnl=sum(x["p"] for x in t if x["r"]=="SL")
    max_w=max((x["p"] for x in t),default=0)
    icon="🟢" if tp>3 else "🟡" if tp>0 else "🔴"
    print(f"  {icon} {tw}笔 多{long}/空{short} WR{wr:.0f}%")
    print(f"     TP:{tp_c}次(+${tp_pnl:+.0f}) SL:{sl_c}次(${sl_pnl:+.0f}) TO:{to_c}次")
    print(f"     净PnL:${tp:+.2f} RR:{rr:.1f} 最大赢:${max_w:+.2f}")
    all_r.append({"name":name,"trades":tw,"long":long,"short":short,"wr":wr,
                   "pnl":tp,"tp_c":tp_c,"sl_c":sl_c,"to_c":to_c,
                   "tp_pnl":tp_pnl,"sl_pnl":sl_pnl,"rr":rr,"max_w":max_w})
    time.sleep(0.2)

print(f"\n{'═'*55}")
print(f"  📊 汇总")
print(f"{'═'*55}")
for r in all_r:
    print(f"  {r['name']:8s} {r['trades']:>3d}笔 TP{r['tp_c']} SL{r['sl_c']} PnL${r['pnl']:+.2f}")
total=sum(r["pnl"] for r in all_r)
print(f"\n  💰 总: ${total:+.2f}")
print(f"  📋 策略: {LEV}x | TP${TP} SL${SL} | 自动多空 | 15min")
print(f"  ✅ 推荐: 挂cron每5分钟扫2只币+推送信号")
