"""
HATrendDetector — Heikin Ashi Trend Sniper

Signals:
  HA_TREND LONG  — first bullish HA flip (prev candle was bearish, current is bullish)
                   with EMA21 rising or price above EMA21.
  HA_TREND SHORT — first bearish HA flip (prev candle was bullish, current is bearish)
                   with EMA21 falling or price below EMA21.
  HA_CONTINUATION — 3+ consecutive same-color HA candles confirming momentum.

A "bullish" HA candle: HA_Close > HA_Open.
A "bearish" HA candle: HA_Close < HA_Open.

The detector feeds a prompt_section string into Claude's context each bar,
giving Claude concrete entry signals with calculated stop levels based on
the first candle in the HA sequence.
"""

import math
from collections import deque
from typing import Dict, Any, Optional, Tuple


class HATrendDetector:
    def __init__(self, ema_confirm: bool = True, min_body_pts: float = 2.0, lookback: int = 60):
        """
        Args:
            ema_confirm:   require EMA alignment before signalling a flip
            min_body_pts:  minimum HA candle body size to count (filters doji noise)
            lookback:      bars of raw OHLC to keep in memory
        """
        self.ema_confirm  = ema_confirm
        self.min_body_pts = min_body_pts

        # Rolling raw OHLC
        self._opens  = deque(maxlen=lookback)
        self._highs  = deque(maxlen=lookback)
        self._lows   = deque(maxlen=lookback)
        self._closes = deque(maxlen=lookback)

        # Previous HA values (needed to compute next bar)
        self._prev_ha_open  = None
        self._prev_ha_close = None

        # Sequence tracking
        self._streak        = 0   # positive = bull streak, negative = bear streak
        self._streak_first_candle = None  # (ha_open, ha_close, ha_high, ha_low) of first candle in current streak

    # ── HA CALCULATION ────────────────────────────────────────────────────────
    def _ha_candle(self, o: float, h: float, l: float, c: float) -> Tuple[float, float, float, float]:
        ha_close = (o + h + l + c) / 4.0
        if self._prev_ha_open is None:
            ha_open = (o + c) / 2.0
        else:
            ha_open = (self._prev_ha_open + self._prev_ha_close) / 2.0
        ha_high = max(h, ha_open, ha_close)
        ha_low  = min(l, ha_open, ha_close)
        return ha_open, ha_high, ha_low, ha_close

    # ── MAIN UPDATE ───────────────────────────────────────────────────────────
    def update(self, market_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call each bar with market_data containing:
          open, high, low, close  — raw OHLC (5-min bar)
          ema21, ema75            — for trend alignment check

        Returns:
          {
            'signal':         'HA_FLIP_LONG' | 'HA_FLIP_SHORT' | 'HA_CONTINUATION_LONG'
                              | 'HA_CONTINUATION_SHORT' | None,
            'streak':         int (positive=bull, negative=bear),
            'ha_open':        float,
            'ha_close':       float,
            'ha_high':        float,
            'ha_low':         float,
            'stop_level':     float | None,
            'ema_aligned':    bool,
            'prompt_section': str,
          }
        """
        o = float(market_data.get('open',  market_data.get('close', 0)) or 0)
        h = float(market_data.get('high',  o) or o)
        l = float(market_data.get('low',   o) or o)
        c = float(market_data.get('close', o) or o)
        ema21 = float(market_data.get('ema21') or 0)
        ema75 = float(market_data.get('ema75') or 0)

        self._opens.append(o); self._highs.append(h)
        self._lows.append(l);  self._closes.append(c)

        if len(self._closes) < 3:
            self._prev_ha_open = (o + c) / 2.0
            self._prev_ha_close = (o + h + l + c) / 4.0
            return {'signal': None, 'streak': 0, 'ha_open': 0, 'ha_close': 0,
                    'ha_high': 0, 'ha_low': 0, 'stop_level': None,
                    'ema_aligned': False, 'prompt_section': ''}

        ha_open, ha_high, ha_low, ha_close = self._ha_candle(o, h, l, c)

        is_bull = ha_close > ha_open
        is_bear = ha_close < ha_open
        body    = abs(ha_close - ha_open)

        # EMA alignment
        ema_bull = (ema21 > ema75) or (c > ema21 and ema21 > 0)
        ema_bear = (ema21 < ema75) or (c < ema21 and ema21 > 0)

        prev_bull = self._streak > 0
        prev_bear = self._streak < 0

        signal     = None
        stop_level = None

        if is_bull and body >= self.min_body_pts:
            if prev_bear or self._streak == 0:
                # Flip to bullish
                self._streak = 1
                self._streak_first_candle = (ha_open, ha_close, ha_high, ha_low)
                if not self.ema_confirm or ema_bull:
                    signal = 'HA_FLIP_LONG'
                    stop_level = ha_low - 5.0
            else:
                self._streak += 1
                if self._streak == 3:
                    signal = 'HA_CONTINUATION_LONG'
                    if self._streak_first_candle:
                        stop_level = self._streak_first_candle[3] - 5.0  # first candle HA_low - 5
        elif is_bear and body >= self.min_body_pts:
            if prev_bull or self._streak == 0:
                # Flip to bearish
                self._streak = -1
                self._streak_first_candle = (ha_open, ha_close, ha_high, ha_low)
                if not self.ema_confirm or ema_bear:
                    signal = 'HA_FLIP_SHORT'
                    stop_level = ha_high + 5.0
            else:
                self._streak -= 1
                if self._streak == -3:
                    signal = 'HA_CONTINUATION_SHORT'
                    if self._streak_first_candle:
                        stop_level = self._streak_first_candle[2] + 5.0  # first candle HA_high + 5
        # else: doji / tiny body — streak unchanged

        self._prev_ha_open  = ha_open
        self._prev_ha_close = ha_close

        ema_aligned = (ema_bull if (self._streak >= 0) else ema_bear)

        prompt_section = self._build_prompt(
            signal, self._streak, ha_open, ha_close, ha_high, ha_low,
            stop_level, ema_aligned, ema21, ema75, c
        )

        return {
            'signal':         signal,
            'streak':         self._streak,
            'ha_open':        round(ha_open,  2),
            'ha_close':       round(ha_close, 2),
            'ha_high':        round(ha_high,  2),
            'ha_low':         round(ha_low,   2),
            'stop_level':     round(stop_level, 2) if stop_level else None,
            'ema_aligned':    ema_aligned,
            'prompt_section': prompt_section,
        }

    # ── PROMPT BUILDER ────────────────────────────────────────────────────────
    def _build_prompt(self, signal, streak, ha_open, ha_close, ha_high, ha_low,
                      stop_level, ema_aligned, ema21, ema75, close) -> str:
        color  = 'BULLISH' if ha_close >= ha_open else 'BEARISH'
        body   = abs(ha_close - ha_open)
        streak_str = f"{abs(streak)} bar {'bull' if streak > 0 else 'bear'} streak" if streak != 0 else "no streak"

        lines = ["HEIKIN ASHI TREND SIGNALS:"]
        lines.append(f"  Current HA candle: {color} | body={body:.2f}pts | streak={streak_str}")
        lines.append(f"  HA O={ha_open:.2f}  C={ha_close:.2f}  H={ha_high:.2f}  L={ha_low:.2f}")
        lines.append(f"  EMA21={ema21:.2f}  EMA75={ema75:.2f}  EMA aligned: {ema_aligned}")

        if signal:
            direction = 'LONG' if 'LONG' in signal else 'SHORT'
            sig_type  = 'FLIP' if 'FLIP' in signal else 'CONTINUATION'
            lines.append(f"")
            lines.append(f"  *** HA_TREND {direction} DETECTED ({sig_type}) ***")
            if stop_level:
                lines.append(f"      Suggested stop: {stop_level:.2f} (structure-based)")
            lines.append(f"      Setup type for JSON: HA_TREND")
            lines.append(f"      Note: {sig_type.lower()} signal — "
                         f"{'first bullish flip after bearish sequence' if direction=='LONG' else 'first bearish flip after bullish sequence'}"
                         if sig_type == 'FLIP' else
                         f"      Note: {abs(streak)}-bar momentum continuation {direction.lower()}.")
        else:
            lines.append(f"  No HA_TREND signal this bar.")

        lines.append("")
        return "\n".join(lines)
