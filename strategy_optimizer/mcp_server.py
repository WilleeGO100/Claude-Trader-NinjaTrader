"""
Strategy Optimizer MCP Server
==============================
Exposes strategy optimization tools to Claude via the MCP protocol.

Start manually:
    python -m strategy_optimizer.mcp_server

Or register in .claude/mcp.json (see project README / OPTIMIZER_QUICKSTART.md).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Optional

# Ensure project root is on path when invoked directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP

from strategy_optimizer.optimizer import StrategyOptimizer
from strategy_optimizer.pine_translator import PineTranslator
from strategy_optimizer.strategies.base import (
    EXAMPLE_STRATEGIES,
    load_strategy_from_code,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("strategy-optimizer-mcp")

mcp = FastMCP(
    "strategy-optimizer",
    instructions=(
        "Use these tools to load trading strategies, run parameter optimisation "
        "across timeframes, and retrieve ranked results. "
        "Workflow: get_example_strategy → load_strategy → start_optimization → "
        "get_optimization_status (poll) → get_results."
    ),
)

_opt = StrategyOptimizer()


# ─────────────────────────────────────────────────────────────────────────────
# Tool definitions
# ─────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def translate_pinescript(pine_code: str) -> str:
    """
    Translate a PineScript strategy into Python (BaseStrategy DSL) ready for optimization.

    Paste the full PineScript code. Returns:
      • Translated Python code you can immediately pass to load_strategy()
      • Confidence score (0–1)
      • List of any flags / items needing manual review

    After translation, call load_strategy(python_code) then start_optimization().

    Args:
        pine_code: Full PineScript strategy source code.
    """
    try:
        translator = PineTranslator()
        result = translator.translate(pine_code, source_name="mcp_input")
        if result.error:
            return f"Translation failed: {result.error}"

        out = {
            "class_name":  result.class_name,
            "confidence":  result.confidence,
            "param_count": result.param_count,
            "flags":       result.flags,
            "needs_review": result.needs_review,
            "python_code": result.python_code,
        }
        return json.dumps(out, indent=2)
    except Exception as exc:
        return f"Error: {exc}"


@mcp.tool()
def list_example_strategies() -> str:
    """
    List the built-in example strategies available for immediate use.
    Returns names and one-line descriptions.
    """
    lines = [f"• {name}: {info['description']}" for name, info in EXAMPLE_STRATEGIES.items()]
    return "\n".join(lines)


@mcp.tool()
def get_example_strategy(name: str) -> str:
    """
    Return the full Python code for a built-in example strategy.

    Args:
        name: Strategy name (from list_example_strategies).
              Options: ema_crossover | rsi_mean_reversion | macd_trend | bb_breakout
    """
    info = EXAMPLE_STRATEGIES.get(name)
    if info is None:
        avail = list(EXAMPLE_STRATEGIES.keys())
        return f"Unknown strategy '{name}'. Available: {avail}"
    return info["code"]


@mcp.tool()
def load_strategy(code: str) -> str:
    """
    Load a trading strategy from Python source code.

    The code must:
      1. Define a class that inherits from BaseStrategy (or backtesting.Strategy).
      2. Optionally define PARAM_RANGES = { 'param': ('int'|'float'|'categorical', ...) }
         to control what Optuna searches over.

    If PARAM_RANGES is omitted the optimizer runs with default parameter values only.

    Example minimal strategy:
        from strategy_optimizer.strategies.base import BaseStrategy

        class MyCross(BaseStrategy):
            fast = 10
            slow = 30

            def init(self):
                self.f = self.ema(self.fast)
                self.s = self.ema(self.slow)

            def next(self):
                if self.crossover(self.f, self.s):
                    self.buy()
                elif self.crossunder(self.f, self.s):
                    self.position.close()

        PARAM_RANGES = {
            'fast': ('int', 5, 50),
            'slow': ('int', 20, 200),
        }

    Args:
        code: Full Python source code string.
    """
    try:
        strategy_class, param_ranges = load_strategy_from_code(code)
        msg = _opt.load_strategy(strategy_class, param_ranges)
        return f"✓ {msg}"
    except Exception as exc:
        return f"✗ Load failed: {exc}"


@mcp.tool()
def start_optimization(
    ticker: str,
    timeframes: str = "15m,1h,4h",
    n_trials: int = 100,
    min_trades: int = 10,
) -> str:
    """
    Start the background optimization loop.

    Two agents run sequentially per timeframe:
      • TimeframeAgent: iterates the timeframe list in order.
      • ParamAgent (Optuna TPE): runs n_trials per timeframe, each trial
        drawing a random walk-forward window to prevent curve-fitting.

    Results are auto-saved to strategy_optimizer/results/ on completion.

    Args:
        ticker:     Symbol to fetch data for.
                    Examples: 'SPY', 'QQQ', 'NQ=F', 'ES=F', 'BTC-USD', 'AAPL'
        timeframes: Comma-separated list of timeframes to test.
                    Valid: 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1wk
                    Default: "15m,1h,4h"
        n_trials:   Number of Optuna trials per timeframe.
                    Higher = better coverage but slower.  Default: 100
        min_trades: Minimum number of trades required for a trial to be scored.
                    Prevents degenerate 1-trade 'perfect' results.  Default: 10
    """
    tf_list = [t.strip() for t in timeframes.split(",") if t.strip()]
    return _opt.start_optimization(
        ticker=ticker,
        timeframes=tf_list,
        n_trials=n_trials,
        min_trades=min_trades,
    )


@mcp.tool()
def get_optimization_status() -> str:
    """
    Return current optimization progress and the best result found so far.
    Poll this tool while status == 'running'.
    """
    return json.dumps(_opt.get_status(), indent=2)


@mcp.tool()
def get_results(top_n: int = 10) -> str:
    """
    Return the top N optimization results, ranked by combined train+test Sharpe.

    Args:
        top_n: Number of results to return (default 10).
    """
    return json.dumps(_opt.get_results(top_n=top_n), indent=2, default=str)


@mcp.tool()
def stop_optimization() -> str:
    """
    Send a stop signal to the running optimization loop.
    The current trial finishes, then the loop halts and saves results.
    """
    return _opt.stop()


@mcp.tool()
def run_single_backtest(
    ticker: str,
    timeframe: str,
    params_json: str = "{}",
) -> str:
    """
    Run one backtest with explicit parameter values — useful for spot-checking
    or validating the best result from get_results().

    Args:
        ticker:      Symbol (e.g. 'SPY', 'NQ=F').
        timeframe:   Timeframe string (e.g. '1h').
        params_json: JSON object of parameter overrides.
                     Example: '{"fast": 12, "slow": 45, "stop_atr": 2.5}'
                     Pass '{}' to use strategy defaults.
    """
    if _opt._strategy_class is None:
        return "ERROR: No strategy loaded — call load_strategy first."
    try:
        params = json.loads(params_json)
        metrics = _opt.run_single(ticker, timeframe, params)
        return json.dumps(metrics, indent=2)
    except Exception as exc:
        return f"ERROR: {exc}"


@mcp.tool()
def list_saved_results() -> str:
    """
    List all saved optimization result files (JSON) in strategy_optimizer/results/.
    """
    rd = Path("strategy_optimizer/results")
    if not rd.exists():
        return "No results directory found."
    files = sorted(rd.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not files:
        return "No saved results yet."
    lines = [f"{f.name}  ({round(f.stat().st_size/1024, 1)} KB)" for f in files]
    return "\n".join(lines)


@mcp.tool()
def load_saved_result(filename: str) -> str:
    """
    Read a previously saved optimization result.

    Args:
        filename: File name from list_saved_results (e.g. 'EMACrossover_SPY_20250530_120000.json').
    """
    path = Path("strategy_optimizer/results") / filename
    if not path.exists():
        return f"File not found: {filename}"
    with open(path) as fh:
        data = json.load(fh)
    return json.dumps(data, indent=2)


@mcp.tool()
def generate_synthetic_backtest(
    n_bars: int = 1000,
    start_price: float = 100.0,
    params_json: str = "{}",
) -> str:
    """
    Run the loaded strategy against freshly generated synthetic (GBM) data.
    Useful for confirming a strategy isn't purely over-fit to historical patterns.

    Args:
        n_bars:       Number of synthetic bars to generate (default 1000).
        start_price:  Starting price for synthetic series (default 100.0).
        params_json:  JSON parameter overrides (default '{}').
    """
    if _opt._strategy_class is None:
        return "ERROR: No strategy loaded — call load_strategy first."
    try:
        params = json.loads(params_json)
        df = _opt._dm.generate_synthetic(n_bars=n_bars, start_price=start_price)
        metrics = _opt._bt.run(_opt._strategy_class, df, params)
        return json.dumps(metrics, indent=2)
    except Exception as exc:
        return f"ERROR: {exc}"


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info("Strategy Optimizer MCP server starting…")
    mcp.run()
