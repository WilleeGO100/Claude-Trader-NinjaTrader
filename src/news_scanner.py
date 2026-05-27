"""
News Scanner Module
Auto-populates data/news_schedule.json with high-impact economic events.
Primary source: FMP Economic Calendar REST API (free tier supported).
Fallback: Known recurring events (NFP, FOMC) computed from schedule rules.
Runs at startup and can be called anytime to refresh the schedule.
"""

import json
import logging
import os
from calendar import monthcalendar
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import List, Dict
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# High-impact USD event keywords to watch for in FMP calendar
HIGH_IMPACT_KEYWORDS = [
    'fomc', 'federal reserve', 'fed rate', 'interest rate decision',
    'nonfarm', 'non-farm', 'nfp', 'unemployment rate', 'jobs report',
    'cpi', 'consumer price index', 'inflation',
    'ppi', 'producer price',
    'gdp', 'gross domestic product',
    'pce', 'personal consumption',
    'ism manufacturing', 'ism services', 'ism non-manufacturing',
    'retail sales',
    'powell', 'fed chair', 'jerome powell',
    'treasury', 'debt ceiling',
    'jolts', 'job openings',
]


class NewsScanner:

    def __init__(self, schedule_file: str = 'data/news_schedule.json'):
        self.schedule_file = Path(schedule_file)
        self.fmp_api_key   = os.getenv('FMP_API_KEY', '')
        self.schedule_file.parent.mkdir(exist_ok=True)

    def refresh(self, days_ahead: int = 3) -> List[Dict]:
        """
        Fetch upcoming high-impact events and write to news_schedule.json.
        Returns list of events written.
        """
        today      = date.today()
        date_to    = today + timedelta(days=days_ahead)
        events     = []

        # Try FMP API first
        if self.fmp_api_key:
            events = self._fetch_fmp(today, date_to)
            if events:
                logger.info(f"NewsScanner: {len(events)} events from FMP ({today} to {date_to})")

        # Always add known recurring events as a safety net
        recurring = self._get_recurring_events(today, date_to)
        existing_names = {e['name'].lower() for e in events}
        for r in recurring:
            if r['name'].lower() not in existing_names:
                events.append(r)

        if events:
            logger.info(f"NewsScanner: {len(events)} total high-impact events scheduled")
            for e in events:
                logger.info(f"  {e['datetime_et']} — {e['name']}")
        else:
            logger.info("NewsScanner: No high-impact events found for the period")

        self._write_schedule(events)
        return events

    # ── FMP REST API ──────────────────────────────────────────────────

    def _fetch_fmp(self, from_date: date, to_date: date) -> List[Dict]:
        try:
            import httpx
            url = (
                f"https://financialmodelingprep.com/api/v3/economic_calendar"
                f"?from={from_date}&to={to_date}&apikey={self.fmp_api_key}"
            )
            resp = httpx.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            events = []
            for item in data:
                name   = item.get('event', '')
                impact = item.get('impact', '').lower()
                country = item.get('country', '').upper()

                if country != 'US':
                    continue
                if impact not in ('high',):
                    if not self._is_high_impact_keyword(name):
                        continue

                dt_str = item.get('date', '')
                if not dt_str:
                    continue

                events.append({
                    'name':        name,
                    'datetime_et': self._format_et(dt_str),
                    'impact':      'high',
                    'source':      'fmp',
                })

            return events

        except Exception as e:
            logger.warning(f"NewsScanner: FMP fetch failed ({e}) — using fallback only")
            return []

    # ── Recurring event rules ─────────────────────────────────────────

    def _get_recurring_events(self, from_date: date, to_date: date) -> List[Dict]:
        """Generate known recurring high-impact events from rules"""
        events = []
        current = from_date

        while current <= to_date:
            # NFP — first Friday of each month at 8:30 AM ET
            first_friday = self._nth_weekday(current.year, current.month, 4, 1)
            if current == first_friday:
                events.append({
                    'name':        'NFP / Nonfarm Payrolls',
                    'datetime_et': f"{current.strftime('%m/%d/%Y')} 08:30",
                    'impact':      'high',
                    'source':      'recurring',
                })

            # CPI — usually 2nd or 3rd Wednesday at 8:30 AM ET
            # Approximate: 2nd Wednesday
            second_wed = self._nth_weekday(current.year, current.month, 2, 2)
            if current == second_wed:
                events.append({
                    'name':        'CPI / Consumer Price Index',
                    'datetime_et': f"{current.strftime('%m/%d/%Y')} 08:30",
                    'impact':      'high',
                    'source':      'recurring',
                })

            current += timedelta(days=1)

        return events

    @staticmethod
    def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
        """
        Return the nth occurrence of weekday in the given month/year.
        weekday: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun
        n: 1=first, 2=second, etc.
        """
        weeks = monthcalendar(year, month)
        count = 0
        for week in weeks:
            if week[weekday] != 0:
                count += 1
                if count == n:
                    return date(year, month, week[weekday])
        return date(year, month, 1)  # fallback

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _is_high_impact_keyword(name: str) -> bool:
        name_lower = name.lower()
        return any(kw in name_lower for kw in HIGH_IMPACT_KEYWORDS)

    @staticmethod
    def _format_et(dt_str: str) -> str:
        """Convert FMP datetime string to MM/DD/YYYY HH:MM format"""
        for fmt in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
            try:
                dt = datetime.strptime(dt_str, fmt)
                return dt.strftime('%m/%d/%Y %H:%M')
            except ValueError:
                continue
        return dt_str

    def _write_schedule(self, events: List[Dict]):
        data = {
            "_instructions": (
                "Auto-generated by NewsScanner. Add manual events below. "
                "Format: MM/DD/YYYY HH:MM ET"
            ),
            "_last_updated": datetime.now().strftime('%Y-%m-%d %H:%M'),
            "events": events,
        }
        with open(self.schedule_file, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"NewsScanner: Schedule written to {self.schedule_file}")
