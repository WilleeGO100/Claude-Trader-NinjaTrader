"""
Higher Timeframe (HTF) Analyzer
Reads the 4H CSV written by HTFDataFeed.cs and computes trend bias
(EMA8 vs EMA21) to add directional context to Claude's prompt.
Falls back gracefully when the file doesn't exist yet.
"""

import logging
import pandas as pd
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class HTFAnalyzer:
    """Computes higher-timeframe trend bias from a 1H OHLC CSV"""

    def __init__(self, htf_path: str = "data/HistoricalData_4H.csv", ema_fast: int = 8, ema_slow: int = 21):
        self.htf_path = Path(htf_path)
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow

    def load(self) -> Optional[pd.DataFrame]:
        if not self.htf_path.exists():
            return None
        try:
            df = pd.read_csv(self.htf_path)
            df['DateTime'] = pd.to_datetime(df['DateTime'])
            df = df.sort_values('DateTime').reset_index(drop=True)
            if len(df) < self.ema_slow + 5:
                return None
            df['EMA_fast'] = df['Close'].ewm(span=self.ema_fast, adjust=False).mean()
            df['EMA_slow'] = df['Close'].ewm(span=self.ema_slow, adjust=False).mean()
            return df
        except Exception as e:
            logger.warning(f"HTF load error: {e}")
            return None

    def get_bias(self) -> Dict[str, Any]:
        """
        Returns a dict with bias label, last price, EMA values, and a
        formatted string ready for injection into the Claude prompt.
        """
        df = self.load()
        if df is None:
            return {
                'bias': 'unknown',
                'available': False,
                'prompt_section': (
                    "4H HIGHER TIMEFRAME BIAS:\n"
                    "  Not available — apply HTFDataFeed.cs to a 4H NQ chart to enable.\n"
                ),
            }

        last = df.iloc[-1]
        price     = last['Close']
        ema_fast  = last['EMA_fast']
        ema_slow  = last['EMA_slow']
        bar_time  = last['DateTime']

        # Bias from EMA alignment
        # EMA bias
        if ema_fast > ema_slow * 1.001:
            ema_bias = 'bullish'
            ema_str  = f"EMA{self.ema_fast} ({ema_fast:.2f}) above EMA{self.ema_slow} ({ema_slow:.2f})"
        elif ema_fast < ema_slow * 0.999:
            ema_bias = 'bearish'
            ema_str  = f"EMA{self.ema_fast} ({ema_fast:.2f}) below EMA{self.ema_slow} ({ema_slow:.2f})"
        else:
            ema_bias = 'neutral'
            ema_str  = f"EMA{self.ema_fast} ({ema_fast:.2f}) ~ EMA{self.ema_slow} ({ema_slow:.2f})"

        # Structure bias (HH/HL vs LH/LL)
        struct_bias = self.get_structure_bias(df)
        struct_str  = {
            'bullish': 'HH + HL (bullish structure)',
            'bearish': 'LH + LL (bearish structure)',
            'neutral': 'Mixed structure (transitioning)',
            'unknown': 'Insufficient data',
        }.get(struct_bias, 'unknown')

        # Combined bias — both must agree for strong signal
        if ema_bias == 'bullish' and struct_bias == 'bullish':
            bias    = 'bullish'
            advice  = "Strong BULLISH confluence — EMA and structure aligned. Favor LONGs."
        elif ema_bias == 'bearish' and struct_bias == 'bearish':
            bias    = 'bearish'
            advice  = "Strong BEARISH confluence — EMA and structure aligned. Favor SHORTs."
        elif ema_bias == 'bullish' or struct_bias == 'bullish':
            bias    = 'bullish'
            advice  = "Mild BULLISH bias — only one signal aligned. Require extra confluence."
        elif ema_bias == 'bearish' or struct_bias == 'bearish':
            bias    = 'bearish'
            advice  = "Mild BEARISH bias — only one signal aligned. Require extra confluence."
        else:
            bias    = 'neutral'
            advice  = "No directional bias — EMA and structure both neutral. Require high confluence."

        prompt_section = (
            f"1H HIGHER TIMEFRAME BIAS:\n"
            f"  Last 1H bar: {bar_time.strftime('%m/%d %H:%M')} | Close: {price:.2f}\n"
            f"  EMA bias:       {ema_str}\n"
            f"  Structure bias: {struct_str}\n"
            f"  Combined:       {bias.upper()} — {advice}\n"
        )

        return {
            'bias': bias,
            'available': True,
            'price': price,
            'ema_fast': ema_fast,
            'ema_slow': ema_slow,
            'bar_time': str(bar_time),
            'prompt_section': prompt_section,
        }

    def resample_from_1h(self, df_1h: pd.DataFrame) -> Optional[pd.DataFrame]:
        """
        Generate synthetic 4H bars by resampling a 1H DataFrame.
        Used by the backtest engine when HTFDataFeed.cs isn't running.
        """
        try:
            df = df_1h.copy()
            df = df.set_index('DateTime')
            df_4h = df['Close'].resample('4h').ohlc()
            df_4h.columns = ['Open', 'High', 'Low', 'Close']
            df_4h = df_4h.dropna().reset_index()
            df_4h.rename(columns={'DateTime': 'DateTime'}, inplace=True)

            if len(df_4h) < self.ema_slow + 5:
                return None

            df_4h['EMA_fast'] = df_4h['Close'].ewm(span=self.ema_fast, adjust=False).mean()
            df_4h['EMA_slow'] = df_4h['Close'].ewm(span=self.ema_slow, adjust=False).mean()
            return df_4h
        except Exception as e:
            logger.warning(f"HTF resample error: {e}")
            return None

    def get_structure_bias(self, df_4h: pd.DataFrame, lookback: int = 6) -> str:
        """
        Determines HTF trend direction by comparing recent swing highs and lows.
        More robust than EMA crossover in choppy/transitioning markets.

        HH + HL = bullish structure
        LH + LL = bearish structure
        Mixed   = neutral/transitioning
        """
        if df_4h is None or len(df_4h) < lookback + 1:
            return 'unknown'

        recent = df_4h.tail(lookback)
        highs  = recent['High'].values if 'High' in recent.columns else recent['Close'].values
        lows   = recent['Low'].values  if 'Low'  in recent.columns else recent['Close'].values

        # Split into two halves and compare
        mid    = lookback // 2
        early_high = highs[:mid].max()
        late_high  = highs[mid:].max()
        early_low  = lows[:mid].min()
        late_low   = lows[mid:].min()

        hh = late_high > early_high   # higher high
        hl = late_low  > early_low    # higher low
        lh = late_high < early_high   # lower high
        ll = late_low  < early_low    # lower low

        if hh and hl:
            return 'bullish'
        if lh and ll:
            return 'bearish'
        if hh and ll:
            return 'neutral'  # expanding range
        if lh and hl:
            return 'neutral'  # contracting range
        return 'neutral'

    def get_bias_at_bar(self, df_4h: pd.DataFrame, bar_dt: pd.Timestamp) -> str:
        """
        Given a 4H DataFrame and a 1H bar timestamp, return the HTF bias
        that was active at that point in time (for backtest bar-by-bar use).
        """
        past = df_4h[df_4h['DateTime'] <= bar_dt]
        if past.empty:
            return 'unknown'
        last = past.iloc[-1]
        if last['EMA_fast'] > last['EMA_slow'] * 1.001:
            return 'bullish'
        if last['EMA_fast'] < last['EMA_slow'] * 0.999:
            return 'bearish'
        return 'neutral'


