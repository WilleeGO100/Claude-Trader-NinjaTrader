"""
News Filter Module
Blocks trading around major economic events (FOMC, CPI, NFP, etc.)
Edit data/news_schedule.json to add upcoming events before each session.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


class NewsFilter:
    """Blocks trading around scheduled high-impact news events"""

    def __init__(self, config: dict):
        news_cfg = config.get('news_filters', {})
        self.enabled = news_cfg.get('enabled', True)
        self.blackout_before = news_cfg.get('blackout_minutes_before', 30)
        self.blackout_after = news_cfg.get('blackout_minutes_after', 30)
        self.schedule_file = Path(news_cfg.get('schedule_file', 'data/news_schedule.json'))
        self._ensure_schedule_file()

    def _ensure_schedule_file(self):
        """Create schedule file with examples if it doesn't exist"""
        if not self.schedule_file.exists():
            example = {
                "_instructions": "Add upcoming high-impact events before each session. Format: MM/DD/YYYY HH:MM ET",
                "events": [
                    {"name": "FOMC Example", "datetime_et": "12/31/2099 14:00", "impact": "high"},
                    {"name": "NFP Example",  "datetime_et": "12/31/2099 08:30", "impact": "high"}
                ]
            }
            with open(self.schedule_file, 'w') as f:
                json.dump(example, f, indent=2)
            logger.info(f"Created news schedule file: {self.schedule_file}")

    def _load_events(self) -> list:
        try:
            with open(self.schedule_file, 'r') as f:
                data = json.load(f)
            return data.get('events', [])
        except Exception as e:
            logger.warning(f"Could not load news schedule: {e}")
            return []

    def is_trading_allowed(self, dt: Optional[datetime] = None) -> tuple[bool, str]:
        """
        Check if trading is allowed (not in a news blackout window).

        Returns:
            (allowed, reason) tuple
        """
        if not self.enabled:
            return True, "News filter disabled"

        if dt is None:
            dt = datetime.now(ET)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=ET)
        else:
            dt = dt.astimezone(ET)

        events = self._load_events()
        for event in events:
            try:
                event_dt = datetime.strptime(event['datetime_et'], '%m/%d/%Y %H:%M')
                event_dt = event_dt.replace(tzinfo=ET)
                window_start = event_dt - timedelta(minutes=self.blackout_before)
                window_end = event_dt + timedelta(minutes=self.blackout_after)

                if window_start <= dt <= window_end:
                    name = event.get('name', 'News event')
                    return False, f"News blackout: {name} at {event_dt.strftime('%H:%M')} ET (±{self.blackout_before}/{self.blackout_after}min)"
            except Exception:
                continue

        return True, "No news blackout active"
