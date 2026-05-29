# 土狗动量剥头皮 (Degen Momentum Scalper)

CMC Binance Alpha × 币安永续合约，低价高波动土狗币动量剥头皮。

## 策略逻辑

```
选币: CMC Alpha (399 tokens) ∩ 币安合约 → 价< $5过滤 → 1h交易量Top15
信号: 多周期ROC加速度 + 量价确认 + 订单簿 + 吃单比
      综合得分 ≥ 5.5 开仓
出场: ATR×1.0 止损 / ATR×1.5 止盈 / 15min超时
      移动止盈: 浮盈>1%激活, 回撤1%触发
```

## 配置参数

| 参数 | 值 | 说明 |
|------|-----|------|
| POSITION_USDT | 10 | 单笔保证金 |
| LEVERAGE | 10 | 杠杆 |
| SL_ATR_MULT | 1.0 | 止损=ATR×1.0 |
| TP_ATR_MULT | 1.5 | 止盈=ATR×1.5 |
| TRAIL_TRIGGER | 1.0% | 移动止盈激活 |
| TRAIL_GAP | 1.0% | 回撤触发 |
| SIGNAL_MIN | 5.5 | 最低信号分 |
| MAX_HOLD_MIN | 15 | 超时(分钟) |

## 运行

```bash
python3 degen_live_sim.py          # 实盘
```

## 回测

内置 DuckDB 回测，需要提前采集 1m/5m/15m K线数据。

## 文件

- `degen_live_sim.py` — 主引擎
- `~/.degen_state.json` — 持仓状态
- `~/.degen_log.txt` — 运行日志

## 风险

土狗币波动极大，止损可能被跳穿。仅供研究，风险自负。
