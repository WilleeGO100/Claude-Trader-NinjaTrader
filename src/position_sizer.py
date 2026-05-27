"""
Position Sizer Module
Computes contract size, partial exit levels, and trailing stop points
based on Claude's confidence score and the configured sizing rules.
"""

import logging
import math
from typing import Dict, Any

logger = logging.getLogger(__name__)


class PositionSizer:
    """Determines position size and exit structure for each trade"""

    def __init__(self, config: Dict[str, Any]):
        sizing = config.get('position_sizing', {})
        self.base_contracts = sizing.get('base_contracts', 1)
        self.max_contracts = sizing.get('max_contracts', 3)
        self.scale_by_confidence = sizing.get('scale_by_confidence', True)

        # Confidence tiers sorted descending so first match wins
        raw_tiers = sizing.get('confidence_tiers', [
            {'min_confidence': 0.65, 'contracts': 1},
            {'min_confidence': 0.75, 'contracts': 2},
            {'min_confidence': 0.85, 'contracts': 3},
        ])
        self.tiers = sorted(raw_tiers, key=lambda t: t['min_confidence'], reverse=True)

        # Partial exit
        pe = sizing.get('partial_exit', {})
        self.partial_exit_enabled = pe.get('enabled', True)
        self.scale1_r_multiple = pe.get('scale1_r_multiple', 1.0)  # exit at 1R
        self.scale1_pct = pe.get('scale1_pct', 0.5)               # exit 50%

        # Trailing stop
        ts = sizing.get('trailing_stop', {})
        self.trail_enabled = ts.get('enabled', True)
        self.trail_points = ts.get('trail_points', 20)

    def get_contracts(self, confidence: float) -> int:
        """Return contract count for this confidence level"""
        if not self.scale_by_confidence:
            return self.base_contracts

        for tier in self.tiers:
            if confidence >= tier['min_confidence']:
                return min(tier['contracts'], self.max_contracts)

        return self.base_contracts

    def compute_trade_sizing(
        self,
        direction: str,
        entry: float,
        stop: float,
        target: float,
        confidence: float
    ) -> Dict[str, Any]:
        """
        Compute full trade sizing including partial exit and trail.

        Returns dict with:
            contracts       — total contracts to enter
            scale1_price    — price for first partial exit (0 = disabled)
            scale1_contracts— contracts to exit at scale1
            trail_points    — points to trail after scale1 (0 = disabled)
        """
        contracts = self.get_contracts(confidence)
        risk = abs(entry - stop)

        # Partial exit
        scale1_price = 0.0
        scale1_contracts = 0
        if self.partial_exit_enabled and contracts >= 2:
            reward_to_scale = risk * self.scale1_r_multiple
            if direction == 'LONG':
                scale1_price = entry + reward_to_scale
            else:
                scale1_price = entry - reward_to_scale
            scale1_contracts = max(1, math.floor(contracts * self.scale1_pct))

        # Trailing stop (only meaningful if we have contracts remaining after scale1)
        remaining = contracts - scale1_contracts
        trail = self.trail_points if (self.trail_enabled and remaining > 0 and scale1_contracts > 0) else 0

        sizing = {
            'contracts': contracts,
            'scale1_price': round(scale1_price, 2),
            'scale1_contracts': scale1_contracts,
            'trail_points': trail,
        }

        logger.info(
            f"Position sizing: {contracts} contracts | confidence={confidence:.2f} | "
            f"Scale1={scale1_price:.2f} ({scale1_contracts}c) | Trail={trail}pts"
        )
        return sizing
