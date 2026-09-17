from typing import Dict, Any
from datetime import datetime, timedelta, timezone
import logging
import math
from fastapi import HTTPException
import aiohttp
import httpx

from .external_api_client import BaseApiClient, ExternalAPIError


class WeatherService(BaseApiClient):
    """
    Service for fetching and caching weather data from a third-party API.
    """

    def __init__(self, api_key: str, api_url: str, cache_ttl_minutes: int = 10, timeout: float = 10.0):
        super().__init__(base_url=api_url, timeout=timeout)
        self.api_key = api_key
        # self.api_url = api_url # Handled by BaseApiClient
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_expiry: Dict[str, datetime] = {}
        self.logger = logging.getLogger(__name__)

    async def get_current_weather(self, lat: float, lon: float) -> Dict[str, Any]:
        """Get current weather for a given location with async HTTP request"""
        cache_key = f"{lat},{lon}"
        now = datetime.now(timezone.utc)

        # Check cache
        if cache_key in self._cache and self._cache_expiry[cache_key] > now:
            weather_data = self._cache[cache_key]
        else:
            # Fetch new data using the base API client
            try:
                response = await self._make_request(
                    method="GET",
                    url="data/2.5/weather",
                    params={"lat": lat, "lon": lon, "appid": self.api_key, "units": "metric"},
                )

                weather_data = response.json()
                self._validate_weather_data(weather_data)

                # Cache the data
                self._cache[cache_key] = weather_data
                self._cache_expiry[cache_key] = now + self.cache_ttl

            except (ValueError, TypeError, KeyError, IndexError) as e:
                raise HTTPException(status_code=502, detail="Invalid response from weather service") from e
            except aiohttp.ClientError as e:
                self.logger.error(f"Weather API request failed: {str(e)}")
                raise HTTPException(
                    status_code=502, detail="Weather service temporarily unavailable"
                )
            except ExternalAPIError as e:
                self.logger.error(f"Weather API request failed: {e}")
                raise HTTPException(
                    status_code=504 if isinstance(e.__cause__, httpx.TimeoutException) or e.status_code == 504 else 502,
                    detail="Weather service temporarily unavailable",
                )

            except Exception as e: # Catch any other unexpected errors
                self.logger.error(f"Unexpected error in weather service: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail="Internal server error while fetching weather data",
                )

        # Transform to our format
        return {
            "temperature": weather_data["main"]["temp"],
            "conditions": weather_data["weather"][0]["main"],
            "precipitation_chance": self._calculate_precipitation_chance(weather_data),
            "wind_speed": weather_data["wind"]["speed"],
        }

    @staticmethod
    def _validate_weather_data(data: Any) -> None:
        """Reject malformed upstream values before they can poison the cache."""
        def number(value: Any) -> bool:
            return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)

        if not isinstance(data, dict):
            raise ValueError("Weather payload must be an object")
        temperature = data["main"]["temp"]
        wind_speed = data["wind"]["speed"]
        conditions = data["weather"][0]["main"]
        if not number(temperature) or not number(wind_speed) or not isinstance(conditions, str) or not conditions:
            raise ValueError("Invalid weather measurements")
        if "pop" in data and (not number(data["pop"]) or not 0 <= data["pop"] <= 1):
            raise ValueError("Invalid precipitation probability")
        for kind in ("rain", "snow"):
            if kind in data:
                if not isinstance(data[kind], dict):
                    raise ValueError("Invalid precipitation measurements")
                amount = data[kind].get("1h", 0)
                if not number(amount) or amount < 0:
                    raise ValueError("Invalid precipitation amount")

    async def get_weather_impact(self, lat: float, lon: float) -> Dict[str, Any]:
        """Get weather impact assessment for a location"""
        weather = await self.get_current_weather(lat, lon)

        # Determine severity based on conditions
        severity = self._assess_weather_severity(weather)

        return {
            "type": "weather",
            "description": f"{weather['conditions']} - {weather['temperature']}°C",
            "severity": severity,
            "location": f"Location ({lat:.2f}, {lon:.2f})",
            "startTime": datetime.now(timezone.utc).isoformat(),
            "details": weather,
        }

    def _calculate_precipitation_chance(self, weather_data: Dict[str, Any]) -> float:
        """Calculate precipitation chance from weather data"""
        # OpenWeatherMap provides pop (probability of precipitation) if available
        if "pop" in weather_data:
            return weather_data["pop"] * 100

        # Fallback: estimate from rain/snow data
        precipitation = 0
        if "rain" in weather_data:
            precipitation = max(precipitation, weather_data["rain"].get("1h", 0))
        if "snow" in weather_data:
            precipitation = max(precipitation, weather_data["snow"].get("1h", 0))

        # Convert mm/h to rough probability
        return min(precipitation * 20, 100)  # 5mm/h = 100% chance

    def _assess_weather_severity(self, weather: Dict[str, Any]) -> str:
        """Assess weather severity based on conditions"""
        if weather["precipitation_chance"] > 70 or weather["wind_speed"] > 50:
            return "High"
        elif weather["precipitation_chance"] > 30 or weather["wind_speed"] > 30:
            return "Medium"
        return "Low"
