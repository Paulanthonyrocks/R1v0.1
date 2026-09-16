import socket
import json
import logging
import asyncio
import time
import threading
from typing import Dict, List, Optional, Any

logger = logging.getLogger("app.services.v2x")


def parse_inbound_datagram(data: bytes) -> Optional[Dict[str, Any]]:
    """Parse an inbound V2X datagram (BSM/CAM/ACK JSON). None = not our protocol."""
    try:
        msg = json.loads(data.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(msg, dict):
        return None
    kind = str(msg.get("v2x_msg", msg.get("type", ""))).upper()
    if kind not in ("BSM", "CAM", "ACK", "DENM_ACK"):
        return None
    return msg

class V2XService:
    """
    V2X (Vehicle-to-Everything) Service.
    Broadcasts traffic directives and safety messages to connected autonomous vehicles.
    """
    def __init__(self, config: dict):
        self.config = config
        self.v2x_cfg = config.get("v2x", {})
        self.enabled = self.v2x_cfg.get("enabled", False)
        self.broadcast_ip = self.v2x_cfg.get("broadcast_ip", "255.255.255.255")
        self.broadcast_port = self.v2x_cfg.get("broadcast_port", 5005)
        self.udp_socket = None
        # Feature 7 inbound: counts + per-station last-seen. Listener thread
        # only runs when v2x.listen_enabled true (default false).
        self.listen_enabled = self.v2x_cfg.get("listen_enabled", False)
        self.listen_port = self.v2x_cfg.get("listen_port", self.broadcast_port)
        self.rx_count = 0
        self.rx_by_station: Dict[str, float] = {}
        self.tx_count = 0
        self._listen_thread = None
        self._listen_stop = threading.Event()
        
        if self.enabled:
            try:
                self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                logger.info(f"V2X Service initialized on {self.broadcast_ip}:{self.broadcast_port}")
            except Exception as e:
                logger.error(f"Failed to initialize V2X UDP socket: {e}")
                self.enabled = False

    async def broadcast_directive(self, feed_id: str, zone_id: str, directive_type: str, value: Any):
        """
        Broadcasts a traffic directive (e.g., 'SPEED_LIMIT', 'LANE_CLOSED').
        """
        if not self.enabled:
            return

        message = {
            "v2x_msg": "DIRECTIVE",
            "f": feed_id,
            "z": zone_id,
            "type": directive_type,
            "val": value,
            "ts": time.time()
        }
        
        try:
            data = json.dumps(message).encode('utf-8')
            # Standard UDP sendto is non-blocking for small payloads
            self.udp_socket.sendto(data, (self.broadcast_ip, self.broadcast_port))
            self.tx_count += 1
            logger.debug(f"V2X Broadcast [{feed_id}]: {directive_type} -> {value}")
        except Exception as e:
            logger.error(f"V2X Broadcast failed: {e}")

    def handle_inbound(self, data: bytes) -> Optional[Dict[str, Any]]:
        """Record one inbound datagram. Returns parsed msg or None."""
        msg = parse_inbound_datagram(data)
        if msg is None:
            return None
        self.rx_count += 1
        station = str(msg.get("station_id", msg.get("station", "unknown")))
        self.rx_by_station[station] = time.time()
        logger.debug(f"V2X inbound {msg.get('v2x_msg')} from {station}")
        return msg

    def inbound_stats(self) -> Dict[str, Any]:
        return {
            "rx_count": self.rx_count,
            "tx_count": self.tx_count,
            "stations_seen": len(self.rx_by_station),
            "listening": self._listen_thread is not None and self._listen_thread.is_alive(),
        }

    async def process_analytics_trigger(self, feed_id: str, metrics: dict):
        """
        Automated V2X responses based on real-time analytics.
        """
        if not self.enabled:
            return

        # Example: Congestion-based speed smoothing
        congestion = metrics.get("congestion_level", 0)
        if congestion > 0.8:
            await self.broadcast_directive(
                feed_id=feed_id,
                zone_id="AUTO_GRID",
                directive_type="REDUCE_SPEED",
                value=20
            )
        elif congestion < 0.2:
            await self.broadcast_directive(
                feed_id=feed_id,
                zone_id="AUTO_GRID",
                directive_type="RESUME_NORMAL_SPEED",
                value=None
            )

    async def stop(self):
        """Cleanup sockets."""
        self._listen_stop.set()
        if self.udp_socket:
            self.udp_socket.close()
            self.udp_socket = None
        logger.info("V2X Service stopped.")
