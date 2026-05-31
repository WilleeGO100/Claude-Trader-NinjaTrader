"""
PineScript → Python (BaseStrategy) Translator
==============================================
Uses Claude (claude-haiku-4-5, cheap + fast) to translate PineScript strategies
into the BaseStrategy DSL used by the optimizer.

Single translation:
    translator = PineTranslator()
    result = translator.translate(pine_code)

Batch translation (folder of .pine / .txt files):
    results = translator.translate_folder("my_pine_strategies/")
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from typing import Optional

import anthropic
from dotenv import load_dotenv

# Load .env from the project root regardless of working directory
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")
logger = logging.getLogger(__name__)

# ── Prompt ─────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = dedent("""\
You are an expert at translating TradingView PineScript strategies into Python
strategies for the backtesting.py library, specifically using the BaseStrategy
helper class.

## BaseStrategy API reference

```python
from strategy_optimizer.strategies.base import BaseStrategy

class MyStrategy(BaseStrategy):
    # Class-level attributes = optimisable parameters (must have default values)
    fast = 10
    slow = 30

    def init(self):
        # Called once at startup. Use self.I() wrappers below:
        self.ema_f = self.ema(self.fast)       # EMA
        self.ema_s = self.ema(self.slow)       # EMA
        self.sma_  = self.sma(20)              # SMA
        self.wma_  = self.wma(20)              # WMA
        self.rsi_  = self.rsi(14)              # RSI (0–100)
        self.ml_   = self.macd_line(12, 26)    # MACD line
        self.ms_   = self.macd_signal_line(12, 26, 9)
        self.bbu_  = self.bb_upper(20, 2.0)    # Bollinger upper
        self.bbl_  = self.bb_lower(20, 2.0)    # Bollinger lower
        self.bbm_  = self.bb_mid(20)           # Bollinger mid (SMA)
        self.atr_  = self.atr(14)              # ATR
        self.stk_  = self.stochastic_k(14)     # Stochastic %K

    def next(self):
        # Called on every bar. Access indicator values with [-1] (latest).
        price = self.data.Close[-1]
        high  = self.data.High[-1]
        low   = self.data.Low[-1]

        # Crossover helpers (return bool)
        self.crossover(self.ema_f, self.ema_s)   # True when f crossed above s
        self.crossunder(self.ema_f, self.ema_s)  # True when f crossed below s

        # Entry orders
        self.buy(sl=price - 10, tp=price + 20)   # sl/tp are optional
        self.sell(sl=price + 10, tp=price - 20)

        # Position checks
        self.position          # current position (None if flat)
        self.position.is_long  # bool
        self.position.is_short # bool
        self.position.close()  # close current position

        # Historical bars: self.data.Close[-2] = previous bar's close, etc.
```

## PARAM_RANGES format
```python
PARAM_RANGES = {
    "fast":      ("int",   5,   50),      # integer range [low, high]
    "slow":      ("int",  20,  200),
    "threshold": ("float", 0.1, 5.0),     # float range
    "mode":      ("categorical", ["a", "b", "c"]),  # discrete
}
```

## Translation rules
1. `input.int(default, ...)` → class attribute with that default value; add to PARAM_RANGES
2. `input.float(default, ...)` → same
3. `input.bool(...)` → categorical ("true","false") or fix to one value
4. `input.string(...)` → categorical with the options list
5. `ta.ema(src, len)` → `self.ema(len)` (source is always close unless noted)
6. `ta.sma(src, len)` → `self.sma(len)`
7. `ta.rsi(src, len)` → `self.rsi(len)`
8. `ta.macd(src, f, s, sig)` → `self.macd_line(f,s)` + `self.macd_signal_line(f,s,sig)`
9. `ta.bb(src, len, mult)` → `self.bb_upper(len,mult)` + `self.bb_lower(len,mult)`
10. `ta.atr(len)` → `self.atr(len)`
11. `ta.stoch(h,l,c,len)` → `self.stochastic_k(len)`
12. `ta.crossover(a, b)` → `self.crossover(self.a_, self.b_)`
13. `ta.crossunder(a, b)` → `self.crossunder(self.a_, self.b_)`
14. `strategy.entry("Long", strategy.long)` → `self.buy(...)`
15. `strategy.entry("Short", strategy.short)` → `self.sell(...)`
16. `strategy.close(...)` → `self.position.close()`
17. `strategy.exit(...)` with stop/limit → set `sl=` and `tp=` on entry order
18. Stop-loss as % of price → add `stop_pct` float param, `sl = price * (1 - self.stop_pct/100)`
19. `barssince(...)`, `ta.highest(...)`, `ta.lowest(...)` → implement manually in next()
    using list comprehension over `self.data.Close` array
20. `security(...)` multi-timeframe → skip with a # TODO comment
21. Pine comments (`//`) → Python comments (`#`)
22. Anything you cannot translate confidently → add `# REVIEW: <reason>` comment

