"""
GexbotFeed — live gamma levels via gex.bot REST API.

Polls https://api.gexbot.com/NQ_NDX/classic/full/majors every 5 minutes
and writes gamma_flip/call_wall/put_wall to data/gamma_levels.json.
GammaLevelLoader.reload() is called automatically after each update.

No WebSocket or streaming subscription required — works with standard API key.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import certifi
import os
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# REQUESTS_CA_BUNDLE may point to a stale venv path — override with live certifi
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

logger = logging.getLogger(__name__)

GAMMA_FILE   = Path("data/gamma_levels.json")
API_BASE     = "https://api.gexbot.com"
TICKER       = "NQ_NDX"
POLL_SECONDS = 300  # 5 minutes


class GexbotFeed:
    """
    Start with GexbotFeed(gamma_loader=self.gamma).start().
    Polls the REST API every 5 min in a background daemon thread.
    GammaLevelLoader.reload() is called automatically on each update.
    """

    def __init__(self, gamma_loader=None, poll_seconds: int = POLL_SECONDS):
        self.gamma_loader  = gamma_loader
        self.poll_seconds  = poll_seconds
        self._api_key      = os.getenv("GEXBOT_API_KEY", "")
        self._user_agent   = os.getenv("GEXBOT_USER_AGENT", "ClaudeTrader/1.0")
        self._session      = requests.Session()
        self._session.verify = certifi.where()
        self._session.headers.update({
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent":    self._user_agent,
            "Accept":        "application/json",
        })

    def start(self):
        if not self._api_key:
            logger.warning("GexbotFeed: GEXBOT_API_KEY not set — live gamma feed disabled")
            return

        # Fetch immediately on startup, then poll on interval
        self._fetch_and_write()

        t = threading.Thread(target=self._loop, daemon=True)
        t.start()
        logger.info(f"GexbotFeed: polling {TICKER} every {self.poll_seconds}s")

    def _loop(self):
        while True:
            time.sleep(self.poll_seconds)
            self._fetch_and_write()

    def _fetch_and_write(self):
        url = f"{API_BASE}/{TICKER}/classic/full/majors"
        try:
            resp = self._session.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"GexbotFeed: fetch failed: {e}")
            return

        gamma_flip = data.get("zero_gamma", 0)
        call_wall  = data.get("mpos_vol", 0)
        put_wall   = data.get("mneg_vol", 0)
        spot       = data.get("spot", 0)

        if not gamma_flip:
            logger.debug("GexbotFeed: zero_gamma=0 in response, skipping write")
            return

        payload = {
            "date":       datetime.now().strftime("%Y-%m-%d"),
            "source":     "gexbot_live",
            "gamma_flip": round(gamma_flip, 2),
            "call_wall":  round(call_wall, 2),
            "put_wall":   round(put_wall, 2),
            "notes":      f"Live REST feed. Spot: {spot:.2f}. Updated: {datetime.now().strftime('%H:%M:%S')}",
        }

        try:
            with open(GAMMA_FILE, "w") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            logger.warning(f"GexbotFeed: failed to write gamma_levels.json: {e}")
            return

        if self.gamma_loader:
            self.gamma_loader.reload()

        logger.info(
            f"GexbotFeed: updated — flip={gamma_flip:.2f} "
            f"call={call_wall:.2f} put={put_wall:.2f} spot={spot:.2f}"
        )
