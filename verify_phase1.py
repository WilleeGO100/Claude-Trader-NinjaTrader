import sys, json
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()

with open('config/agent_config.json') as f:
    config = json.load(f)

from src.session_filter import SessionFilter
from src.news_filter import NewsFilter
from src.market_analysis_manager import MarketAnalysisManager

sf = SessionFilter(config)
nf = NewsFilter(config)
mam = MarketAnalysisManager()

ok, reason = sf.is_trading_allowed()
print(f"Session filter: allowed={ok}, reason={reason}")

ok, reason = nf.is_trading_allowed()
print(f"News filter:    allowed={ok}, reason={reason}")

last = mam.current_analysis.get('last_updated', 'fresh')
print(f"Analysis last_updated: {last}")
print("All Phase 1 modules OK")
