"""
Intrabar Entry Watchdog
Monitors price tick-by-tick between bar closes.
When Claude has a WAITING setup, checks every few seconds if the trigger
condition has been met and fires an immediate entry via Groq (sub-second).
Claude still owns all bar-close analysis. Groq only confirms the trigger.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)


class IntrabarWatchdog:
    """
    Background thread that watches for intra-bar entry triggers.
    Reads Claude's WAITING setup from market_analysis.json and monitors
    LiveFeed.csv. When trigger conditions are met, calls Groq for fast
    confirmation then fires the entry signal immediately.
    """

    def __init__(self, config: Dict[str, Any], signal_generator, check_interval: float = 3.0):
        self.config           = config
        self.signal_generator = signal_generator
        self.check_interval   = check_interval

        # Import session/news filters to enforce same rules as main loop
        from src.session_filter import SessionFilter
        from src.news_filter import NewsFilter
        self._session_filter = SessionFilter(config)
        self._news_filter    = NewsFilter(config)

        self.groq_api_key  = os.getenv('GROQ_API_KEY', '')
        self.groq_model    = os.getenv('GROQ_MODEL', 'llama-3.3-70b-versatile')
        self.touch_tolerance = config.get('trading_params', {}).get('watchdog_touch_pts', 8.0)

        self.analysis_file   = Path('data/market_analysis.json')
        self.live_feed_path  = Path('data/LiveFeed.csv')
        self.historical_path = Path('data/HistoricalData.csv')

        self._open_position  = None
        self._running        = False
        self._thread         = None
        self._fired_this_bar = False
        self._last_bar_time  = None
        self._groq_client    = None
        self._paused         = False
        self._min_rr         = config.get('trading_params', {}).get('min_risk_reward', 1.3)
        self._groq_failures  = 0
        self._groq_max_fails = 5  # after 5 consecutive failures, log a warning

        # Called by main thread when watchdog fires — sets open_position there too
        self.on_entry_fired: Optional[Callable[[Dict], None]] = None

        self._init_groq()

    def _init_groq(self):
        if not self.groq_api_key:
            logger.warning("Watchdog: GROQ_API_KEY not set — watchdog disabled")
            return
        try:
            from groq import Groq
            self._groq_client = Groq(api_key=self.groq_api_key)
            logger.info(f"Watchdog: Groq initialized (model={self.groq_model})")
        except ImportError:
            logger.warning("Watchdog: groq package not installed — run: pip install groq")

    # ── Public control ─────────────────────────────────────────────────

    def start(self):
        if not self._groq_client:
            logger.warning("Watchdog not started — Groq unavailable")
            return
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True, name="IntrabarWatchdog")
        self._thread.start()
        logger.info(f"Watchdog started (interval={self.check_interval}s, touch={self.touch_tolerance}pts)")

    def stop(self):
        self._running = False

    def pause(self):
        """Pause watchdog during Claude's API call to prevent conflicts"""
        self._paused = True

    def resume(self):
        """Resume watchdog after Claude's API call finishes"""
        self._paused = False

    def set_open_position(self, position):
        self._open_position  = position
        if position is None:
            self._fired_this_bar = False

    def notify_new_bar(self, bar_time):
        if bar_time != self._last_bar_time:
            self._last_bar_time  = bar_time
            self._fired_this_bar = False

    # ── Main loop ──────────────────────────────────────────────────────

    def _loop(self):
        while self._running:
            try:
                self._check()
            except Exception as e:
                logger.debug(f"Watchdog error: {e}")
            time.sleep(self.check_interval)

    def _check(self):
        if self._open_position or self._fired_this_bar or self._paused:
            return

        # Respect session and news filters — same rules as main loop
        session_ok, _ = self._session_filter.is_trading_allowed()
        news_ok, _    = self._news_filter.is_trading_allowed()
        if not session_ok or not news_ok:
            return

        waiting = self._get_waiting_setup()
        if not waiting:
            return

        current_price = self._read_current_price()
        if current_price is None:
            return

        indicators = self._read_latest_indicators()
        if not indicators:
            return

        entry_plan  = waiting['entry_plan']
        stop_plan   = waiting['stop_plan']
        target_plan = waiting['target_plan']
        distance    = abs(current_price - entry_plan)

        if distance > self.touch_tolerance:
            return

        # Directional check — price must be approaching the entry FROM the correct side
        # LONG bounce: price should be AT or BELOW entry (pulling back to it)
        # SHORT bounce: price should be AT or ABOVE entry (rallying to it)
        direction = waiting['direction']
        if direction == 'LONG' and current_price > entry_plan + 2:
            logger.debug(f"Watchdog: LONG entry skip — price {current_price:.2f} is extended ABOVE entry {entry_plan:.2f}")
            return
        if direction == 'SHORT' and current_price < entry_plan - 2:
            logger.debug(f"Watchdog: SHORT entry skip — price {current_price:.2f} is extended BELOW entry {entry_plan:.2f}")
            return

        # Validate R:R and minimum stop distance at current price before bothering Groq
        risk     = abs(current_price - stop_plan)
        reward   = abs(target_plan - current_price)
        rr       = reward / risk if risk > 0 else 0
        min_stop = self.config.get('risk_management', {}).get('stop_loss_min', 20)

        if risk < min_stop:
            logger.debug(f"Watchdog: Stop too tight ({risk:.1f}pts < {min_stop}pt minimum) — skipping")
            return
        if rr < self._min_rr:
            logger.debug(f"Watchdog: R:R {rr:.2f} below minimum {self._min_rr} — skipping")
            return

        decision = self._ask_groq(waiting, current_price, indicators)

        if decision == 'ENTER':
            logger.info(f"Watchdog ENTRY: {waiting['direction']} @ {current_price:.2f} | R:R {rr:.2f} | Groq confirmed")
            self._fire_entry(waiting, current_price)

    # ── Groq confirmation ──────────────────────────────────────────────

    def _ask_groq(self, waiting: Dict, price: float, indicators: Dict) -> str:
        direction  = waiting['direction']
        entry      = waiting['entry_plan']
        stop       = waiting['stop_plan']
        target     = waiting['target_plan']
        confidence = waiting.get('confidence', 0.65)
        setup_type = waiting.get('setup_type', 'unknown')
        reasoning  = waiting.get('reasoning', '')[:200]

        risk   = abs(price - stop)   if stop   else 1
        reward = abs(target - price) if target else 0
        rr     = round(reward / risk, 2) if risk > 0 else 0

        ema21  = indicators.get('ema21', 0)
        ema75  = indicators.get('ema75', 0)
        stoch  = indicators.get('stochastic', 50)
        trend  = "UPTREND" if ema21 > ema75 else "DOWNTREND"

        waiting_for = waiting.get('waiting_for', reasoning)

        # Read order flow for Groq context
        of_section = ''
        try:
            from src.order_flow_reader import OrderFlowReader
            of_ctx = OrderFlowReader().get_context(price, window_seconds=30)
            if of_ctx.get('available'):
                of_section = (
                    f"\nOrder flow (last 30s):\n"
                    f"  Delta: {of_ctx['bar_delta']:+d} ({of_ctx['delta_direction'].replace('_',' ')})\n"
                    f"  Tape: {of_ctx['tape_speed']:.1f} prints/sec ({of_ctx['tape_trend']})\n"
                )
                if of_ctx['large_prints']:
                    p = of_ctx['large_prints'][-1]
                    of_section += f"  Large print: {p['size']}c @ {p['price']:.2f} ({p['side']})\n"
                if of_ctx['absorption']:
                    of_section += f"  ABSORPTION at {of_ctx['absorption_price']:.2f}\n"
        except Exception:
            pass

        prompt = (
            f"You are a NQ futures trade trigger validator. Reply with ENTER or WAIT only.\n\n"
            f"Claude's plan: {direction} {setup_type} near {entry:.2f}\n"
            f"  Stop: {stop:.2f} | Target: {target:.2f} | R:R {rr}:1 | Confidence: {confidence:.2f}\n\n"
            f"CRITICAL - What Claude is waiting for:\n"
            f"  {waiting_for}\n\n"
            f"Current intrabar conditions:\n"
            f"  Price: {price:.2f} ({abs(price - entry):.1f}pts from entry)\n"
            f"  EMA21: {ema21:.2f} | EMA75: {ema75:.2f} | Trend: {trend} | Stoch: {stoch:.1f}\n"
            f"{of_section}\n"
            f"RULES: Reply WAIT if Claude said price is overbought/extended or needs a pullback first.\n"
            f"If order flow shows delta still strongly against direction, reply WAIT.\n"
            f"Reply ENTER only if the specific trigger Claude described has clearly been met.\n"
            f"Has the {direction} entry trigger been met? ENTER or WAIT only."
        )

        try:
            resp = self._groq_client.chat.completions.create(
                model=self.groq_model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=5,
                temperature=0.0,
            )
            answer = resp.choices[0].message.content.strip().upper()
            result = 'ENTER' if 'ENTER' in answer else 'WAIT'
            logger.debug(f"Groq: {result}")
            self._groq_failures = 0  # reset on success
            return result
        except Exception as e:
            self._groq_failures += 1
            if self._groq_failures >= self._groq_max_fails:
                logger.error(f"Watchdog: Groq has failed {self._groq_failures} consecutive times — check API key/connectivity: {e}")
            else:
                logger.warning(f"Watchdog Groq error ({self._groq_failures}/{self._groq_max_fails}): {e}")
            return 'WAIT'

    # ── Entry firing ───────────────────────────────────────────────────

    def _fire_entry(self, waiting: Dict, price: float):
        from src.position_sizer import PositionSizer
        direction  = waiting['direction']
        stop       = waiting['stop_plan']
        target     = waiting['target_plan']
        confidence = waiting.get('confidence', 0.65)

        sizing = PositionSizer(self.config).compute_trade_sizing(
            direction, price, stop, target, confidence
        )

        signal = {
            'decision':   direction,
            'entry':      price,
            'stop':       stop,
            'target':     target,
            'risk_reward': round(abs(target - price) / abs(price - stop), 2) if abs(price - stop) > 0 else 0,
            'confidence': confidence,
            'reasoning':  'Intrabar watchdog — Groq confirmed trigger',
            'setup_type': 'watchdog',
            **sizing,
        }

        success = self.signal_generator.generate_signal(signal)
        if success:
            self._fired_this_bar = True
            position = {
                'direction':      direction,
                'entry':          price,
                'stop':           stop,
                'target':         target,
                'setup_type':     'watchdog',
                'confidence':     confidence,
                'ema21_at_entry': 0,
                'bars_in_trade':  0,
            }
            self._open_position = position
            if self.on_entry_fired:
                self.on_entry_fired(position)

    # ── Data readers ───────────────────────────────────────────────────

    def _get_waiting_setup(self) -> Optional[Dict]:
        try:
            if not self.analysis_file.exists():
                return None
            with open(self.analysis_file) as f:
                analysis = json.load(f)
            for side in ('long_assessment', 'short_assessment'):
                a = analysis.get(side, {})
                if a.get('status') == 'waiting':
                    direction = 'LONG' if side == 'long_assessment' else 'SHORT'
                    ep = a.get('entry_plan')
                    sp = a.get('stop_plan')
                    tp = a.get('target_plan')
                    if ep and sp and tp:
                        return {
                            'direction':   direction,
                            'entry_plan':  float(ep),
                            'stop_plan':   float(sp),
                            'target_plan': float(tp),
                            'confidence':  a.get('confidence', 0.65),
                            'setup_type':  a.get('setup_type', 'unknown'),
                            'reasoning':   a.get('reasoning', ''),
                            'waiting_for': analysis.get('waiting_for', ''),
                        }
        except Exception:
            pass
        return None

    def _read_current_price(self) -> Optional[float]:
        try:
            import pandas as pd
            df = pd.read_csv(self.live_feed_path)
            return float(df.iloc[-1]['Last']) if not df.empty else None
        except Exception:
            return None

    def _read_latest_indicators(self) -> Optional[Dict]:
        try:
            import pandas as pd
            df = pd.read_csv(self.historical_path)
            if df.empty:
                return None
            last = df.iloc[-1]
            return {
                'ema21':       float(last.get('EMA21', 0)),
                'ema75':       float(last.get('EMA75', 0)),
                'ema150':      float(last.get('EMA150', 0)),
                'stochastic':  float(last.get('StochD', 50)),
            }
        except Exception:
            return None
