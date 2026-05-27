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
    """Computes higher-timeframe trend bias from a 4H OHLC CSV"""

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
        if ema_fast > ema_slow * 1.001:
            bias = 'bullish'
            bias_str = f"BULLISH — EMA{self.ema_fast} ({ema_fast:.2f}) above EMA{self.ema_slow} ({ema_slow:.2f})"
            advice = "Favor LONG setups; SHORT only on very high-confluence FVGs against the trend."
        elif ema_fast < ema_slow * 0.999:
            bias = 'bearish'
            bias_str = f"BEARISH — EMA{self.ema_fast} ({ema_fast:.2f}) below EMA{self.ema_slow} ({ema_slow:.2f})"
            advice = "Favor SHORT setups; LONG only on very high-confluence FVGs against the trend."
        else:
            bias = 'neutral'
            bias_str = f"NEUTRAL — EMA{self.ema_fast} ({ema_fast:.2f}) ≈ EMA{self.ema_slow} ({ema_slow:.2f})"
            advice = "No strong directional bias — require extra confluence before entering either direction."

        prompt_section = (
            f"4H HIGHER TIMEFRAME BIAS:\n"
            f"  Last 4H bar: {bar_time.strftime('%m/%d %H:%M')} | Close: {price:.2f}\n"
            f"  Trend: {bias_str}\n"
            f"  Guidance: {advice}\n"
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
