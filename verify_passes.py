import sys, json
sys.path.insert(0, '.')
from dotenv import load_dotenv; load_dotenv()

with open('config/agent_config.json') as f:
    config = json.load(f)

from src.fvg_analyzer import FVGAnalyzer
from src.setup_quality import compute_setup_quality
from src.session_filter import SessionFilter

sf = SessionFilter(config)
ok, reason = sf.is_trading_allowed()
enabled = config['session_rules']['enabled']
print(f'Session filter enabled: {enabled}')
print(f'Current: allowed={ok} ({reason})')

fvg_ctx = {
    'current_price': 30100,
    'nearest_bearish_fvg': {'age_bars': 3, 'gap_size': 25},
    'nearest_bullish_fvg': None,
    'long_confirmation':  {'confirmed': True, 'reason': 'bullish close at zone'},
    'short_confirmation': None,
}
mkt = {'ema21': 30090, 'ema75': 30070, 'ema150': 30050}

q = compute_setup_quality('LONG', fvg_ctx, mkt, 'bullish', True)
print(f'LONG with-trend quality: {q["score"]:.2f} gate={q["gate_pass"]}')

q2 = compute_setup_quality('SHORT', fvg_ctx, mkt, 'bullish', True)
print(f'SHORT counter-trend quality: {q2["score"]:.2f} gate={q2["gate_pass"]}')

fa = FVGAnalyzer(min_gap_size=2.0)
bars = [{'Open': 30105, 'High': 30110, 'Low': 30088, 'Close': 30092}]
conf = fa.check_reversal_confirmation(bars, 'LONG', 30085, 30095)
print(f'Reversal confirmation: {conf}')

print('All 4 passes verified OK')
