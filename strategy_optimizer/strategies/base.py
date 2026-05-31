"""
BaseStrategy — thin wrapper around backtesting.py's Strategy class.

Adds common indicator helpers so user strategies stay concise.
Also provides load_strategy_from_code() for dynamic loading from a string.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from backtesting import Strategy
from backtesting.lib import crossover

# backtesting.lib doesn't export crossunder — implement it: a crosses below b ≡ b crosses above a
def crossunder(series1, series2) -> bool:
    return crossover(series2, series1)


class BaseStrategy(Strategy):
    """
    Inherit from this class when writing strategies for the optimizer.

    Helper methods wrap backtesting.py's self.I() for common indicators.
    All indicator helpers return numpy arrays; index with [-1] for latest value.
    """

    # ── Trend indicators ──────────────────────────────────────────────────────

    def ema(self, period: int, series=None):
        src = self.data.Close if series is None else series
        return self.I(
            lambda x, p: pd.Series(x).ewm(span=p, adjust=False).mean().values,
            src, period, name=f"EMA{period}",
        )

    def sma(self, period: int, series=None):
        src = self.data.Close if series is None else series
        return self.I(
            lambda x, p: pd.Series(x).rolling(p, min_periods=1).mean().values,
            src, period, name=f"SMA{period}",
        )

    def wma(self, period: int, series=None):
        """Weighted Moving Average."""
        src = self.data.Close if series is None else series
        def _wma(x, p):
            weights = np.arange(1, p + 1, dtype=float)
            return pd.Series(x).rolling(p).apply(
                lambda v: np.dot(v, weights) / weights.sum(), raw=True
            ).values
        return self.I(_wma, src, period, name=f"WMA{period}")

    # ── Momentum indicators ───────────────────────────────────────────────────

    def rsi(self, period: int = 14, series=None):
        src = self.data.Close if series is None else series
        def _rsi(close, p):
            s = pd.Series(close)
            delta = s.diff()
            gain = delta.clip(lower=0).ewm(alpha=1 / p, adjust=False).mean()
            loss = (-delta.clip(upper=0)).ewm(alpha=1 / p, adjust=False).mean()
            rs = gain / loss.replace(0, np.nan)
            return (100 - 100 / (1 + rs)).fillna(50).values
        return self.I(_rsi, src, period, name=f"RSI{period}")

    def macd_line(self, fast: int = 12, slow: int = 26):
        def _macd(c, f, s):
            c = pd.Series(c)
            return (c.ewm(span=f, adjust=False).mean() - c.ewm(span=s, adjust=False).mean()).values
        return self.I(_macd, self.data.Close, fast, slow, name=f"MACD{fast}_{slow}")

    def macd_signal_line(self, fast: int = 12, slow: int = 26, signal: int = 9):
        def _sig(c, f, s, sig):
            c = pd.Series(c)
            macd = c.ewm(span=f, adjust=False).mean() - c.ewm(span=s, adjust=False).mean()
            return macd.ewm(span=sig, adjust=False).mean().values
        return self.I(_sig, self.data.Close, fast, slow, signal, name=f"MACDSig{signal}")

    def stochastic_k(self, period: int = 14):
        def _stoch(h, l, c, p):
            h_ = pd.Series(h).rolling(p).max()
            l_ = pd.Series(l).rolling(p).min()
            return (100 * (pd.Series(c) - l_) / (h_ - l_).replace(0, np.nan)).fillna(50).values
        return self.I(_stoch, self.data.High, self.data.Low, self.data.Close, period, name=f"StochK{period}")

    # ── Volatility indicators ─────────────────────────────────────────────────

    def atr(self, period: int = 14):
        def _atr(h, l, c, p):
            h_ = pd.Series(h); l_ = pd.Series(l); c_ = pd.Series(c)
            tr = pd.concat([h_ - l_, (h_ - c_.shift()).abs(), (l_ - c_.shift()).abs()], axis=1).max(axis=1)
            return tr.ewm(alpha=1 / p, adjust=False).mean().values
        return self.I(_atr, self.data.High, self.data.Low, self.data.Close, period, name=f"ATR{period}")

    def bb_upper(self, period: int = 20, std: float = 2.0):
        def _bbu(c, p, s):
            c_ = pd.Series(c)
            m = c_.rolling(p, min_periods=1).mean()
            return (m + s * c_.rolling(p, min_periods=1).std()).values
        return self.I(_bbu, self.data.Close, period, std, name=f"BBU{period}")

    def bb_lower(self, period: int = 20, std: float = 2.0):
        def _bbl(c, p, s):
            c_ = pd.Series(c)
            m = c_.rolling(p, min_periods=1).mean()
            return (m - s * c_.rolling(p, min_periods=1).std()).values
        return self.I(_bbl, self.data.Close, period, std, name=f"BBL{period}")

    def bb_mid(self, period: int = 20):
        return self.sma(period)

    # ── Crossover helpers ─────────────────────────────────────────────────────

    def crossover(self, series1, series2) -> bool:
        """True when series1 crossed above series2 on this bar."""
        return crossover(series1, series2)

    def crossunder(self, series1, series2) -> bool:
        """True when series1 crossed below series2 on this bar."""
        return crossunder(series1, series2)


# ── Dynamic loader ─────────────────────────────────────────────────────────────

def load_strategy_from_code(code: str):
    """
    Exec a user-supplied strategy code string and return (strategy_class, param_ranges).

    The code must:
      1. Define exactly one class that subclasses BaseStrategy (or backtesting.Strategy).
      2. Optionally define PARAM_RANGES = { 'param': ('int'|'float'|'categorical', ...) }

    PARAM_RANGES format:
        'name': ('int',   low, high)          # integer range
        'name': ('float', low, high)          # float range
        'name': ('categorical', [a, b, c])    # discrete choices
    """
    namespace: dict = {
        "BaseStrategy": BaseStrategy,
        "Strategy": Strategy,
        "crossover": crossover,
        "crossunder": crossunder,
        "np": np,
        "pd": pd,
    }

    exec(compile(code, "<strategy>", "exec"), namespace)  # noqa: S102

    strategy_class = None
    for _name, obj in namespace.items():
        if (
            isinstance(obj, type)
            and issubclass(obj, Strategy)
            and obj not in (Strategy, BaseStrategy)
            and _name not in ("Strategy", "BaseStrategy")
        ):
            strategy_class = obj
            break

    if strategy_class is None:
        raise ValueError(
            "No Strategy subclass found. Your code must define a class that inherits "
            "from BaseStrategy (or backtesting.Strategy)."
        )

    param_ranges: dict = namespace.get("PARAM_RANGES", {})
    return strategy_class, param_ranges


# ── Built-in example strategies ───────────────────────────────────────────────

_EMA_CROSS = '''\
from strategy_optimizer.strategies.base import BaseStrategy

class EMACrossover(BaseStrategy):
    """Fast/slow EMA crossover with RSI momentum filter and ATR stop."""
    fast       = 10
    slow       = 30
    rsi_period = 14
    rsi_long   = 45.0   # RSI must be above this to go long
    rsi_short  = 55.0   # RSI must be below this to go short
    stop_atr   = 2.0

    def init(self):
        self.f   = self.ema(self.fast)
        self.s   = self.ema(self.slow)
        self.r   = self.rsi(self.rsi_period)
        self.a   = self.atr(14)

    def next(self):
        price = self.data.Close[-1]
        atr   = self.a[-1]

        if self.crossover(self.f, self.s) and self.r[-1] > self.rsi_long:
            self.buy(sl=price - self.stop_atr * atr)

        elif self.crossunder(self.f, self.s) and self.r[-1] < self.rsi_short:
            self.sell(sl=price + self.stop_atr * atr)

PARAM_RANGES = {
    "fast":       ("int",   5,   50),
    "slow":       ("int",  20,  200),
    "rsi_period": ("int",   7,   28),
    "rsi_long":   ("float", 30,  55),
    "rsi_short":  ("float", 45,  70),
    "stop_atr":   ("float", 0.5,  4.0),
}
'''

_RSI_MEAN_REV = '''\
from strategy_optimizer.strategies.base import BaseStrategy

class RSIMeanReversion(BaseStrategy):
    """RSI extremes + Bollinger Band squeeze for mean-reversion entries."""
    rsi_period   = 14
    oversold     = 30.0
    overbought   = 70.0
    bb_period    = 20
    bb_std       = 2.0
    stop_atr     = 1.5

    def init(self):
        self.r  = self.rsi(self.rsi_period)
        self.bu = self.bb_upper(self.bb_period, self.bb_std)
        self.bl = self.bb_lower(self.bb_period, self.bb_std)
        self.bm = self.bb_mid(self.bb_period)
        self.a  = self.atr(14)

    def next(self):
        price = self.data.Close[-1]
        atr   = self.a[-1]

        if not self.position:
            if self.r[-1] < self.oversold and price <= self.bl[-1]:
                self.buy(sl=price - self.stop_atr * atr)
            elif self.r[-1] > self.overbought and price >= self.bu[-1]:
                self.sell(sl=price + self.stop_atr * atr)

        elif self.position.is_long and price >= self.bm[-1]:
            self.position.close()
        elif self.position.is_short and price <= self.bm[-1]:
            self.position.close()

PARAM_RANGES = {
    "rsi_period": ("int",   7,  28),
    "oversold":   ("float", 15, 40),
    "overbought": ("float", 60, 85),
    "bb_period":  ("int",   10, 50),
    "bb_std":     ("float", 1.0, 3.5),
    "stop_atr":   ("float", 0.5, 3.0),
}
'''

_MACD_TREND = '''\
from strategy_optimizer.strategies.base import BaseStrategy

class MACDTrend(BaseStrategy):
    """MACD crossover with 200-EMA trend filter and ATR trailing stop."""
    macd_fast   = 12
    macd_slow   = 26
    macd_signal = 9
    trend_ema   = 200
    stop_atr    = 2.0

    def init(self):
        self.ml = self.macd_line(self.macd_fast, self.macd_slow)
        self.ms = self.macd_signal_line(self.macd_fast, self.macd_slow, self.macd_signal)
        self.t  = self.ema(self.trend_ema)
        self.a  = self.atr(14)

    def next(self):
        price = self.data.Close[-1]
        atr   = self.a[-1]
        bull  = price > self.t[-1]

        if self.crossover(self.ml, self.ms) and bull:
            self.buy(sl=price - self.stop_atr * atr)
        elif self.crossunder(self.ml, self.ms) and not bull:
            self.sell(sl=price + self.stop_atr * atr)

PARAM_RANGES = {
    "macd_fast":   ("int",  5,  20),
    "macd_slow":   ("int", 15,  60),
    "macd_signal": ("int",  3,  15),
    "trend_ema":   ("int", 50, 400),
    "stop_atr":    ("float", 0.5, 5.0),
}
'''

_BB_BREAKOUT = '''\
from strategy_optimizer.strategies.base import BaseStrategy

class BollingerBreakout(BaseStrategy):
    """Bollinger Band breakout (momentum) with RSI confirmation."""
    bb_period  = 20
    bb_std     = 2.0
    rsi_period = 14
    rsi_min    = 55.0   # RSI threshold to confirm long breakout
    stop_atr   = 1.5

    def init(self):
        self.bu = self.bb_upper(self.bb_period, self.bb_std)
        self.bl = self.bb_lower(self.bb_period, self.bb_std)
        self.r  = self.rsi(self.rsi_period)
        self.a  = self.atr(14)

    def next(self):
        price = self.data.Close[-1]
        prev  = self.data.Close[-2]
        atr   = self.a[-1]

        if not self.position:
            if price > self.bu[-1] and prev <= self.bu[-2] and self.r[-1] > self.rsi_min:
                self.buy(sl=price - self.stop_atr * atr)
            elif price < self.bl[-1] and prev >= self.bl[-2] and self.r[-1] < (100 - self.rsi_min):
                self.sell(sl=price + self.stop_atr * atr)

        elif self.position.is_long and price < self.bu[-1]:
            self.position.close()
        elif self.position.is_short and price > self.bl[-1]:
            self.position.close()

PARAM_RANGES = {
    "bb_period":  ("int",   10, 50),
    "bb_std":     ("float", 1.2, 3.5),
    "rsi_period": ("int",    7, 28),
    "rsi_min":    ("float", 50, 75),
    "stop_atr":   ("float", 0.5, 3.0),
}
'''

EXAMPLE_STRATEGIES: dict = {
    "ema_crossover":     {"description": "Fast/slow EMA crossover + RSI filter + ATR stop", "code": _EMA_CROSS},
    "rsi_mean_reversion":{"description": "RSI extremes + Bollinger Band squeeze mean-reversion", "code": _RSI_MEAN_REV},
    "macd_trend":        {"description": "MACD crossover trend-following with 200-EMA filter", "code": _MACD_TREND},
    "bb_breakout":       {"description": "Bollinger Band breakout with RSI momentum confirmation", "code": _BB_BREAKOUT},
}
