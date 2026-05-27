import pandas as pd, json, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

with open('config/agent_config.json') as f:
    config = json.load(f)

from src.backtest_engine import BacktestEngine
engine = BacktestEngine(config)
df = engine.load_historical_data(days=30)
all_fvgs = engine.detect_fvgs_historical(df)

print('First 5 FVGs:')
for fvg in all_fvgs[:5]:
    idx = fvg['index']
    t = fvg['type']
    bot = fvg['bottom']
    top = fvg['top']
    print(f"  idx={idx} type={t} bottom={bot:.2f} top={top:.2f}")
    bar = df.iloc[idx]
    print(f"  Created bar: H={bar['High']:.2f} L={bar['Low']:.2f} C={bar['Close']:.2f}")
    if idx + 1 < len(df):
        nb = df.iloc[idx + 1]
        print(f"  Next bar:    H={nb['High']:.2f} L={nb['Low']:.2f}")
        if t == 'bullish':
            filled = nb['Low'] <= top
            print(f"  Bullish fill check: Low({nb['Low']:.2f}) <= top({top:.2f})? {filled}")
        else:
            filled = nb['High'] >= bot
            print(f"  Bearish fill check: High({nb['High']:.2f}) >= bottom({bot:.2f})? {filled}")
    print()

# Count how many bars each FVG survives before being filled
survival = []
for fvg in all_fvgs:
    for i in range(fvg['index'] + 1, len(df)):
        bar = df.iloc[i]
        if fvg['type'] == 'bullish' and bar['Low'] <= fvg['top']:
            survival.append(i - fvg['index'])
            break
        elif fvg['type'] == 'bearish' and bar['High'] >= fvg['bottom']:
            survival.append(i - fvg['index'])
            break
    else:
        survival.append(None)

filled_next_bar = sum(1 for s in survival if s == 1)
filled_within_5 = sum(1 for s in survival if s is not None and s <= 5)
never_filled = sum(1 for s in survival if s is None)
print(f"FVGs filled on very next bar: {filled_next_bar}/{len(all_fvgs)}")
print(f"FVGs filled within 5 bars:   {filled_within_5}/{len(all_fvgs)}")
print(f"FVGs never filled:            {never_filled}/{len(all_fvgs)}")
