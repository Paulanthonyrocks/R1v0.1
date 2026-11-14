import aiohttp
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone
import logging
from fastapi import HTTPException


class EventService:
    """
    Service for fetching and caching public event data that may impact routes.

    source="api" (default when api_url is set) pulls from the configured
    external API. source="incidents" (default when no api_url is set) serves
    the system's own ACTIVE incidents from the local DB -- real, keyless event
    data instead of a 501/[] honesty hold.
    """

    _INACTIVE_STATUSES = {"RESOLVED", "CLEARED", "FALSE_ALARM"}

    def __init__(self, api_url: str, cache_ttl_minutes: int = 30, timeout: float = 10.0,
                 db_manager: Any = None, source: str = "", limit: int = 50):
        self.api_url = api_url
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._cache: Optional[List[Dict[str, Any]]] = None
        self._cache_expiry: Optional[datetime] = None
        self.db_manager = db_manager
        self.source = source or ("api" if api_url else "incidents")
        self.limit = limit
        self.logger = logging.getLogger(__name__)

    async def get_events(self) -> List[Dict[str, Any]]:
        """Fetch current events from the API or the local incidents table"""
        now = datetime.now(timezone.utc)
        if self._cache is not None and self._cache_expiry and self._cache_expiry > now:
            return self._cache

        if self.source == "incidents":
            events = await self._events_from_incidents()
        else:
            events = await self._events_from_api()

        self._cache = events
        self._cache_expiry = now + self.cache_ttl
        return events

    async def _events_from_incidents(self) -> List[Dict[str, Any]]:
        """Active incidents from the local DB, shaped like external events."""
        if self.db_manager is None:
            self.logger.warning("EventService: no db_manager; returning empty event list.")
            return []
        try:
            rows = await self.db_manager.get_incidents(limit=self.limit)
        except Exception as e:
            self.logger.error(f"Failed to load incidents for events: {e}")
            raise HTTPException(status_code=503, detail="Event source temporarily unavailable")
        events = []
        for row in rows:
            if str(row.get("status", "")).upper() in self._INACTIVE_STATUSES:
                continue
            lat, lon = row.get("latitude"), row.get("longitude")
            location = f"({lat:.4f}, {lon:.4f})" if lat is not None and lon is not None else (row.get("feed_id") or "unknown")
            events.append({
                "id": row.get("id"),
                "type": row.get("type") or "incident",
                "description": row.get("description") or f"{row.get('type')} on {row.get('feed_id')}",
                "severity": row.get("severity") or "Medium",
                "location": location,
                "feed_id": row.get("feed_id"),
                "start_time": row.get("created_at"),
                "end_time": None,
                "status": row.get("status"),
                "details": row,
            })
        self.logger.debug(f"Served {len(events)} active incidents as events.")
        return events

    async def _events_from_api(self) -> List[Dict[str, Any]]:
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(self.api_url) as response:
                    response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
                    events = await response.json()

            # Note: We assume the external API returns a list of event dictionaries
            # If the structure is different, parsing logic will need adjustment here.
            self.logger.debug(f"Fetched {len(events)} events from {self.api_url}")
            return events

        except TimeoutError as e:
            raise HTTPException(status_code=504, detail="Event service timed out") from e
        except aiohttp.ClientError as e:
            self.logger.error(f"Event API request failed: {str(e)}")
            raise HTTPException(
                status_code=503, detail="Event service temporarily unavailable"
            )
        except Exception as e:
            self.logger.error(f"Failed to fetch event data: {str(e)}")
            raise HTTPException(
                status_code=500, detail="Internal server error while fetching events"
            )

    async def get_event_impacts(self) -> List[Dict[str, Any]]:
        """Get formatted event impacts for the route planner"""
        events = await self.get_events()

        return [
            {
                "type": "event",
                "description": event["description"],
                "severity": event["severity"],
                "location": event["location"],
                "startTime": event["start_time"],
                "endTime": event["end_time"],
                "details": event,
            }
            for event in events
        ]

    def _assess_event_severity(self, event_type: str) -> str:
        """Assess event severity based on type"""
        high_severity_types = {"accident", "disaster", "emergency"}
        medium_severity_types = {"roadwork", "construction", "delay"}

        if event_type.lower() in high_severity_types:
            return "High"
        elif event_type.lower() in medium_severity_types:
            return "Medium"
        return "Low"
