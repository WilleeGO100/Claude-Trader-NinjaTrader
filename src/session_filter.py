"""
Session Filter Module
Controls when trading is allowed based on market session and time of day
"""

import logging
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


class SessionFilter:
    """Filters trading based on active market session"""

    def __init__(self, config: dict):
        rules = config.get('session_rules', {})
        self.enabled = rules.get('enabled', True)

        # NY session window
        self.ny_start = self._parse_time(rules.get('ny_open_start', '09:30'))
        self.ny_end = self._parse_time(rules.get('ny_open_end', '16:00'))

        # London/NY overlap window
        self.london_start = self._parse_time(rules.get('london_start', '03:00'))
        self.london_end = self._parse_time(rules.get('london_end', '12:00'))

        # Lunch avoidance
        self.avoid_lunch = rules.get('avoid_lunch', True)
        self.lunch_start = self._parse_time(rules.get('lunch_start', '12:00'))
        self.lunch_end = self._parse_time(rules.get('lunch_end', '13:00'))

        # Allowed sessions
        self.allowed_sessions = rules.get('allowed_sessions', ['ny_open', 'london_overlap'])

    def _parse_time(self, time_str: str) -> time:
        h, m = map(int, time_str.split(':'))
        return time(h, m)

    def _get_et_time(self, dt: Optional[datetime] = None) -> time:
        if dt is None:
            dt = datetime.now(ET)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=ET)
        return dt.astimezone(ET).time()

    def is_trading_allowed(self, dt: Optional[datetime] = None) -> tuple[bool, str]:
        """
        Check if trading is allowed at the given time.

        Returns:
            (allowed, reason) tuple
        """
        if not self.enabled:
            return True, "Session filter disabled"

        t = self._get_et_time(dt)

        # Check lunch avoidance first
        if self.avoid_lunch and self.lunch_start <= t < self.lunch_end:
            return False, f"Lunch hour ({self.lunch_start.strftime('%H:%M')}-{self.lunch_end.strftime('%H:%M')} ET)"

        in_ny = self.ny_start <= t < self.ny_end
        in_london = self.london_start <= t < self.london_end

        if 'ny_open' in self.allowed_sessions and in_ny:
            return True, "NY session"
        if 'london_overlap' in self.allowed_sessions and in_london:
            return True, "London/NY session"

        active = []
        if in_ny:
            active.append("NY")
        if in_london:
            active.append("London")
        session_str = "/".join(active) if active else "off-hours"

        return False, f"Outside allowed sessions (currently {session_str}, ET time {t.strftime('%H:%M')})"
