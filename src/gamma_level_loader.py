"""
Gamma Level Loader
Reads data/gamma_levels.json and provides gamma context for Claude's prompt
and setup_quality scoring.

Update gamma_levels.json before each session:
  - Market Chameleon (free): QQQ OI → multiply levels x 40 for NQ
  - SpotGamma ($50/mo): direct NQ gamma levels
  - Tradytics ($20/mo): QQQ proxy

Key concepts:
  Gamma Flip  — price where MM hedging switches from damping to amplifying moves
                Above flip = range/mean-revert regime
                Below flip = trending/momentum regime
  Call Wall   — strike with most call OI, acts as ceiling/target
  Put Wall    — strike with most put OI, acts as floor/target
"""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

GAMMA_FILE = Path('data/gamma_levels.json')


class GammaLevelLoader:

    def __init__(self):
        self._data: Optional[Dict] = None
        self._load()

    def _load(self):
        try:
            if not GAMMA_FILE.exists():
                self._data = None
                return
            with open(GAMMA_FILE) as f:
                self._data = json.load(f)
        except Exception as e:
            logger.warning(f"Gamma: could not load gamma_levels.json: {e}")
            self._data = None

    def reload(self):
        """Reload from disk — call at session start"""
        self._load()

    def is_available(self) -> bool:
        if not self._data:
            return False
        flip = self._data.get('gamma_flip', 0)
        return bool(flip)

    def get_levels(self) -> Dict[str, Any]:
        if not self.is_available():
            return {'available': False}
        d = self._data
        return {
            'available':   True,
            'date':        d.get('date', ''),
            'source':      d.get('source', 'manual'),
            'gamma_flip':  float(d.get('gamma_flip', 0)),
            'call_wall':   float(d.get('call_wall', 0)),
            'put_wall':    float(d.get('put_wall', 0)),
            'notes':       d.get('notes', ''),
        }

    def get_regime(self, current_price: float) -> str:
        """Returns 'positive', 'negative', or 'unknown' gamma regime"""
        levels = self.get_levels()
        if not levels['available'] or not levels['gamma_flip']:
            return 'unknown'
        return 'positive' if current_price >= levels['gamma_flip'] else 'negative'

    def get_quality_score(self, direction: str, current_price: float, fvg_bottom: float, fvg_top: float) -> float:
        """
        Returns setup quality contribution (0.0 - 0.10) from gamma alignment.
        Injected into setup_quality.py as Factor 9.
        """
        levels = self.get_levels()
        if not levels['available']:
            return 0.05  # neutral — no data

        flip       = levels['gamma_flip']
        call_wall  = levels['call_wall']
        put_wall   = levels['put_wall']
        fvg_mid    = (fvg_bottom + fvg_top) / 2
        tolerance  = 15.0

        score = 0.0

        # FVG near gamma flip — strongest confluence
        if abs(fvg_mid - flip) <= tolerance:
            score = 0.10
        # SHORT setup near call wall
        elif direction == 'SHORT' and call_wall and abs(fvg_mid - call_wall) <= tolerance:
            score = 0.08
        # LONG setup near put wall
        elif direction == 'LONG' and put_wall and abs(fvg_mid - put_wall) <= tolerance:
            score = 0.08
        # Regime alignment
        elif direction == 'LONG' and current_price >= flip:
            score = 0.05  # positive gamma = mean-revert friendly for longs
        elif direction == 'SHORT' and current_price < flip:
            score = 0.05  # negative gamma = trending friendly for shorts
        else:
            score = 0.02

        return score

    def get_prompt_section(self, current_price: float) -> str:
        levels = self.get_levels()
        if not levels['available']:
            return ""

        flip      = levels['gamma_flip']
        call_wall = levels['call_wall']
        put_wall  = levels['put_wall']
        regime    = self.get_regime(current_price)

        regime_desc = {
            'positive': 'POSITIVE GAMMA — MMs damping moves, range/mean-revert regime. Counter-trend setups more viable.',
            'negative': 'NEGATIVE GAMMA — MMs amplifying moves, trending regime. Favor trend-following. Counter-trend needs extra confluence.',
            'unknown':  'Regime unknown',
        }[regime]

        lines = [
            f"GAMMA LEVELS ({levels['source'].upper()}):",
            f"  Gamma Flip: {flip:.0f} — price is {'ABOVE' if regime == 'positive' else 'BELOW'} flip ({current_price:.2f})",
        ]
        if call_wall:
            lines.append(f"  Call Wall:  {call_wall:.0f} (resistance/ceiling)")
        if put_wall:
            lines.append(f"  Put Wall:   {put_wall:.0f} (support/floor)")
        lines.append(f"  Regime: {regime_desc}")
        lines.append("")

        return "\n".join(lines)
