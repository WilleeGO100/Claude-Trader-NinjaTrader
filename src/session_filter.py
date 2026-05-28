"""
Session Filter Module
Controls when trading is allowed based on market session.
All times are ET (America/New_York).

Config sessions:
  ny_open  — New York main session      09:30-16:00 ET
  london   — London/European session    03:00-12:00 ET
  asian    — Asian/overnight session    18:00-02:00 ET (crosses midnight)

Toggle each session on/off independently in agent_config.json.
Master switch: session_rules.enabled = false disables all filtering.
"""

import logging
from datetime import datetime, time
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


class SessionFilter:

    def __init__(self, config: dict):
        rules = self.rules = config.get('session_rules', {})
        self.enabled = rules.get('enabled', True)
        self.avoid_lunch = rules.get('avoid_lunch', True)
        self.lunch_start = self._t(rules.get('lunch_start', '12:00'))
        self.lunch_end   = self._t(rules.get('lunch_end',   '13:00'))

        # NY open blackout — avoids the chaotic first X minutes of NY session
        self.avoid_ny_open = rules.get('avoid_ny_open', True)
        blackout_mins      = rules.get('ny_open_blackout_minutes', 30)
        ny_open_hour, ny_open_min = 9, 30
        blackout_end_min   = ny_open_min + blackout_mins
        blackout_end_hour  = ny_open_hour + blackout_end_min // 60
        blackout_end_min   = blackout_end_min % 60
        self.ny_open_blackout_start = time(ny_open_hour, ny_open_min)
        self.ny_open_blackout_end   = time(blackout_end_hour, blackout_end_min)

        # Load each session
        self.sessions = {}
        for name in ('ny_open', 'london', 'asian'):
            cfg = rules.get(name, {})
            self.sessions[name] = {
                'enabled': cfg.get('enabled', False),
                'start':   self._t(cfg.get('start', '00:00')),
                'end':     self._t(cfg.get('end',   '00:00')),
                'crosses_midnight': self._crosses_midnight(
                    cfg.get('start', '00:00'), cfg.get('end', '00:00')
                ),
                'description': cfg.get('description', name),
            }

        enabled_names = [n for n, s in self.sessions.items() if s['enabled']]
        logger.info(f"SessionFilter: enabled={self.enabled} sessions={enabled_names}")

    @staticmethod
    def _t(s: str) -> time:
        h, m = map(int, s.split(':'))
        return time(h, m)

    @staticmethod
    def _crosses_midnight(start_str: str, end_str: str) -> bool:
        h_s, m_s = map(int, start_str.split(':'))
        h_e, m_e = map(int, end_str.split(':'))
        return time(h_s, m_s) > time(h_e, m_e)

    def _get_et_time(self, dt: Optional[datetime] = None) -> time:
        if dt is None:
            dt = datetime.now(ET)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=ET)
        return dt.astimezone(ET).time()

    def _in_session(self, t: time, session: dict) -> bool:
        s, e = session['start'], session['end']
        if session['crosses_midnight']:
            return t >= s or t < e
        return s <= t < e

    def is_trading_allowed(self, dt: Optional[datetime] = None) -> tuple[bool, str]:
        t = self._get_et_time(dt)

        # NY open blackout runs regardless of master enabled flag
        if self.avoid_ny_open and self.ny_open_blackout_start <= t < self.ny_open_blackout_end:
            end_str = self.ny_open_blackout_end.strftime('%H:%M')
            return False, f"NY open blackout until {end_str} ET (volatile first {(self.ny_open_blackout_end.hour * 60 + self.ny_open_blackout_end.minute) - 570}min)"

        if not self.enabled:
            return True, "Session filter disabled"

        # Lunch block (only matters if inside an otherwise allowed session)
        in_lunch = self.avoid_lunch and self.lunch_start <= t < self.lunch_end

        for name, session in self.sessions.items():
            if not session['enabled']:
                continue
            if self._in_session(t, session):
                if in_lunch:
                    return False, f"Lunch break ({self.lunch_start.strftime('%H:%M')}-{self.lunch_end.strftime('%H:%M')} ET)"
                return True, session['description']

        active = [n for n, s in self.sessions.items() if s['enabled'] and self._in_session(t, s)]
        return False, f"Outside enabled sessions (ET {t.strftime('%H:%M')})"
