"""Work-zone mode (feature 9).

Scheduled lane/speed overrides with expiry. Zones come from config
`work_zones:` (list of {id, feed_id, lane, speed_limit, starts_at,
ends_at}). Expired zones are inert; no zone means normal rules. Pure
time check so routers and the safety path share one truth.
"""
import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.services.workzone")


def is_zone_active(zone: Dict[str, Any], now_ts: Optional[float] = None) -> bool:
    now = time.time() if now_ts is None else now_ts
    try:
        if zone.get("starts_at") is not None and now < float(zone["starts_at"]):
            return False
        if zone.get("ends_at") is not None and now > float(zone["ends_at"]):
            return False
        return True
    except (TypeError, ValueError):
        return False


def active_zones(zones: List[Dict[str, Any]], now_ts: Optional[float] = None) -> List[Dict[str, Any]]:
    return [z for z in zones if is_zone_active(z, now_ts)]


class WorkZoneService:
    def __init__(self, config: Dict[str, Any]):
        self.enabled = config.get("work_zones", {}).get("enabled", True)
        self.zones: List[Dict[str, Any]] = config.get("work_zones", {}).get("zones", [])

    def active(self, now_ts: Optional[float] = None, feed_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        zones = active_zones(self.zones, now_ts)
        if feed_id is not None:
            zones = [z for z in zones if not z.get("feed_id") or z.get("feed_id") == feed_id]
        return zones

    def speed_limit_for(self, feed_id: str, lane: Any, default: int,
                        now_ts: Optional[float] = None) -> int:
        for z in self.active(now_ts, feed_id):
            zl = z.get("lane")
            if zl is None or str(zl) == str(lane):
                try:
                    return int(z.get("speed_limit", default))
                except (TypeError, ValueError):
                    return default
        return default
