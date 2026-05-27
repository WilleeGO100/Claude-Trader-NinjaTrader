"""
Setup Quality Scorer
Produces a deterministic 0.0-1.0 score based on measurable market factors.
Used alongside (not instead of) Claude's confidence to gate trade signals.
Avoids treating LLM confidence as calibrated probability.

Scoring factors:
  - FVG freshness (age in bars)
  - FVG size (larger = more significant)
  - EMA alignment with trade direction
  - HTF bias alignment
  - Session quality (RTH vs extended hours)
  - Reversal confirmation at zone
"""

import logging
from typing import Dict, Any, Optional  # noqa: F401

logger = logging.getLogger(__name__)


def compute_setup_quality(
    direction: str,
    fvg_context: Dict[str, Any],
    market_data: Dict[str, Any],
    htf_bias: str = 'unknown',
    session_active: bool = True,
    gamma_loader=None,
    order_flow_context: Optional[Dict] = None,
    dom_context: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Score a potential trade setup deterministically.

    Returns dict with:
        score       — 0.0 to 1.0
        breakdown   — per-factor scores
        gate_pass   — whether score meets minimum (0.50)
        description — human-readable summary
    """
    scores = {}

    direction_is_long  = direction == 'LONG'
    direction_is_short = direction == 'SHORT'

    # ── Factor 1: FVG freshness (0-0.20) ─────────────────────────────
    target_fvg = (
        fvg_context.get('nearest_bearish_fvg') if direction_is_long
        else fvg_context.get('nearest_bullish_fvg')
    )
    if target_fvg:
        age = target_fvg.get('age_bars', 0)
        # Fresh FVG (0-5 bars) = full score, degrades linearly to 0 at 50 bars
        freshness = max(0.0, 1.0 - age / 50.0)
        scores['fvg_freshness'] = round(freshness * 0.20, 3)
    else:
        scores['fvg_freshness'] = 0.0

    # ── Factor 2: FVG size (0-0.15) ──────────────────────────────────
    if target_fvg:
        gap_size = target_fvg.get('gap_size', 0)
        # 5pt = 0, 50pt+ = full score (capped)
        size_score = min(1.0, max(0.0, (gap_size - 5) / 45.0))
        scores['fvg_size'] = round(size_score * 0.15, 3)
    else:
        scores['fvg_size'] = 0.0

    # ── Factor 3: EMA alignment (0-0.25) ─────────────────────────────
    ema21  = market_data.get('ema21', 0)
    ema75  = market_data.get('ema75', 0)
    ema150 = market_data.get('ema150', 0)

    if direction_is_long:
        # All EMAs aligned bullish and price above EMA21
        current = fvg_context.get('current_price', 0)
        if ema21 > ema75 > ema150:
            scores['ema_alignment'] = 0.25
        elif ema21 > ema75:
            scores['ema_alignment'] = 0.15
        elif current > ema21:
            scores['ema_alignment'] = 0.08
        else:
            scores['ema_alignment'] = 0.0
    else:  # SHORT
        current = fvg_context.get('current_price', 0)
        if ema21 < ema75 < ema150:
            scores['ema_alignment'] = 0.25
        elif ema21 < ema75:
            scores['ema_alignment'] = 0.15
        elif current < ema21:
            scores['ema_alignment'] = 0.08
        else:
            scores['ema_alignment'] = 0.0

    # ── Factor 4: HTF bias alignment (0-0.20) ────────────────────────
    if htf_bias == 'unknown':
        scores['htf_alignment'] = 0.10  # neutral — partial credit
    elif (htf_bias == 'bullish' and direction_is_long) or (htf_bias == 'bearish' and direction_is_short):
        scores['htf_alignment'] = 0.20  # with trend
    elif htf_bias == 'neutral':
        scores['htf_alignment'] = 0.10
    else:
        scores['htf_alignment'] = 0.0   # counter-trend

    # ── Factor 5: Session quality (0-0.10) ───────────────────────────
    scores['session'] = 0.10 if session_active else 0.03

    # ── Factor 6: Reversal confirmation (0-0.10) ─────────────────────
    conf_key = 'long_confirmation' if direction_is_long else 'short_confirmation'
    confirmation = fvg_context.get(conf_key)
    if confirmation is None:
        scores['confirmation'] = 0.05   # no data = neutral
    elif confirmation.get('confirmed'):
        scores['confirmation'] = 0.10
    else:
        scores['confirmation'] = 0.0

    # ── Factor 7: Order flow delta (0-0.40) ──────────────────────────
    if order_flow_context and order_flow_context.get('available'):
        from src.order_flow_reader import OrderFlowReader
        scores['order_flow'] = OrderFlowReader().get_quality_score(direction, order_flow_context)
    else:
        scores['order_flow'] = 0.05  # neutral

    # ── Factor 8: DOM imbalance + walls (0-0.10) ─────────────────────
    if dom_context and dom_context.get('available'):
        from src.dom_analyzer import DOMAnalyzer
        scores['dom'] = DOMAnalyzer().get_quality_score(
            direction, dom_context, fvg_context.get('current_price', 0)
        )
    else:
        scores['dom'] = 0.05  # neutral

    # ── Factor 9: Gamma levels (0-0.10) ──────────────────────────────
    if gamma_loader and target_fvg:
        scores['gamma'] = gamma_loader.get_quality_score(
            direction, fvg_context.get('current_price', 0),
            target_fvg.get('bottom', 0), target_fvg.get('top', 0)
        )
    else:
        scores['gamma'] = 0.05  # neutral — no data

    # ── Total ─────────────────────────────────────────────────────────
    total = sum(scores.values())
    gate_pass = total >= 0.50

    factors_str = ' | '.join(f"{k}={v:.2f}" for k, v in scores.items())
    logger.info(f"Setup quality [{direction}]: {total:.2f} ({'PASS' if gate_pass else 'FAIL'}) — {factors_str}")

    return {
        'score':       round(total, 3),
        'breakdown':   scores,
        'gate_pass':   gate_pass,
        'description': f"Quality {total:.0%} — {', '.join(k for k, v in scores.items() if v == 0)}{'  weak' if not gate_pass else ''}",
    }
