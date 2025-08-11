import aiohttp
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import logging
from fastapi import HTTPException


class EventService:
    """
    Service for fetching and caching public event data that may impact routes.
    """

    def __init__(self, api_url: str, cache_ttl_minutes: int = 30):
        self.api_url = api_url
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_expiry: Optional[datetime] = None
        self.logger = logging.getLogger(__name__)

    async def get_events(self) -> List[Dict[str, Any]]:
        """Fetch current events from the API"""
        now = datetime.utcnow()
        if self._cache and self._cache_expiry and self._cache_expiry > now:
            return self._cache

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.api_url) as response:
                    response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
                    events = await response.json()

            # Note: We assume the external API returns a list of event dictionaries
            # If the structure is different, parsing logic will need adjustment here.
            self.logger.debug(f"Fetched {len(events)} events from {self.api_url}")

            self._cache = events
            self._cache_expiry = now + self.cache_ttl
            return events

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
