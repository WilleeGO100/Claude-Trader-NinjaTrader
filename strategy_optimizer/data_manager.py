"""
Data Manager
Fetches OHLCV data via yfinance and generates walk-forward windows + synthetic data.
Walk-forward windows prevent curve-fitting by always validating on unseen data.
"""

import logging
import os
from datetime import datetime
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# yfinance interval → max lookback period
TIMEFRAME_PERIOD = {
    "1m":  "7d",
    "2m":  "60d",
    "5m":  "60d",
    "15m": "60d",
    "30m": "60d",
    "1h":  "730d",
    "4h":  "730d",
    "1d":  "5y",
    "1wk": "10y",
}

# Human alias → yfinance interval string
TIMEFRAME_ALIAS = {
    "4h": "1h",   # yfinance has no 4h; we resample from 1h below
}


class DataManager:
    def __init__(self, cache_dir: str = "strategy_optimizer/data_cache"):
        self.cache_dir = cache_dir
        os.makedirs(cache_dir, exist_ok=True)

    # ── Public API ────────────────────────────────────────────────────────────

    def fetch(self, ticker: str, timeframe: str) -> pd.DataFrame:
        """
        Fetch OHLCV data for ticker at the given timeframe.
        Supports 1m,5m,15m,30m,1h,4h,1d,1wk.
        Returns DataFrame with columns Open/High/Low/Close/Volume.
        """
        cache_key = f"{ticker}_{timeframe}".replace("=", "_").replace("-", "_")
        cache_file = os.path.join(self.cache_dir, f"{cache_key}.parquet")

        # Use cache if fresh (< 1 h old for intraday, < 1 day for daily+)
        if os.path.exists(cache_file):
            age_hours = (datetime.now().timestamp() - os.path.getmtime(cache_file)) / 3600
            max_age = 1 if timeframe in ("1m", "5m", "15m", "30m") else 24
            if age_hours < max_age:
                logger.info(f"Cache hit: {cache_key}")
                df = pd.read_parquet(cache_file)
                return df

        logger.info(f"Fetching {ticker} {timeframe}…")

        need_resample = timeframe == "4h"
        yf_interval = "1h" if need_resample else TIMEFRAME_ALIAS.get(timeframe, timeframe)
        period = TIMEFRAME_PERIOD.get(timeframe, "1y")

        raw = yf.download(
            ticker,
            interval=yf_interval,
            period=period,
            auto_adjust=True,
            progress=False,
        )

        if raw.empty:
            raise ValueError(f"yfinance returned no data for {ticker} {timeframe}")

        # Flatten multi-level columns (happens when downloading a single ticker)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)

        df = raw[["Open", "High", "Low", "Close", "Volume"]].dropna()

        if need_resample:
            df = self._resample_4h(df)

        df.index.name = "Date"
        logger.info(f"Fetched {len(df)} bars for {ticker} @ {timeframe}")

        df.to_parquet(cache_file)
        return df

    def walk_forward_windows(
        self,
        df: pd.DataFrame,
        train_ratio: float = 0.65,
        n_windows: int = 5,
        min_train_bars: int = 150,
        min_test_bars: int = 50,
    ) -> List[Tuple[pd.DataFrame, pd.DataFrame]]:
        """
        Slice the full dataset into N overlapping walk-forward windows.
        Each window has a train segment and an out-of-sample test segment.

        This is the core anti-overfitting mechanism: every trial in Optuna
        is evaluated against unseen test data; only trials that generalise
        are rewarded.

        Returns:
            List of (train_df, test_df) tuples.
        """
        n = len(df)
        windows: List[Tuple[pd.DataFrame, pd.DataFrame]] = []

        if n < (min_train_bars + min_test_bars) * 2:
            # Not enough bars for multiple windows — single split
            split = int(n * train_ratio)
            train, test = df.iloc[:split], df.iloc[split:]
            if len(train) >= min_train_bars and len(test) >= min_test_bars:
                windows.append((train, test))
                logger.info("Single walk-forward split (insufficient bars for multiple windows)")
            return windows

        step = n // n_windows

        for i in range(n_windows - 1):
            start = i * step
            end = min(start + step * 2, n)
            split = start + int((end - start) * train_ratio)

            train = df.iloc[start:split]
            test = df.iloc[split:end]

            if len(train) >= min_train_bars and len(test) >= min_test_bars:
                windows.append((train, test))

        logger.info(f"Created {len(windows)} walk-forward windows from {n} bars")
        return windows

    def generate_synthetic(
        self,
        n_bars: int = 1000,
        start_price: float = 100.0,
        drift: float = 0.0001,
        volatility: float = 0.015,
        regime_shifts: int = 3,
        seed: Optional[int] = None,
    ) -> pd.DataFrame:
        """
        Generate synthetic OHLCV using Geometric Brownian Motion with
        random regime shifts (bull/bear/sideways).  Used to stress-test
        strategies on data they could never have been fit to.
        """
        rng = np.random.default_rng(seed)

        # Randomly vary drift across regimes
        shifts = sorted(rng.integers(0, n_bars, regime_shifts).tolist())
        drifts = rng.uniform(-0.0003, 0.0003, regime_shifts + 1)

        drift_arr = np.empty(n_bars)
        prev = 0
        for idx, (s, d) in enumerate(zip(shifts + [n_bars], drifts)):
            drift_arr[prev:s] = d
            prev = s

        vols = np.abs(rng.normal(volatility, volatility * 0.3, n_bars)).clip(0.005, 0.05)
        log_returns = rng.normal(drift_arr, vols)
        closes = start_price * np.exp(np.cumsum(log_returns))

        noise = rng.uniform(0.001, 0.008, n_bars)
        opens = closes * np.exp(rng.normal(0, 0.003, n_bars))
        highs = np.maximum(opens, closes) * (1 + noise)
        lows = np.minimum(opens, closes) * (1 - noise)
        volumes = rng.integers(50_000, 2_000_000, n_bars).astype(float)

        dates = pd.date_range(end=datetime.now(), periods=n_bars, freq="1h")
        return pd.DataFrame(
            {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
            index=dates,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _resample_4h(self, df_1h: pd.DataFrame) -> pd.DataFrame:
        """Resample 1h OHLCV to 4h."""
        df = df_1h.resample("4h").agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
        ).dropna()
        return df