## PARAM_RANGES guidance
- Every `input.*` parameter becomes a PARAM_RANGES entry
- For int inputs: range = [max(1, default//3), default*4] (reasonable search space)
- For float inputs: range = [default*0.25, default*4] clamped to sensible limits
- Always add `stop_pct` float (0.1, 5.0) if the strategy uses a stop loss
- Always add `tp_pct` float (0.1, 10.0) if the strategy uses a take profit
- Add `atr_mult` float (0.5, 5.0) if the strategy uses ATR-based stops

## Output format
Return ONLY a JSON object with these keys:
{
  "class_name": "StrategyName",
  "python_code": "...full python code...",
  "param_count": 5,
  "flags": ["list of REVIEW items or warnings"],
  "confidence": 0.9
}
- confidence: 0.0–1.0 (how confident you are the translation is correct)
- flags: empty list if clean translation
- python_code must be a complete, runnable Python file starting with the import line
- Do NOT wrap in markdown fences inside the JSON string
""")

_USER_TEMPLATE = "Translate this PineScript strategy to Python:\n\n{pine_code}"


# ── Result dataclass ───────────────────────────────────────────────────────────

@dataclass
class TranslationResult:
    source_file: str = ""
    class_name: str = ""
    python_code: str = ""
    param_count: int = 0
    flags: list = field(default_factory=list)
    confidence: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.python_code and not self.error)

    @property
    def needs_review(self) -> bool:
        return self.confidence < 0.75 or bool(self.flags)

    def summary(self) -> str:
        if self.error:
            return f"  ✗ ERROR: {self.error}"
        icon = "⚠️ " if self.needs_review else "✓"
        review = f"  flags: {self.flags}" if self.flags else ""
        return (
            f"  {icon} {self.class_name}  "
            f"confidence={self.confidence:.0%}  "
            f"params={self.param_count}{review}"
        )


# ── Translator ─────────────────────────────────────────────────────────────────

class PineTranslator:
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "claude-haiku-4-5",
        output_dir: str = "strategy_optimizer/translated",
    ):
        self.client = anthropic.Anthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
        )
        self.model = model
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    # ── Public API ─────────────────────────────────────────────────────────────

    def translate(self, pine_code: str, source_name: str = "unknown") -> TranslationResult:
        """Translate a single PineScript strategy string."""
        result = TranslationResult(source_file=source_name)
        try:
            raw = self._call_claude(pine_code)
            parsed = self._parse_response(raw)
            result.class_name  = parsed.get("class_name", "UnnamedStrategy")
            result.python_code = parsed.get("python_code", "")
            result.param_count = int(parsed.get("param_count", 0))
            result.flags       = parsed.get("flags", [])
            result.confidence  = float(parsed.get("confidence", 0.0))
        except Exception as exc:
            result.error = str(exc)
            logger.error(f"Translation failed for {source_name}: {exc}")
        return result

    def translate_file(self, pine_path: str | Path, save: bool = True) -> TranslationResult:
        """Translate a .pine / .txt file and optionally save the .py output."""
        path = Path(pine_path)
        pine_code = path.read_text(encoding="utf-8", errors="replace")
        result = self.translate(pine_code, source_name=path.name)

        if save and result.ok:
            out_path = self.output_dir / (path.stem + ".py")
            out_path.write_text(result.python_code, encoding="utf-8")
            logger.info(f"Saved → {out_path}")

        return result

    def translate_folder(
        self,
        folder: str | Path,
        extensions: tuple = (".pine", ".txt", ".pinescript"),
        delay: float = 0.3,
    ) -> list[TranslationResult]:
        """
        Translate every Pine strategy file in a folder.
        Saves .py files to strategy_optimizer/translated/.
        Prints a summary report when done.

        Args:
            folder:     Path containing .pine (or .txt) files.
            extensions: File extensions to process.
            delay:      Seconds between API calls (rate-limit safety).
        """
        folder = Path(folder)
        files = [f for f in folder.iterdir() if f.suffix.lower() in extensions]

        if not files:
            print(f"No Pine files found in {folder} (looked for {extensions})")
            return []

        print(f"Found {len(files)} strategies to translate…\n")
        results: list[TranslationResult] = []

        for i, f in enumerate(files, 1):
            print(f"[{i}/{len(files)}] {f.name}", end="  ", flush=True)
            r = self.translate_file(f)
            results.append(r)
            print(r.summary())
            if i < len(files):
                time.sleep(delay)

        self._print_batch_report(results)
        self._save_batch_manifest(results, folder)
        return results

    # ── Internal ────────────────────────────────────────────────────────────────

    def _call_claude(self, pine_code: str) -> str:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _USER_TEMPLATE.format(pine_code=pine_code)}],
        )
        return resp.content[0].text

    def _parse_response(self, raw: str) -> dict:
        """Extract JSON from Claude's response, tolerating minor formatting issues."""
        # Strip markdown fences if present
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`")

        # Find the outermost { ... }
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("Claude returned no JSON object")

        return json.loads(raw[start:end])

    @staticmethod
    def _print_batch_report(results: list[TranslationResult]):
        ok       = [r for r in results if r.ok and not r.needs_review]
        review   = [r for r in results if r.ok and r.needs_review]
        failed   = [r for r in results if not r.ok]

        print("\n" + "═" * 60)
        print(f"  BATCH TRANSLATION REPORT")
        print("═" * 60)
        print(f"  ✓ Clean translations : {len(ok)}")
        print(f"  ⚠️  Needs review       : {len(review)}")
        print(f"  ✗ Failed             : {len(failed)}")
        print("═" * 60)

        if review:
            print("\n  Strategies needing a quick look:")
            for r in review:
                print(f"    • {r.source_file} → {r.class_name}")
                for flag in r.flags:
                    print(f"        ↳ {flag}")

        if failed:
            print("\n  Failed (retry manually):")
            for r in failed:
                print(f"    • {r.source_file}: {r.error}")

        print(f"\n  Output folder: strategy_optimizer/translated/\n")

    def _save_batch_manifest(self, results: list[TranslationResult], source_folder: Path):
        manifest = []
        for r in results:
            manifest.append({
                "source":     r.source_file,
                "class_name": r.class_name,
                "ok":         r.ok,
                "confidence": r.confidence,
                "flags":      r.flags,
                "params":     r.param_count,
                "py_file":    (r.class_name + ".py") if r.ok else None,
            })
        out = self.output_dir / "translation_manifest.json"
        with open(out, "w") as f:
            json.dump(manifest, f, indent=2)
        logger.info(f"Manifest saved → {out}")