class CombinedHTFAnalyzer:
    """
    Two-layer HTF bias: 4H sets the macro trend, 1H fills in intraday structure.

    Gate hierarchy:
      - 4H bullish  + 1H bullish  → strong bullish  (block SHORTs hard)
      - 4H bullish  + 1H bearish  → bullish caution  (4H wins; SHORTs blocked, LONGs need conf >= 0.70)
      - 4H bullish  + 1H neutral  → mild bullish     (SHORTs blocked)
      - 4H bearish  + 1H bearish  → strong bearish   (block LONGs hard)
      - 4H bearish  + 1H bullish  → bearish caution  (4H wins; LONGs blocked, SHORTs need conf >= 0.70)
      - 4H bearish  + 1H neutral  → mild bearish     (LONGs blocked)
      - 4H neutral  + 1H bullish  → mild bullish     (SHORTs need conf >= 0.75)
      - 4H neutral  + 1H bearish  → mild bearish     (LONGs need conf >= 0.75)
      - 4H neutral  + 1H neutral  → neutral          (both need conf >= 0.75)
      - 4H unknown               → fall back to 1H only
      - Both unknown             → unknown (conf >= 0.75 required)
    """

    def __init__(
        self,
        path_4h: str = "data/HistoricalData_4H.csv",
        path_1h: str = "data/HistoricalData_1H.csv",
    ):
        self.analyzer_4h = HTFAnalyzer(htf_path=path_4h, ema_fast=8, ema_slow=21)
        self.analyzer_1h = HTFAnalyzer(htf_path=path_1h, ema_fast=8, ema_slow=21)

    def get_bias(self) -> Dict[str, Any]:
        bias_4h = self.analyzer_4h.get_bias()
        bias_1h = self.analyzer_1h.get_bias()

        b4 = bias_4h.get('bias', 'unknown')
        b1 = bias_1h.get('bias', 'unknown')

        combined, strength, counter_conf = self._combine(b4, b1)

        section_4h = bias_4h.get('prompt_section', '  4H data not available\n')
        section_1h = bias_1h.get('prompt_section', '  1H data not available\n')

        # Rewrite header labels clearly
        section_4h = section_4h.replace('1H HIGHER TIMEFRAME BIAS', '4H MACRO BIAS')
        section_1h = section_1h.replace('1H HIGHER TIMEFRAME BIAS', '1H STRUCTURE BIAS')

        advice = self._advice(b4, b1, combined, strength, counter_conf)

        prompt_section = (
            f"MULTI-TIMEFRAME BIAS:\n"
            f"{section_4h}"
            f"{section_1h}"
            f"  Combined verdict: {combined.upper()} ({strength}) — {advice}\n"
        )

        return {
            'bias': combined,
            'strength': strength,
            'counter_conf_required': counter_conf,
            'bias_4h': b4,
            'bias_1h': b1,
            'available': bias_4h.get('available', False) or bias_1h.get('available', False),
            'prompt_section': prompt_section,
        }

    def _combine(self, b4: str, b1: str):
        """Returns (combined_bias, strength, counter_conf_required)."""
        if b4 == 'unknown':
            # Fall back to 1H only
            if b1 == 'unknown':
                return 'unknown', 'none', 0.75
            return b1, 'mild', 0.75

        if b4 == 'bullish':
            if b1 == 'bullish':
                return 'bullish', 'strong', 1.0    # hard block on counter
            if b1 == 'bearish':
                return 'bullish', 'caution', 0.70  # 4H wins, but 1H diverging
            return 'bullish', 'mild', 1.0           # neutral 1H — still block counter

        if b4 == 'bearish':
            if b1 == 'bearish':
                return 'bearish', 'strong', 1.0
            if b1 == 'bullish':
                return 'bearish', 'caution', 0.70
            return 'bearish', 'mild', 1.0

        # 4H neutral — defer to 1H
        if b1 == 'bullish':
            return 'bullish', 'mild', 0.75
        if b1 == 'bearish':
            return 'bearish', 'mild', 0.75
        return 'neutral', 'none', 0.75

    def _advice(self, b4, b1, combined, strength, counter_conf):
        if strength == 'strong':
            return f"4H and 1H both {combined} — favor {combined.upper()[0:4]}s only"
        if strength == 'caution':
            opp = 'SHORT' if combined == 'bullish' else 'LONG'
            return f"4H {b4} overrides 1H {b1} divergence — {opp}s blocked; with-trend needs conf >= 0.70"
        if strength == 'mild' and b4 != 'neutral':
            opp = 'SHORT' if combined == 'bullish' else 'LONG'
            return f"4H {b4}, 1H neutral — {opp}s blocked"
        if strength == 'mild' and b4 == 'neutral':
            opp = 'SHORT' if combined == 'bullish' else 'LONG'
            return f"4H neutral, 1H {b1} — {opp}s need conf >= 0.75"
        return "No directional bias — both timeframes neutral; require conf >= 0.75"
