"""
Live Trend Monitor
Background thread that continuously watches current price vs macro EMAs.
No API calls — pure math, updated every 10 seconds.

Data sources:
  data/LiveFeed.csv          — real-time price (Last column)
  data/HistoricalData.csv    — EMA21, EMA75, EMA150 from primary chart
"""

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)

_POLL_INTERVAL   = 10        # seconds between reads
_MOMENTUM_WINDOW = 6         # number of reads for momentum calc (~60s)
_SUSTAINED_SOFT  = 3 * 60   # seconds above/below EMA21 to call it "sustained"
_SUSTAINED_HARD  = 10 * 60  # seconds for "firmly" confirmed


class LiveTrendMonitor:
    """
    Runs in a background thread. Exposes get_trend_state() for instant
    queries from the gate logic and get_prompt_section() for Claude.
    """

    def __init__(
        self,
        live_feed_path:  str = "data/LiveFeed.csv",
        historical_path: str = "data/HistoricalData.csv",
        poll_interval:   int = _POLL_INTERVAL,
    ):
        self._live_path  = Path(live_feed_path)
        self._hist_path  = Path(historical_path)
        self._interval   = poll_interval

        self._lock       = threading.Lock()
        self._thread:    Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Rolling price history for momentum
        self._price_history: deque = deque(maxlen=_MOMENTUM_WINDOW)

        # Sustained time tracking
        self._above_ema21_since: Optional[datetime] = None
        self._below_ema21_since: Optional[datetime] = None
        self._above_ema75_since: Optional[datetime] = None
        self._below_ema75_since: Optional[datetime] = None

        # Shared state — written by background thread, read by main
        self._state: Dict[str, Any] = {
            'price':          None,
            'ema21':          None,
            'ema75':          None,
            'ema150':         None,
            'trend_state':    'unknown',
            'ema21_delta':    0.0,
            'ema75_delta':    0.0,
            'ema21_sustained_mins': 0.0,
            'ema75_sustained_mins': 0.0,
            'momentum':       0.0,   # pts/min positive = up, negative = down
            'momentum_label': 'flat',
            'last_updated':   None,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="LiveTrendMonitor")
        self._thread.start()
        logger.info("LiveTrendMonitor started")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("LiveTrendMonitor stopped")

    def get_trend_state(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def get_prompt_section(self) -> str:
        s = self.get_trend_state()
        if s['price'] is None:
            return "LIVE TREND MONITOR:\n  Not available — LiveFeed.csv not found.\n"

        ts = s['last_updated'].strftime('%H:%M:%S') if s['last_updated'] else 'unknown'
        ema21_dir  = "ABOVE" if s['ema21_delta'] >= 0 else "BELOW"
        ema75_dir  = "ABOVE" if s['ema75_delta'] >= 0 else "BELOW"
        ema150_dir = "ABOVE" if s['ema150'] and s['price'] >= s['ema150'] else "BELOW"

        return (
            f"LIVE TREND MONITOR (as of {ts}):\n"
            f"  Price:   {s['price']:.2f}\n"
            f"  vs EMA21  ({s['ema21']:.2f}):  {ema21_dir} by {abs(s['ema21_delta']):.2f} pts"
            f"  [{s['ema21_sustained_mins']:.0f} min sustained]\n"
            f"  vs EMA75  ({s['ema75']:.2f}):  {ema75_dir} by {abs(s['ema75_delta']):.2f} pts"
            f"  [{s['ema75_sustained_mins']:.0f} min sustained]\n"
            f"  vs EMA150 ({s['ema150']:.2f}): {ema150_dir}\n"
            f"  Momentum: {s['momentum']:+.1f} pts/min ({s['momentum_label']})\n"
            f"  Trend state: {s['trend_state'].upper()}\n"
        )

    def apply_gate_override(self, htf_bias: Dict[str, Any]) -> Dict[str, Any]:
        """
        Use live trend state to downgrade HTF bias strength when price
        has physically moved against the closed-bar verdict.
        Only loosens hard blocks — never upgrades bias direction.
        """
        s = self.get_trend_state()
        if s['price'] is None:
            return htf_bias

        combined = htf_bias.get('bias', 'unknown')
        strength = htf_bias.get('strength', 'none')
        override = None

        if combined == 'bearish':
            if s['ema21_delta'] > 0 and strength in ('strong', 'mild'):
                # Price reclaimed EMA21 — closed-bar bearish bias loses its veto
                override = (
                    f"[LIVE MONITOR] price {s['price']:.2f} above EMA21 "
                    f"({s['ema21_sustained_mins']:.0f}min) -- bearish {strength} -> neutral"
                )
                htf_bias = dict(htf_bias)
                htf_bias['bias']                  = 'neutral'
                htf_bias['strength']              = 'none'
                htf_bias['counter_conf_required'] = 0.70

        elif combined == 'bullish':
            if s['ema21_delta'] < 0 and strength in ('strong', 'mild'):
                # Price lost EMA21 — closed-bar bullish bias loses its veto
                override = (
                    f"[LIVE MONITOR] price {s['price']:.2f} below EMA21 "
                    f"({s['ema21_sustained_mins']:.0f}min) -- bullish {strength} -> neutral"
                )
                htf_bias = dict(htf_bias)
                htf_bias['bias']                  = 'neutral'
                htf_bias['strength']              = 'none'
                htf_bias['counter_conf_required'] = 0.70

        if override:
            logger.info(override)
            htf_bias = dict(htf_bias)
            htf_bias['prompt_section'] = htf_bias.get('prompt_section', '') + f"  {override}\n"

        return htf_bias

    # ------------------------------------------------------------------
    # Background loop
    # ------------------------------------------------------------------

    def _run(self):
        while not self._stop_event.is_set():
            try:
                price = self._read_price()
                emas  = self._read_emas()
                if price is not None and emas is not None:
                    self._update_state(price, emas)
            except Exception as e:
                logger.warning(f"LiveTrendMonitor error: {e}")
            self._stop_event.wait(self._interval)

    def _read_price(self) -> Optional[float]:
        if not self._live_path.exists():
            return None
        try:
            df = pd.read_csv(self._live_path)
            if df.empty:
                return None
            return float(df.iloc[-1]['Last'])
        except Exception:
            return None

    def _read_emas(self) -> Optional[Dict[str, float]]:
        if not self._hist_path.exists():
            return None
        try:
            df = pd.read_csv(self._hist_path)
            if df.empty:
                return None
            last = df.iloc[-1]
            return {
                'ema21':  float(last.get('EMA21',  0)),
                'ema75':  float(last.get('EMA75',  0)),
                'ema150': float(last.get('EMA150', 0)),
            }
        except Exception:
            return None

    def _update_state(self, price: float, emas: Dict[str, float]):
        now    = datetime.now()
        ema21  = emas['ema21']
        ema75  = emas['ema75']
        ema150 = emas['ema150']

        # Update sustained time trackers
        self._update_sustained(price, ema21, ema75, now)

        # Momentum: pts/min over the rolling window
        self._price_history.append((now, price))
        momentum = self._calc_momentum()

        if   momentum >  5: momentum_label = 'accelerating bullish'
        elif momentum >  1: momentum_label = 'rising'
        elif momentum < -5: momentum_label = 'accelerating bearish'
        elif momentum < -1: momentum_label = 'falling'
        else:               momentum_label = 'flat'

        # Trend state
        ema21_delta = price - ema21
        ema75_delta = price - ema75

        if price > ema21 and price > ema75:
            trend_state = 'bullish'
        elif price < ema21 and price < ema75:
            trend_state = 'bearish'
        else:
            trend_state = 'transitioning'

        with self._lock:
            self._state.update({
                'price':          price,
                'ema21':          ema21,
                'ema75':          ema75,
                'ema150':         ema150,
                'trend_state':    trend_state,
                'ema21_delta':    ema21_delta,
                'ema75_delta':    ema75_delta,
                'ema21_sustained_mins': self._sustained_mins(price, ema21, now, above=ema21_delta >= 0),
                'ema75_sustained_mins': self._sustained_mins(price, ema75, now, above=ema75_delta >= 0, ema75=True),
                'momentum':       momentum,
                'momentum_label': momentum_label,
                'last_updated':   now,
            })

    def _update_sustained(self, price, ema21, ema75, now):
        # EMA21 sustained tracking
        if price > ema21:
            if self._above_ema21_since is None:
                self._above_ema21_since = now
            self._below_ema21_since = None
        else:
            if self._below_ema21_since is None:
                self._below_ema21_since = now
            self._above_ema21_since = None

        # EMA75 sustained tracking
        if price > ema75:
            if self._above_ema75_since is None:
                self._above_ema75_since = now
            self._below_ema75_since = None
        else:
            if self._below_ema75_since is None:
                self._below_ema75_since = now
            self._above_ema75_since = None

    def _sustained_mins(self, price, ema, now, above: bool, ema75: bool = False) -> float:
        if ema75:
            since = self._above_ema75_since if above else self._below_ema75_since
        else:
            since = self._above_ema21_since if above else self._below_ema21_since
        if since is None:
            return 0.0
        return (now - since).total_seconds() / 60.0

    def _calc_momentum(self) -> float:
        if len(self._price_history) < 2:
            return 0.0
        oldest_t, oldest_p = self._price_history[0]
        newest_t, newest_p = self._price_history[-1]
        elapsed_mins = (newest_t - oldest_t).total_seconds() / 60.0
        if elapsed_mins < 0.01:
            return 0.0
        return (newest_p - oldest_p) / elapsed_mins
