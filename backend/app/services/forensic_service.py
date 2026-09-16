"""Forensic search (feature 5).

Attribute filter over incident dicts already in the DB. No new index yet:
linear scan is fine at incident volumes (tens/min); the match function is
pure so a DB-side filter can replace it later without changing the API.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.services.forensic")


def match_incident(incident: Dict[str, Any], filters: Dict[str, Any]) -> bool:
    ftype = filters.get("type")
    if ftype and str(incident.get("type", "")).upper() != str(ftype).upper():
        return False
    sev = filters.get("severity")
    if sev and str(incident.get("severity", "")).upper() != str(sev).upper():
        return False
    feed = filters.get("feed_id")
    if feed and str(incident.get("source_feed_id", incident.get("feed_id", ""))) != str(feed):
        return False
    lane = filters.get("lane")
    if lane is not None:
        details = incident.get("details") or {}
        ilane = details.get("lane", details.get("meta", {}).get("lane") if isinstance(details.get("meta"), dict) else None)
        if str(ilane) != str(lane):
            return False
    since = filters.get("since")
    if since is not None:
        try:
            if float(incident.get("timestamp", 0)) < float(since):
                return False
        except (TypeError, ValueError):
            pass
    until = filters.get("until")
    if until is not None:
        try:
            if float(incident.get("timestamp", 0)) > float(until):
                return False
        except (TypeError, ValueError):
            pass
    q = filters.get("q")
    if q:
        hay = f"{incident.get('description', '')} {incident.get('type', '')}".lower()
        if str(q).lower() not in hay:
            return False
    return True


def filter_incidents(
    incidents: List[Dict[str, Any]],
    filters: Dict[str, Any],
    limit: int = 100,
) -> List[Dict[str, Any]]:
    out = [i for i in incidents if match_incident(i, filters)]
    return out[: max(1, limit)]


class ForensicService:
    def __init__(self, config: Dict[str, Any]):
        self.enabled = config.get("forensic", {}).get("enabled", True)
        self.default_limit = int(config.get("forensic", {}).get("default_limit", 100))

    async def search(self, db_manager, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        if not self.enabled:
            return []
        limit = int(filters.get("limit", self.default_limit))
        incidents = await db_manager.get_incidents(limit=min(limit * 5, 1000), offset=0, filters={})
        return filter_incidents(incidents, filters, limit)
