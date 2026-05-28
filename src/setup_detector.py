"""
SetupDetector — runs deterministic setup checks each bar and injects
findings into Claude's prompt as additional context.

Detects:
  KELTNER_BOUNCE — price at Keltner channel extreme + stochastic extreme
  SWEEP_FVG      — liquidity sweep of recent swing + FVG in same direction

These give Claude concrete Plan B options beyond FVG_FILL / EMA_BOUNCE,
fixing the "locked on one setup, goes days without a trade" problem.
"""

from collections import deque
from typing import Dict, Any, Optional, List


class SetupDetector:

    def __init__(
        self,
        keltner_len: int = 20,
        keltner_mult: float = 1.5,
        stoch_extreme: float = 20.0,       # <= oversold, >= (100 - oversold) overbought
        sweep_lookback: int = 10,           # bars to look for swing highs/lows
        sweep_margin: float = 2.0,          # pts price must exceed swing to count as sweep
    ):
        self.keltner_len   = keltner_len
        self.keltner_mult  = keltner_mult
        self.stoch_extreme = stoch_extreme
        self.sweep_lookback = sweep_lookback
        self.sweep_margin  = sweep_margin

        self._closes: deque = deque(maxlen=500)
        self._highs:  deque = deque(maxlen=500)
        self._lows:   deque = deque(maxlen=500)
        self._kc_mid: Optional[float] = None

    # ── internal math ────────────────────────────────────────────────

    def _ema(self, prev: Optional[float], val: float, n: int) -> float:
        if prev is None:
            return val
        a = 2.0 / (n + 1.0)
        return a * val + (1.0 - a) * prev

    def _atr(self, n: int = 14) -> Optional[float]:
        c = list(self._closes)
        h = list(self._highs)
        l = list(self._lows)
        if len(c) < n + 1:
            return None
        trs = []
        for i in range(len(c) - n, len(c)):
            tr = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
            trs.append(tr)
        return sum(trs) / len(trs)

    def _stoch(self, n: int = 14) -> Optional[float]:
        c = list(self._closes)
        h = list(self._highs)
        l = list(self._lows)
        if len(c) < n:
            return None
        hi = max(h[-n:])
        lo = min(l[-n:])
        if hi <= lo:
            return None
        return 100.0 * (c[-1] - lo) / (hi - lo)

    def _keltner_bands(self) -> tuple:
        atr = self._atr(14)
        if atr is None or self._kc_mid is None:
            return None, None, None
        upper = self._kc_mid + self.keltner_mult * atr
        lower = self._kc_mid - self.keltner_mult * atr
        return self._kc_mid, upper, lower

    def _swing_high(self) -> Optional[float]:
        h = list(self._highs)
        n = min(self.sweep_lookback, len(h) - 1)
        if n < 2:
            return None
        return max(h[-n-1:-1])

    def _swing_low(self) -> Optional[float]:
        l = list(self._lows)
        n = min(self.sweep_lookback, len(l) - 1)
        if n < 2:
            return None
        return min(l[-n-1:-1])

    # ── main detection ────────────────────────────────────────────────

    def update(self, market_data: Dict[str, Any], fvg_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call each bar. Returns detected signals and a prompt section string.
        market_data keys used: close, high, low, stochastic, ema21
        fvg_context keys used: nearest_bullish_fvg, nearest_bearish_fvg
        """
        close = float(market_data.get('close', market_data.get('current_price', 0)))
        high  = float(market_data.get('high', close))
        low   = float(market_data.get('low', close))
        stoch = float(market_data.get('stochastic', 50))

        self._closes.append(close)
        self._highs.append(high)
        self._lows.append(low)
        self._kc_mid = self._ema(self._kc_mid, close, self.keltner_len)

        if len(self._closes) < max(self.keltner_len, 15):
            return {'signals': [], 'prompt_section': ''}

        kc_mid, kc_upper, kc_lower = self._keltner_bands()
        swing_hi = self._swing_high()
        swing_lo = self._swing_low()

        signals: List[Dict] = []

        # ── KELTNER_BOUNCE ────────────────────────────────────────────
        if kc_lower is not None:
            if close <= kc_lower and stoch <= self.stoch_extreme:
                signals.append({
                    'type':      'KELTNER_BOUNCE',
                    'direction': 'LONG',
                    'close':     close,
                    'kc_lower':  round(kc_lower, 2),
                    'kc_mid':    round(kc_mid, 2),
                    'stoch':     round(stoch, 1),
                    'note':      f"Price ({close:.2f}) at/below Keltner lower ({kc_lower:.2f}), "
                                 f"stoch oversold ({stoch:.1f}). Target: KC mid ({kc_mid:.2f}).",
                })
            elif close >= kc_upper and stoch >= (100 - self.stoch_extreme):
                signals.append({
                    'type':      'KELTNER_BOUNCE',
                    'direction': 'SHORT',
                    'close':     close,
                    'kc_upper':  round(kc_upper, 2),
                    'kc_mid':    round(kc_mid, 2),
                    'stoch':     round(stoch, 1),
                    'note':      f"Price ({close:.2f}) at/above Keltner upper ({kc_upper:.2f}), "
                                 f"stoch overbought ({stoch:.1f}). Target: KC mid ({kc_mid:.2f}).",
                })

        # ── SWEEP_FVG ─────────────────────────────────────────────────
        bull_fvg  = fvg_context.get('nearest_bullish_fvg')
        bear_fvg  = fvg_context.get('nearest_bearish_fvg')

        if swing_hi and high > swing_hi + self.sweep_margin and bear_fvg:
            signals.append({
                'type':       'SWEEP_FVG',
                'direction':  'SHORT',
                'sweep_level': round(swing_hi, 2),
                'close':       close,
                'fvg_zone':   f"{bear_fvg['bottom']:.2f}-{bear_fvg['top']:.2f}",
                'note':       f"Swept swing high ({swing_hi:.2f}) by {high - swing_hi:.1f} pts. "
                              f"Bearish FVG at {bear_fvg['bottom']:.2f}-{bear_fvg['top']:.2f} above. "
                              f"SHORT opportunity: enter on rejection, target FVG fill.",
            })

        if swing_lo and low < swing_lo - self.sweep_margin and bull_fvg:
            signals.append({
                'type':       'SWEEP_FVG',
                'direction':  'LONG',
                'sweep_level': round(swing_lo, 2),
                'close':       close,
                'fvg_zone':   f"{bull_fvg['bottom']:.2f}-{bull_fvg['top']:.2f}",
                'note':       f"Swept swing low ({swing_lo:.2f}) by {swing_lo - low:.1f} pts. "
                              f"Bullish FVG at {bull_fvg['bottom']:.2f}-{bull_fvg['top']:.2f} below. "
                              f"LONG opportunity: enter on reclaim, target FVG fill.",
            })

        prompt_section = self._build_prompt(signals, kc_mid, kc_upper, kc_lower, swing_hi, swing_lo, stoch)

        return {
            'signals':        signals,
            'prompt_section': prompt_section,
            'kc_mid':         kc_mid,
            'kc_upper':       kc_upper,
            'kc_lower':       kc_lower,
            'swing_hi':       swing_hi,
            'swing_lo':       swing_lo,
        }

    def _build_prompt(self, signals, kc_mid, kc_upper, kc_lower, swing_hi, swing_lo, stoch) -> str:
        lines = ["ADDITIONAL SETUP SIGNALS:"]

        if kc_mid:
            lines.append(
                f"  Keltner Channel: lower={kc_lower:.2f} | mid={kc_mid:.2f} | upper={kc_upper:.2f} | stoch={stoch:.1f}"
            )
        if swing_hi:
            lines.append(f"  Recent swing high: {swing_hi:.2f} (last {10} bars)")
        if swing_lo:
            lines.append(f"  Recent swing low:  {swing_lo:.2f} (last {10} bars)")

        if signals:
            lines.append("")
            for s in signals:
                lines.append(f"  *** {s['type']} {s['direction']} DETECTED ***")
                lines.append(f"      {s['note']}")
        else:
            lines.append("  No Keltner or Sweep/FVG signals active this bar.")

        lines.append("")
        return "\n".join(lines)
