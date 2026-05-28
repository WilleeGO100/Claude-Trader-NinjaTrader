"""
Trade Notifier
Sends trade alerts to Discord webhook and logs to data/activity_log.csv.
Set DISCORD_WEBHOOK_URL in .env to enable.
"""

import csv
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional
import certifi
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

logger = logging.getLogger(__name__)
ACTIVITY_LOG = Path('data/activity_log.csv')


class TradeNotifier:

    def __init__(self):
        self.webhook_url = os.getenv('DISCORD_WEBHOOK_URL', '')
        if self.webhook_url:
            logger.info("TradeNotifier: Discord webhook active")
        self._ensure_log()

    def _ensure_log(self):
        if not ACTIVITY_LOG.exists():
            with open(ACTIVITY_LOG, 'w', newline='') as f:
                csv.writer(f).writerow(['Timestamp', 'Event', 'Direction', 'Price', 'Detail'])

    # ── Entry ──────────────────────────────────────────────────────────

    def on_entry(self, direction, entry, stop, target, confidence,
                 setup_type, source='Claude', contracts=1):
        risk   = abs(entry - stop)
        reward = abs(target - entry)
        rr     = reward / risk if risk > 0 else 0
        arrow  = '🟢 LONG' if direction == 'LONG' else '🔴 SHORT'
        color  = 0x00FF00 if direction == 'LONG' else 0xFF0000

        logger.info(f"TRADE ENTRY: {direction} @ {entry:.2f} | R:R {rr:.2f} | {setup_type} via {source}")

        self._discord({
            "embeds": [{
                "title": f"{arrow}  NQ TRADE ENTRY",
                "color": color,
                "fields": [
                    {"name": "Entry",     "value": f"`{entry:.2f}`",      "inline": True},
                    {"name": "Stop",      "value": f"`{stop:.2f}`",       "inline": True},
                    {"name": "Target",    "value": f"`{target:.2f}`",     "inline": True},
                    {"name": "R:R",       "value": f"`{rr:.2f}:1`",       "inline": True},
                    {"name": "Confidence","value": f"`{confidence:.0%}`", "inline": True},
                    {"name": "Contracts", "value": f"`{contracts}`",      "inline": True},
                    {"name": "Risk",      "value": f"`{risk:.1f} pts`",   "inline": True},
                    {"name": "Reward",    "value": f"`{reward:.1f} pts`", "inline": True},
                    {"name": "Source",    "value": f"`{source}`",         "inline": True},
                    {"name": "Setup",     "value": f"`{setup_type}`",     "inline": False},
                ],
                "footer": {"text": f"Claude Trader  •  {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}"}
            }]
        })

        self._log('ENTRY', direction, entry,
                  f"{setup_type} | stop={stop:.2f} | target={target:.2f} | R:R={rr:.2f} | conf={confidence:.0%} | {source}")

    # ── Exit ───────────────────────────────────────────────────────────

    def on_exit(self, direction, entry, exit_price, pnl=None,
                exit_reason='', bars_held=0):
        if pnl is None:
            pnl = (exit_price - entry) if direction == 'LONG' else (entry - exit_price)

        result  = 'WIN' if pnl > 0.5 else ('LOSS' if pnl < -0.5 else 'BREAKEVEN')
        pnl_str = f"+{pnl:.2f}" if pnl >= 0 else f"{pnl:.2f}"
        dollar  = pnl * 20
        dol_str = f"+${dollar:.0f}" if dollar >= 0 else f"-${abs(dollar):.0f}"
        icon    = ('✅' if result == 'WIN' else ('❌' if result == 'LOSS' else '➖'))
        color   = 0x00FF00 if result == 'WIN' else (0xFF0000 if result == 'LOSS' else 0xFFFF00)

        logger.info(f"TRADE EXIT: {direction} {entry:.2f}->{exit_price:.2f} | {pnl_str}pts ({dol_str}) | {exit_reason}")

        self._discord({
            "embeds": [{
                "title": f"{icon}  NQ TRADE EXIT  —  {result}",
                "color": color,
                "fields": [
                    {"name": "Direction", "value": f"`{direction}`",       "inline": True},
                    {"name": "Entry",     "value": f"`{entry:.2f}`",       "inline": True},
                    {"name": "Exit",      "value": f"`{exit_price:.2f}`",  "inline": True},
                    {"name": "P&L (pts)", "value": f"`{pnl_str}`",         "inline": True},
                    {"name": "P&L ($)",   "value": f"**`{dol_str}`**",     "inline": True},
                    {"name": "Bars Held", "value": f"`{bars_held}`",       "inline": True},
                    {"name": "Reason",    "value": f"`{exit_reason}`",     "inline": False},
                ],
                "footer": {"text": f"Claude Trader  •  {datetime.now().strftime('%Y-%m-%d %H:%M:%S ET')}"}
            }]
        })

        self._log('EXIT', direction, exit_price,
                  f"{result} | pnl={pnl_str}pts ({dol_str}) | entry={entry:.2f} | {exit_reason} | bars={bars_held}")

    # ── Watchdog ───────────────────────────────────────────────────────

    def on_watchdog_fire(self, direction, price, rr):
        logger.info(f"WATCHDOG ENTRY: {direction} @ {price:.2f} R:R {rr:.2f} (Groq)")
        self._discord({"content": f"**Watchdog fired** — {direction} @ `{price:.2f}` | R:R `{rr:.2f}` | Groq confirmed intrabar trigger"})

    # ── News block ─────────────────────────────────────────────────────

    def on_news_block(self, event_name, resume_time):
        logger.warning(f"NEWS BLACKOUT: {event_name} — resumes {resume_time} ET")
        self._discord({"content": f":warning: **NEWS BLACKOUT** — `{event_name}` | Trading resumes after `{resume_time}` ET"})

    # ── Discord POST ───────────────────────────────────────────────────

    def _discord(self, payload: dict):
        if not self.webhook_url:
            return
        try:
            import requests
            requests.post(self.webhook_url, json=payload, timeout=5)
        except Exception as e:
            logger.debug(f"Discord error: {e}")

    # ── CSV log ────────────────────────────────────────────────────────

    def _log(self, event, direction, price, detail):
        try:
            ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            with open(ACTIVITY_LOG, 'a', newline='') as f:
                csv.writer(f).writerow([ts, event, direction, f"{price:.2f}", detail])
        except Exception as e:
            logger.debug(f"Log error: {e}")
