"""
Backtester
Thin wrapper around backtesting.py that returns a flat metrics dict.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional, Type

import numpy as np
import pandas as pd
from backtesting import Backtest

logger = logging.getLogger(__name__)

DEFAULT_CASH       = 100_000.0
DEFAULT_COMMISSION = 0.001   # 0.1 %


class Backtester:
    def __init__(
        self,
        cash: float = DEFAULT_CASH,
        commission: float = DEFAULT_COMMISSION,
    ):
        self.cash = cash
        self.commission = commission

    # ── Public API ────────────────────────────────────────────────────────────

    def run(
        self,
        strategy_class: Type,
        data: pd.DataFrame,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, float]:
        """
        Run a single backtest.

        Args:
            strategy_class: Class inheriting from backtesting.Strategy.
            data:           OHLCV DataFrame (index = DatetimeIndex).
            params:         Parameter overrides (kwargs forwarded to bt.run()).

        Returns:
            Flat dict of metrics.  Always returns something — errors give zeros.
        """
        try:
            bt = Backtest(
                data,
                strategy_class,
                cash=self.cash,
                commission=self.commission,
                exclusive_orders=True,
            )
            stats = bt.run(**(params or {}))
            return self._parse(stats)
        except Exception as exc:
            logger.debug(f"Backtest error ({strategy_class.__name__}): {exc}")
            return self._zeros()

    def plot(
        self,
        strategy_class: Type,
        data: pd.DataFrame,
        params: Optional[Dict[str, Any]] = None,
        filename: Optional[str] = None,
    ) -> Dict[str, float]:
        """Run and plot; saves to filename (HTML) or opens browser."""
        bt = Backtest(
            data,
            strategy_class,
            cash=self.cash,
            commission=self.commission,
            exclusive_orders=True,
        )
        stats = bt.run(**(params or {}))
        bt.plot(filename=filename, open_browser=(filename is None))
        return self._parse(stats)

    # ── Internals ─────────────────────────────────────────────────────────────

    @staticmethod
    def _safe(val, default: float = 0.0) -> float:
        try:
            v = float(val)
            return v if np.isfinite(v) else default
        except Exception:
            return default

    def _parse(self, stats) -> Dict[str, float]:
        s = self._safe
        return {
            "sharpe":        s(stats.get("Sharpe Ratio")),
            "sortino":       s(stats.get("Sortino Ratio")),
            "calmar":        s(stats.get("Calmar Ratio")),
            "return_pct":    s(stats.get("Return [%]")),
            "max_dd_pct":    s(stats.get("Max. Drawdown [%]")),
            "win_rate":      s(stats.get("Win Rate [%]")),
            "profit_factor": s(stats.get("Profit Factor")),
            "sqn":           s(stats.get("SQN")),
            "expectancy":    s(stats.get("Expectancy [%]")),
            "avg_trade_pct": s(stats.get("Avg. Trade [%]")),
            "n_trades":      s(stats.get("# Trades")),
            "exposure_pct":  s(stats.get("Exposure Time [%]")),
        }

    def _zeros(self) -> Dict[str, float]:
        return {k: 0.0 for k in [
            "sharpe", "sortino", "calmar", "return_pct", "max_dd_pct",
            "win_rate", "profit_factor", "sqn", "expectancy",
            "avg_trade_pct", "n_trades", "exposure_pct",
        ]}
