import aiohttp
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import logging
from fastapi import HTTPException

class WeatherService:
    """
    Service for fetching and caching weather data from a third-party API.
    """
    def __init__(self, api_key: str, cache_ttl_minutes: int = 10):
        self.api_key = api_key
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_expiry: Dict[str, datetime] = {}
        self.logger = logging.getLogger(__name__)

    async def get_current_weather(self, lat: float, lon: float) -> Dict[str, Any]:
        """Get current weather for a given location with async HTTP request"""
        cache_key = f"{lat},{lon}"
        now = datetime.utcnow()
        
        # Check cache
        if cache_key in self._cache and self._cache_expiry[cache_key] > now:
            weather_data = self._cache[cache_key]
        else:
            try:
                # Fetch new data
                async with aiohttp.ClientSession() as session:
                    url = f"https://api.openweathermap.org/data/2.5/weather"
                    params = {
                        'lat': lat,
                        'lon': lon,
                        'appid': self.api_key,
                        'units': 'metric'
                    }
                    async with session.get(url, params=params) as response:
                        if response.status != 200:
                            raise HTTPException(
                                status_code=response.status,
                                detail="Failed to fetch weather data"
                            )
                        weather_data = await response.json()
                        
                        # Cache the data
                        self._cache[cache_key] = weather_data
                        self._cache_expiry[cache_key] = now + self.cache_ttl
                        
            except aiohttp.ClientError as e:
                self.logger.error(f"Weather API request failed: {str(e)}")
                raise HTTPException(
                    status_code=503,
                    detail="Weather service temporarily unavailable"
                )
            except Exception as e:
                self.logger.error(f"Unexpected error in weather service: {str(e)}")
                raise HTTPException(
                    status_code=500,
                    detail="Internal server error while fetching weather data"
                )

        # Transform to our format
        return {
            'temperature': weather_data['main']['temp'],
            'conditions': weather_data['weather'][0]['main'],
            'precipitation_chance': self._calculate_precipitation_chance(weather_data),
            'wind_speed': weather_data['wind']['speed']
        }

    async def get_weather_impact(self, lat: float, lon: float) -> Dict[str, Any]:
        """Get weather impact assessment for a location"""
        weather = await self.get_current_weather(lat, lon)
        
        # Determine severity based on conditions
        severity = self._assess_weather_severity(weather)
        
        return {
            'type': 'weather',
            'description': f"{weather['conditions']} - {weather['temperature']}°C",
            'severity': severity,
            'location': f"Location ({lat:.2f}, {lon:.2f})",
            'startTime': datetime.utcnow().isoformat(),
            'details': weather
        }

    def _calculate_precipitation_chance(self, weather_data: Dict[str, Any]) -> float:
        """Calculate precipitation chance from weather data"""
        # OpenWeatherMap provides pop (probability of precipitation) if available
        if 'pop' in weather_data:
            return weather_data['pop'] * 100
        
        # Fallback: estimate from rain/snow data
        precipitation = 0
        if 'rain' in weather_data:
            precipitation = max(precipitation, weather_data['rain'].get('1h', 0))
        if 'snow' in weather_data:
            precipitation = max(precipitation, weather_data['snow'].get('1h', 0))
            
        # Convert mm/h to rough probability
        return min(precipitation * 20, 100)  # 5mm/h = 100% chance

    def _assess_weather_severity(self, weather: Dict[str, Any]) -> str:
        """Assess weather severity based on conditions"""
        if weather['precipitation_chance'] > 70 or weather['wind_speed'] > 50:
            return 'High'
        elif weather['precipitation_chance'] > 30 or weather['wind_speed'] > 30:
            return 'Medium'
        return 'Low'
