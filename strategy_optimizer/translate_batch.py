"""
Batch Pine→Python translator CLI
=================================
Usage:
    python -m strategy_optimizer.translate_batch  <folder_of_pine_files>

    # Translate everything in a folder
    python -m strategy_optimizer.translate_batch  my_pine_strategies/

    # Also immediately validate each translation imports cleanly
    python -m strategy_optimizer.translate_batch  my_pine_strategies/ --validate

Translated .py files land in:  strategy_optimizer/translated/
Manifest JSON (pass/fail list): strategy_optimizer/translated/translation_manifest.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy_optimizer.pine_translator import PineTranslator
from strategy_optimizer.strategies.base import load_strategy_from_code


def validate_translated(translated_dir: Path):
    """Try to exec every translated .py and report errors."""
    py_files = sorted(translated_dir.glob("*.py"))
    print(f"\nValidating {len(py_files)} translated files…")
    ok = bad = 0
    for f in py_files:
        if f.name == "__init__.py":
            continue
        try:
            code = f.read_text(encoding="utf-8")
            cls, ranges = load_strategy_from_code(code)
            print(f"  ✓  {f.name}  → {cls.__name__}  ({len(ranges)} params)")
            ok += 1
        except Exception as exc:
            print(f"  ✗  {f.name}  → {exc}")
            bad += 1
    print(f"\n  Passed: {ok}  Failed: {bad}\n")


def main():
    parser = argparse.ArgumentParser(description="Batch translate PineScript strategies to Python")
    parser.add_argument("folder", help="Folder containing .pine / .txt strategy files")
    parser.add_argument(
        "--validate",
        action="store_true",
        help="After translating, validate each output file can be imported",
    )
    parser.add_argument(
        "--ext",
        default=".pine,.txt,.pinescript",
        help="Comma-separated file extensions to process (default: .pine,.txt,.pinescript)",
    )
    args = parser.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        print(f"ERROR: folder not found: {folder}")
        sys.exit(1)

    extensions = tuple(e.strip() for e in args.ext.split(","))

    translator = PineTranslator()
    translator.translate_folder(folder, extensions=extensions)

    if args.validate:
        validate_translated(translator.output_dir)


if __name__ == "__main__":
    main()
