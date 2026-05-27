"""
Order Flow Reader
Reads TimeAndSales.csv (written by TickLogger.cs) and computes:
  - Bar delta (net aggressive buying minus selling)
  - Cumulative delta (session running total)
  - Tape speed (prints per second)
  - Large print detection (institutional footprints)
  - Absorption detection (price stuck at level despite heavy volume)

Side coding from TickLogger.cs:
  A = Ask-side print (buyer aggressive)
  B = Bid-side print (seller aggressive)
  U = Unknown
  Flag L = large print (>= threshold)
"""

import logging
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

TAS_FILE = Path('data/TimeAndSales.csv')


class OrderFlowReader:

    def __init__(self, large_print_threshold: int = 10):
        self.large_print_threshold = large_print_threshold

    def _load(self) -> Optional[pd.DataFrame]:
        if not TAS_FILE.exists():
            return None
        try:
            df = pd.read_csv(TAS_FILE)
            if df.empty or 'Time' not in df.columns:
                return None
            df['Time'] = pd.to_datetime(df['Time'], errors='coerce')
            df = df.dropna(subset=['Time'])
            df['Size'] = pd.to_numeric(df['Size'], errors='coerce').fillna(0)
            df['Price'] = pd.to_numeric(df['Price'], errors='coerce').fillna(0)
            return df.sort_values('Time').reset_index(drop=True)
        except Exception as e:
            logger.debug(f"OrderFlowReader load error: {e}")
            return None

    def get_context(self, current_price: float, window_seconds: int = 60) -> Dict[str, Any]:
        """
        Compute all order flow metrics for the current moment.
        window_seconds: how far back to look for bar delta / tape speed.
        """
        empty = {
            'available':        False,
            'bar_delta':        0,
            'cum_delta':        0,
            'delta_direction':  'unknown',
            'tape_speed':       0.0,
            'tape_trend':       'unknown',
            'large_prints':     [],
            'absorption':       False,
            'absorption_price': 0.0,
            'prompt_section':   '',
        }

        df = self._load()
        if df is None or df.empty:
            return empty

        now     = df['Time'].max()
        cutoff  = now - timedelta(seconds=window_seconds)
        recent  = df[df['Time'] >= cutoff].copy()

        if recent.empty:
            return empty

        # ── Bar delta ────────────────────────────────────────────────
        ask_vol = recent[recent['Side'] == 'A']['Size'].sum()
        bid_vol = recent[recent['Side'] == 'B']['Size'].sum()
        bar_delta = int(ask_vol - bid_vol)

        # ── Cumulative delta (full session) ──────────────────────────
        all_ask = df[df['Side'] == 'A']['Size'].sum()
        all_bid = df[df['Side'] == 'B']['Size'].sum()
        cum_delta = int(all_ask - all_bid)

        # ── Delta direction (is delta turning?) ──────────────────────
        half   = len(recent) // 2
        if half > 0:
            early_delta = int(recent.iloc[:half][recent['Side'] == 'A']['Size'].sum() -
                               recent.iloc[:half][recent['Side'] == 'B']['Size'].sum())
            late_delta  = int(recent.iloc[half:][recent['Side'] == 'A']['Size'].sum() -
                               recent.iloc[half:][recent['Side'] == 'B']['Size'].sum())
            if late_delta > early_delta + 20:
                delta_direction = 'turning_bullish'
            elif late_delta < early_delta - 20:
                delta_direction = 'turning_bearish'
            elif bar_delta > 50:
                delta_direction = 'bullish'
            elif bar_delta < -50:
                delta_direction = 'bearish'
            else:
                delta_direction = 'neutral'
        else:
            delta_direction = 'neutral'

        # ── Tape speed ───────────────────────────────────────────────
        duration  = max((recent['Time'].max() - recent['Time'].min()).total_seconds(), 1)
        tape_speed = round(len(recent) / duration, 2)

        # Compare first vs last quarter for tape trend
        q = max(len(recent) // 4, 1)
        early_speed = len(recent.iloc[:q]) / max((recent.iloc[:q]['Time'].max() - recent.iloc[:q]['Time'].min()).total_seconds(), 1)
        late_speed  = len(recent.iloc[-q:]) / max((recent.iloc[-q:]['Time'].max() - recent.iloc[-q:]['Time'].min()).total_seconds(), 1)
        if late_speed > early_speed * 1.5:
            tape_trend = 'accelerating'
        elif late_speed < early_speed * 0.5:
            tape_trend = 'slowing'
        else:
            tape_trend = 'steady'

        # ── Large prints near current price ──────────────────────────
        zone_tolerance = 20.0
        large = recent[
            (recent['Size'] >= self.large_print_threshold) &
            (abs(recent['Price'] - current_price) <= zone_tolerance)
        ].copy()

        large_prints = []
        for _, row in large.iterrows():
            large_prints.append({
                'price': row['Price'],
                'size':  int(row['Size']),
                'side':  'buy' if row['Side'] == 'A' else 'sell',
                'time':  row['Time'].strftime('%H:%M:%S'),
            })
        large_prints = large_prints[-5:]  # most recent 5

        # ── Absorption detection ─────────────────────────────────────
        # Price hasn't moved > 2pts but volume is heavy = absorption
        price_range = recent['Price'].max() - recent['Price'].min()
        total_vol   = recent['Size'].sum()
        absorption  = (price_range < 3.0 and total_vol > 200 and len(recent) > 30)
        absorption_price = round(recent['Price'].mean(), 2) if absorption else 0.0

        # ── Prompt section ───────────────────────────────────────────
        lines = ["ORDER FLOW (T&S):"]
        lines.append(f"  Bar delta ({window_seconds}s): {bar_delta:+d} | Cum delta (session): {cum_delta:+d}")
        lines.append(f"  Delta direction: {delta_direction.replace('_', ' ').upper()}")
        lines.append(f"  Tape: {tape_speed:.1f} prints/sec ({tape_trend})")

        if large_prints:
            prints_str = ', '.join(f"{p['size']}c @ {p['price']:.2f} ({p['side']})" for p in large_prints[-3:])
            lines.append(f"  Large prints: {prints_str}")
        else:
            lines.append(f"  Large prints: None near current price")

        if absorption:
            lines.append(f"  ABSORPTION DETECTED @ {absorption_price:.2f} — heavy volume, price not moving")

        lines.append("")

        return {
            'available':        True,
            'bar_delta':        bar_delta,
            'cum_delta':        cum_delta,
            'delta_direction':  delta_direction,
            'tape_speed':       tape_speed,
            'tape_trend':       tape_trend,
            'large_prints':     large_prints,
            'absorption':       absorption,
            'absorption_price': absorption_price,
            'prompt_section':   '\n'.join(lines),
        }

    def get_quality_score(self, direction: str, context: Dict[str, Any]) -> float:
        """
        Returns setup quality contribution (0.0-0.40) from order flow.
        Broken into 4 sub-factors matching the roadmap spec.
        """
        if not context.get('available'):
            return 0.10  # neutral — no data

        score = 0.0
        delta_dir = context['delta_direction']

        # Delta exhaustion (0.15)
        if direction == 'LONG':
            if delta_dir == 'turning_bullish':
                score += 0.15  # sellers running out at zone
            elif delta_dir == 'bullish':
                score += 0.08  # buyers dominant but not exhaustion
            elif delta_dir == 'bearish':
                score += 0.00
            else:
                score += 0.05  # neutral
        else:  # SHORT
            if delta_dir == 'turning_bearish':
                score += 0.15
            elif delta_dir == 'bearish':
                score += 0.08
            elif delta_dir == 'bullish':
                score += 0.00
            else:
                score += 0.05

        # Large print at zone (0.08)
        if context['large_prints']:
            score += 0.08

        # Tape absorption (0.07)
        if context['absorption']:
            score += 0.07

        # Tape slowing at zone (bonus, partial)
        if context['tape_trend'] == 'slowing':
            score += 0.05
        elif context['tape_trend'] == 'accelerating':
            score += 0.02

        return min(score, 0.25)
