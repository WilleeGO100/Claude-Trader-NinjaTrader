# Strategy Optimizer — Quick Start

## 1. Install dependencies

```powershell
# From project root, with .venv active
pip install -r strategy_optimizer/requirements.txt
```

## 2. Register the MCP server with Claude Code

Create (or append to) `.claude/mcp.json` in the project root:

```json
{
  "mcpServers": {
    "strategy-optimizer": {
      "command": "python",
      "args": ["-m", "strategy_optimizer.mcp_server"],
      "cwd": "C:/Users/jwmar/Claude-Trader-NinjaTrader"
    }
  }
}
```

Then restart Claude Code — the tools will appear automatically.

## 3. Translating your PineScript strategies

### Single strategy (via Claude / MCP)
Tell Claude: *"translate this PineScript strategy"* and paste the code.
Claude calls `translate_pinescript()`, returns Python code + flags.
Then tell Claude: *"now load and optimize it on SPY"*.

### Batch (50 strategies at once)
```powershell
# Put all your .pine files in one folder, then:
python -m strategy_optimizer.translate_batch  C:\path\to\your\pine\files\

# With validation (checks each .py actually loads):
python -m strategy_optimizer.translate_batch  C:\path\to\your\pine\files\ --validate
```
Translated `.py` files land in `strategy_optimizer/translated/`.
A `translation_manifest.json` lists pass/fail/confidence for every file.

### What gets flagged for review
- `security()` calls (multi-timeframe) — skipped with TODO comment
- Custom Pine functions with no obvious Python equivalent
- `barssince()` / `ta.highest()` / `ta.lowest()` lookbacks — translated manually
- Confidence < 75%

## 4. Usage workflow (tell Claude)

```
1. list_example_strategies                    ← see what's built in
2. get_example_strategy("ema_crossover")      ← fetch code
3. load_strategy(<code>)                      ← load it
4. start_optimization("SPY", "15m,1h,4h", n_trials=150)
5. get_optimization_status()                  ← poll until completed
6. get_results(top_n=5)                       ← see winners
7. run_single_backtest("SPY","1h",'{"fast":12,"slow":45}')
```

## 4. Writing your own strategy

Copy an example and modify it.  Key rules:

1. Class **must** inherit from `BaseStrategy` (or `backtesting.Strategy`).
2. Define `PARAM_RANGES` as a module-level dict — Optuna uses this to know
   what to search.  Omit a parameter to keep it fixed.
3. Available helpers inside `init()` / `next()`:
   - `self.ema(period)`, `self.sma(period)`, `self.wma(period)`
   - `self.rsi(period)`, `self.macd_line()`, `self.macd_signal_line()`
   - `self.atr(period)`, `self.bb_upper()`, `self.bb_lower()`, `self.bb_mid()`
   - `self.stochastic_k(period)`
   - `self.crossover(a, b)`, `self.crossunder(a, b)`
   - All standard `backtesting.py` methods: `self.buy()`, `self.sell()`,
     `self.position.close()`, `self.data.Close[-1]`, etc.

## 5. Translating PineScript

Rough mapping:

| PineScript                  | BaseStrategy equivalent                      |
|-----------------------------|----------------------------------------------|
| `ta.ema(close, 20)`         | `self.ema(20)`                               |
| `ta.rsi(close, 14)`         | `self.rsi(14)`                               |
| `ta.macd(close,12,26,9)`    | `self.macd_line(12,26)` + `self.macd_signal_line(12,26,9)` |
| `ta.bb(close,20,2)`         | `self.bb_upper(20,2)` / `self.bb_lower(20,2)`|
| `ta.crossover(a, b)`        | `self.crossover(a, b)`                       |
| `strategy.entry("L", long)` | `self.buy(sl=..., tp=...)`                   |
| `strategy.close("L")`       | `self.position.close()`                      |

## 6. Anti-overfitting design

Every Optuna trial:
- Picks a **random walk-forward window** from the full dataset
- Runs the strategy on the **training segment** and the **unseen test segment**
- Scores `(train_sharpe + test_sharpe) / 2`

Parameters that only work on the training data get a low score and Optuna
stops suggesting them.  Only generalisable parameters rise to the top.

## 7. Supported tickers / timeframes

| Timeframes | Notes |
|------------|-------|
| 1m, 5m, 15m, 30m | yfinance limit: last 60 days max |
| 1h | ~2 years |
| 4h | resampled from 1h (~2 years) |
| 1d | 5 years |
| 1wk | 10 years |

Ticker examples: `SPY`, `QQQ`, `AAPL`, `NQ=F`, `ES=F`, `BTC-USD`, `EURUSD=X`
