"""
Strategy Optimizer
Coordinates two agents:
  • ParamAgent   — Optuna TPE sampler searches parameter space for each timeframe
  • TimeframeAgent — iterates over requested timeframes in priority order

Anti-overfitting:
  Each Optuna trial draws a random walk-forward window and reports
  (train_sharpe + test_sharpe) / 2.  Trials that only fit training data
  get a low combined score and Optuna de-prioritises them automatically.
"""

from __future__ import annotations

import json
import logging
import random
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Type

import optuna

from .backtester import Backtester
from .data_manager import DataManager

logger = logging.getLogger(__name__)
optuna.logging.set_verbosity(optuna.logging.WARNING)


# ── Result container ──────────────────────────────────────────────────────────

class OptimizationState:
    def __init__(self):
        self.status: str = "idle"           # idle | running | completed | stopped | error
        self.best_score: float = -999.0
        self.best_params: Dict = {}
        self.best_timeframe: Optional[str] = None
        self.best_metrics: Dict = {}
        self.all_trials: List[Dict] = []
        self.trial_count: int = 0
        self.total_trials: int = 0
        self.current_tf: Optional[str] = None
        self.started_at: datetime = datetime.now()
        self._lock: threading.Lock = threading.Lock()

    def record_trial(
        self,
        tf: str,
        params: Dict,
        train_m: Dict,
        test_m: Dict,
        score: float,
    ):
        with self._lock:
            self.trial_count += 1
            rec = {
                "trial_id": self.trial_count,
                "timeframe": tf,
                "params": params,
                "train": {k: round(v, 4) for k, v in train_m.items()},
                "test":  {k: round(v, 4) for k, v in test_m.items()},
                "score": round(score, 4),
            }
            self.all_trials.append(rec)

            if score > self.best_score:
                self.best_score = score
                self.best_params = params.copy()
                self.best_timeframe = tf
                self.best_metrics = {
                    "train": rec["train"],
                    "test":  rec["test"],
                    "combined_sharpe": round(score, 4),
                }
                logger.info(
                    f"★ New best  Sharpe={score:.3f}  tf={tf}  params={params}"
                )

    def to_status_dict(self) -> Dict:
        elapsed = (datetime.now() - self.started_at).seconds
        pct = (
            round(self.trial_count / self.total_trials * 100, 1)
            if self.total_trials else 0
        )
        return {
            "status":          self.status,
            "progress":        f"{self.trial_count}/{self.total_trials} ({pct}%)",
            "current_timeframe": self.current_tf,
            "best_score":      round(self.best_score, 4),
            "best_timeframe":  self.best_timeframe,
            "best_params":     self.best_params,
            "elapsed_seconds": elapsed,
        }

    def to_results_dict(self, top_n: int = 10) -> Dict:
        top = sorted(self.all_trials, key=lambda t: t["score"], reverse=True)[:top_n]
        return {
            "status":        self.status,
            "best_params":   self.best_params,
            "best_timeframe": self.best_timeframe,
            "best_score":    round(self.best_score, 4),
            "best_metrics":  self.best_metrics,
            "top_trials":    top,
            "total_trials":  self.trial_count,
        }


# ── Main optimizer ────────────────────────────────────────────────────────────

