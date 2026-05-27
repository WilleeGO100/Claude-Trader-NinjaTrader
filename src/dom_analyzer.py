"""
DOM Analyzer
Reads DOMSnapshot.csv (written by DOMSnapshot.cs) and computes:
  - Bid/ask imbalance ratio
  - Wall detection (large resting orders)
  - Nearest bid wall below current price (support for longs)
  - Nearest ask wall above current price (resistance for shorts)
"""

import logging
import pandas as pd
from pathlib import Path
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

DOM_FILE = Path('data/DOMSnapshot.csv')


class DOMAnalyzer:

    def __init__(self, wall_threshold: int = 15):
        self.wall_threshold = wall_threshold

    def _load(self) -> Optional[pd.DataFrame]:
        if not DOM_FILE.exists():
            return None
        try:
            df = pd.read_csv(DOM_FILE)
            if df.empty or 'Side' not in df.columns:
                return None
            df['Price'] = pd.to_numeric(df['Price'], errors='coerce').fillna(0)
            df['Size']  = pd.to_numeric(df['Size'],  errors='coerce').fillna(0)
            return df
        except Exception as e:
            logger.debug(f"DOMAnalyzer load error: {e}")
            return None

    def get_context(self, current_price: float) -> Dict[str, Any]:
        empty = {
            'available':       False,
            'imbalance_ratio': 1.0,
            'imbalance_side':  'neutral',
            'bid_walls':       [],
            'ask_walls':       [],
            'nearest_bid_wall': None,
            'nearest_ask_wall': None,
            'prompt_section':  '',
        }

        df = self._load()
        if df is None or df.empty:
            return empty

        bids = df[df['Side'] == 'BID'].copy()
        asks = df[df['Side'] == 'ASK'].copy()

        # ── Imbalance ratio ──────────────────────────────────────────
        total_bid = bids['Size'].sum()
        total_ask = asks['Size'].sum()
        if total_ask > 0:
            imbalance = round(total_bid / total_ask, 2)
        else:
            imbalance = 1.0

        if imbalance > 1.3:
            imbalance_side = 'bid_heavy'    # more buyers sitting
        elif imbalance < 0.7:
            imbalance_side = 'ask_heavy'    # more sellers sitting
        else:
            imbalance_side = 'balanced'

        # ── Wall detection ───────────────────────────────────────────
        bid_walls = bids[bids['Size'] >= self.wall_threshold][['Price', 'Size']].to_dict('records')
        ask_walls = asks[asks['Size'] >= self.wall_threshold][['Price', 'Size']].to_dict('records')

        # Sort: bid walls descending (closest below), ask walls ascending (closest above)
        bid_walls = sorted(bid_walls, key=lambda x: x['Price'], reverse=True)
        ask_walls = sorted(ask_walls, key=lambda x: x['Price'])

        # Nearest bid wall BELOW current price
        nearest_bid = next((w for w in bid_walls if w['Price'] < current_price), None)
        # Nearest ask wall ABOVE current price
        nearest_ask = next((w for w in ask_walls if w['Price'] > current_price), None)

        # ── Prompt section ───────────────────────────────────────────
        lines = ["DOM ORDER BOOK:"]
        lines.append(f"  Bid/Ask imbalance: {imbalance:.2f} ({imbalance_side.replace('_', ' ')})")

        if nearest_bid:
            dist = round(current_price - nearest_bid['Price'], 2)
            lines.append(f"  Nearest bid wall: {nearest_bid['Price']:.2f} ({nearest_bid['Size']}c) — {dist:.1f}pts below (LONG support)")
        else:
            lines.append(f"  Nearest bid wall: None detected")

        if nearest_ask:
            dist = round(nearest_ask['Price'] - current_price, 2)
            lines.append(f"  Nearest ask wall: {nearest_ask['Price']:.2f} ({nearest_ask['Size']}c) — {dist:.1f}pts above (SHORT resistance)")
        else:
            lines.append(f"  Nearest ask wall: None detected")

        lines.append("")

        return {
            'available':        True,
            'imbalance_ratio':  imbalance,
            'imbalance_side':   imbalance_side,
            'bid_walls':        bid_walls,
            'ask_walls':        ask_walls,
            'nearest_bid_wall': nearest_bid,
            'nearest_ask_wall': nearest_ask,
            'prompt_section':   '\n'.join(lines),
        }

    def get_quality_score(self, direction: str, context: Dict[str, Any], current_price: float) -> float:
        """Returns DOM quality contribution (0.0-0.10)"""
        if not context.get('available'):
            return 0.05  # neutral — no data

        score   = 0.0
        near_bid = context.get('nearest_bid_wall')
        near_ask = context.get('nearest_ask_wall')
        imbal    = context.get('imbalance_side', 'balanced')

        if direction == 'LONG':
            # Bid wall within 5pts below = strong support
            if near_bid and abs(current_price - near_bid['Price']) <= 5:
                score += 0.10
            elif near_bid and abs(current_price - near_bid['Price']) <= 15:
                score += 0.06
            # Imbalance favoring bids
            if imbal == 'bid_heavy':
                score += 0.03
        else:  # SHORT
            # Ask wall within 5pts above = strong resistance
            if near_ask and abs(near_ask['Price'] - current_price) <= 5:
                score += 0.10
            elif near_ask and abs(near_ask['Price'] - current_price) <= 15:
                score += 0.06
            # Imbalance favoring asks
            if imbal == 'ask_heavy':
                score += 0.03

        return min(score, 0.10)
