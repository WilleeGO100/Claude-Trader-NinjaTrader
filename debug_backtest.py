import pandas as pd, json, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

with open('config/agent_config.json') as f:
    config = json.load(f)

from src.backtest_engine import BacktestEngine
from src.session_filter import SessionFilter

engine = BacktestEngine(config)
sf = SessionFilter(config)
df = engine.load_historical_data(days=7)
all_fvgs = engine.detect_fvgs_historical(df)
df_4h = engine.htf_analyzer.resample_from_1h(df)

print(f"Total bars: {len(df)}")
print(f"Total FVGs: {len(all_fvgs)}")
print(f"Bar time range: {df.iloc[0]['DateTime']} to {df.iloc[-1]['DateTime']}")
print()

session_blocked = 0
no_fvgs = 0
would_call_claude = 0

for i in range(3, len(df)):
    bar = df.iloc[i]
    bar_dt = bar['DateTime'].to_pydatetime()
    engine.update_fvg_status(all_fvgs, bar, i)
    active = engine.get_active_fvgs(all_fvgs, i)

    if not active:
        no_fvgs += 1
        continue

    session_ok, reason = sf.is_trading_allowed(bar_dt)
    if not session_ok:
        session_blocked += 1
        continue

    would_call_claude += 1

print(f"Bars with no active FVGs:  {no_fvgs}")
print(f"Bars blocked by session:   {session_blocked}")
print(f"Bars that would call Claude: {would_call_claude}")
print()

# Sample a few bar timestamps and session check results
print("Sample bar timestamps and session check:")
for i in [10, 30, 50, 80, 110, 140]:
    if i < len(df):
        bar = df.iloc[i]
        dt = bar['DateTime'].to_pydatetime()
        ok, reason = sf.is_trading_allowed(dt)
        print(f"  Bar {i}: {dt} → session_ok={ok} ({reason})")