class StrategyOptimizer:
    def __init__(self, results_dir: str = "strategy_optimizer/results"):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)

        self._bt = Backtester()
        self._dm = DataManager()

        self._strategy_class: Optional[Type] = None
        self._param_ranges: Dict = {}
        self._state = OptimizationState()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def load_strategy(self, strategy_class: Type, param_ranges: Dict) -> str:
        self._strategy_class = strategy_class
        self._param_ranges = param_ranges
        self._state = OptimizationState()
        return (
            f"Loaded '{strategy_class.__name__}' with "
            f"{len(param_ranges)} optimisable parameters: "
            f"{list(param_ranges.keys())}"
        )

    def start_optimization(
        self,
        ticker: str,
        timeframes: List[str],
        n_trials: int = 100,
        min_trades: int = 10,
    ) -> str:
        if self._strategy_class is None:
            return "ERROR: No strategy loaded — call load_strategy first."
        if self._state.status == "running":
            return "Optimization is already running."

        self._stop_event.clear()
        self._state = OptimizationState()
        self._state.status = "running"
        self._state.total_trials = n_trials * len(timeframes)

        self._thread = threading.Thread(
            target=self._loop,
            args=(ticker, timeframes, n_trials, min_trades),
            daemon=True,
        )
        self._thread.start()

        return (
            f"Optimization started  |  ticker={ticker}  "
            f"timeframes={timeframes}  trials={n_trials}/tf  "
            f"total={n_trials * len(timeframes)}"
        )

    def stop(self) -> str:
        self._stop_event.set()
        self._state.status = "stopped"
        return "Stop signal sent — current trial will finish then halt."

    def get_status(self) -> Dict:
        return self._state.to_status_dict()

    def get_results(self, top_n: int = 10) -> Dict:
        return self._state.to_results_dict(top_n=top_n)

    def run_single(
        self,
        ticker: str,
        timeframe: str,
        params: Dict,
    ) -> Dict:
        """Run one backtest with fixed params — for spot-checking."""
        df = self._dm.fetch(ticker, timeframe)
        return self._bt.run(self._strategy_class, df, params)

    # ── Background loop ────────────────────────────────────────────────────────

    def _loop(
        self,
        ticker: str,
        timeframes: List[str],
        n_trials: int,
        min_trades: int,
    ):
        """Runs in a background thread.  Iterates timeframes (TimeframeAgent),
        then runs Optuna (ParamAgent) for each."""
        try:
            for tf in timeframes:
                if self._stop_event.is_set():
                    break

                self._state.current_tf = tf
                logger.info(f"TimeframeAgent: starting {ticker} @ {tf}")

                try:
                    df = self._dm.fetch(ticker, tf)
                    windows = self._dm.walk_forward_windows(df)
                except Exception as exc:
                    logger.error(f"Data error for {tf}: {exc}")
                    continue

                if not windows:
                    logger.warning(f"No walk-forward windows for {tf} — skipping")
                    continue

                self._run_param_agent(tf, windows, n_trials, min_trades)

            self._state.status = "completed"
            self._save_results(ticker)
            logger.info("Optimization complete.")

        except Exception as exc:
            logger.exception(f"Optimization loop crashed: {exc}")
            self._state.status = "error"

    def _run_param_agent(
        self,
        tf: str,
        windows: List[Tuple],
        n_trials: int,
        min_trades: int,
    ):
        """ParamAgent: Optuna study for one timeframe."""
        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=42, n_startup_trials=20),
            pruner=optuna.pruners.MedianPruner(),
        )

        def objective(trial: optuna.Trial) -> float:
            if self._stop_event.is_set():
                raise optuna.exceptions.TrialPruned()

            params = self._suggest(trial)

            # Random walk-forward window (anti-curve-fitting)
            train_df, test_df = random.choice(windows)

            train_m = self._bt.run(self._strategy_class, train_df, params)
            if train_m["n_trades"] < min_trades:
                return -2.0   # penalise under-trading configs

            test_m = self._bt.run(self._strategy_class, test_df, params)

            # Score = average Sharpe on train + unseen test
            score = (train_m["sharpe"] + test_m["sharpe"]) / 2

            # Extra penalty: heavy drawdown on test set
            if test_m["max_dd_pct"] < -30:
                score -= 0.5

            self._state.record_trial(tf, params, train_m, test_m, score)
            return score

        try:
            study.optimize(
                objective,
                n_trials=n_trials,
                show_progress_bar=False,
                gc_after_trial=True,
            )
        except Exception as exc:
            logger.error(f"Optuna study failed for {tf}: {exc}")

    def _suggest(self, trial: optuna.Trial) -> Dict[str, Any]:
        """Translate PARAM_RANGES into Optuna suggestions."""
        params: Dict[str, Any] = {}
        for name, spec in self._param_ranges.items():
            ptype = spec[0]
            if ptype == "int":
                params[name] = trial.suggest_int(name, spec[1], spec[2])
            elif ptype == "float":
                params[name] = trial.suggest_float(name, spec[1], spec[2])
            elif ptype == "categorical":
                params[name] = trial.suggest_categorical(name, spec[1])
        return params

    def _save_results(self, ticker: str):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = getattr(self._strategy_class, "__name__", "strategy")
        path = self.results_dir / f"{name}_{ticker}_{ts}.json"
        data = self._state.to_results_dict(top_n=50)
        with open(path, "w") as fh:
            json.dump(data, fh, indent=2, default=str)
        logger.info(f"Results saved → {path}")
