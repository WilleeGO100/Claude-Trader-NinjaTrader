"""
GexbotFeed — live gamma level feed from gex.bot WebSocket API.

Runs as a background daemon thread. On each GEX message for NQ_NDX,
extracts zero_gamma (flip), major_pos_vol (call wall), major_neg_vol
(put wall) and writes them to data/gamma_levels.json so GammaLevelLoader
always has fresh levels without manual updates.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import requests
from azure.messaging.webpubsubclient import WebPubSubClient
from azure.messaging.webpubsubclient.models import (
    CallbackType,
    OnConnectedArgs,
    OnDisconnectedArgs,
    OnGroupDataMessageArgs,
)
from google.protobuf import any_pb2

# Use compiled protos from project root
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from generated_proto import gex_pb2
import zstandard

logger = logging.getLogger(__name__)

GAMMA_FILE  = Path("data/gamma_levels.json")
BASE_URL    = "https://api.gex.bot/v2"
TICKER      = "NQ_NDX"
DCTX        = zstandard.ZstdDecompressor()


def _decompress_gex(any_message: any_pb2.Any) -> Optional[Dict]:
    try:
        compressed = any_message.value
        with DCTX.stream_reader(compressed) as r:
            raw = r.read()
        proto = gex_pb2.Gex()
        proto.ParseFromString(raw)
        return {
            "spot":          (proto.spot          or 0) / 100.0,
            "zero_gamma":    (proto.zero_gamma     or 0) / 100.0,
            "major_pos_vol": (proto.major_pos_vol  or 0) / 100.0,
            "major_neg_vol": (proto.major_neg_vol  or 0) / 100.0,
        }
    except Exception as e:
        logger.debug(f"GexbotFeed: decompress error: {e}")
        return None


def _write_gamma_levels(gex: Dict):
    data = {
        "date":        datetime.now().strftime("%Y-%m-%d"),
        "source":      "gexbot_live",
        "gamma_flip":  round(gex["zero_gamma"], 2),
        "call_wall":   round(gex["major_pos_vol"], 2),
        "put_wall":    round(gex["major_neg_vol"], 2),
        "notes":       f"Live feed. Spot: {gex['spot']:.2f}. Updated: {datetime.now().strftime('%H:%M:%S')}",
    }
    try:
        with open(GAMMA_FILE, "w") as f:
            json.dump(data, f, indent=2)
        logger.info(
            f"GexbotFeed: gamma updated — flip={data['gamma_flip']} "
            f"call={data['call_wall']} put={data['put_wall']}"
        )
    except Exception as e:
        logger.warning(f"GexbotFeed: failed to write gamma_levels.json: {e}")


class _GexClient:
    def __init__(self, url: str, groups: List[str], gamma_loader=None):
        self.groups       = groups
        self.gamma_loader = gamma_loader
        self.client       = WebPubSubClient(url)
        self.client.subscribe(CallbackType.CONNECTED,     self._on_connected)
        self.client.subscribe(CallbackType.DISCONNECTED,  self._on_disconnected)
        self.client.subscribe(CallbackType.GROUP_MESSAGE, self._on_message)

    def start(self):
        t = threading.Thread(target=self.client.open, daemon=True)
        t.start()

    def _on_connected(self, event: OnConnectedArgs):
        logger.info(f"GexbotFeed: connected (id={event.connection_id})")
        for g in self.groups:
            try:
                self.client.join_group(g)
                logger.info(f"GexbotFeed: joined {g}")
            except Exception as e:
                logger.warning(f"GexbotFeed: failed to join {g}: {e}")

    def _on_disconnected(self, event: OnDisconnectedArgs):
        logger.warning(f"GexbotFeed: disconnected — {event.message}")

    def _on_message(self, event: OnGroupDataMessageArgs):
        try:
            any_msg = any_pb2.Any()
            any_msg.ParseFromString(event.data)
            if "proto.gex" not in any_msg.type_url:
                return
            gex = _decompress_gex(any_msg)
            if not gex or not gex["zero_gamma"]:
                return
            _write_gamma_levels(gex)
            if self.gamma_loader:
                self.gamma_loader.reload()
        except Exception as e:
            logger.debug(f"GexbotFeed: message error: {e}")


class GexbotFeed:
    """
    Start with GexbotFeed(gamma_loader=self.gamma).start().
    Runs entirely in background — no blocking, no main loop changes needed.
    GammaLevelLoader.reload() is called automatically on each update.
    """

    def __init__(self, gamma_loader=None):
        self.gamma_loader = gamma_loader
        self._started     = False

    def start(self):
        api_key    = os.getenv("GEXBOT_API_KEY", "")
        user_agent = os.getenv("GEXBOT_USER_AGENT", "ClaudeTrader/1.0")

        if not api_key:
            logger.warning("GexbotFeed: GEXBOT_API_KEY not set — live gamma feed disabled")
            return

        try:
            resp = requests.get(
                f"{BASE_URL}/negotiate",
                headers={"Authorization": f"Bearer {api_key}", "User-Agent": user_agent},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"GexbotFeed: negotiate failed: {e} — live gamma disabled")
            return

        prefix = data.get("prefix", "red")
        urls   = data.get("websocket_urls", {})
        classic_url = urls.get("classic")

        if not classic_url:
            logger.warning("GexbotFeed: no classic websocket URL in negotiate response")
            return

        group = f"{prefix}_{TICKER}_classic_gex_full"
        client = _GexClient(classic_url, [group], self.gamma_loader)
        client.start()
        self._started = True
        logger.info(f"GexbotFeed: live gamma feed started — subscribed to {group}")
