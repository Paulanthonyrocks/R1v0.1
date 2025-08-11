from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime


class CurrentWeatherResponse(BaseModel):
    temperature: float = Field(..., description="Temperature in degrees Celsius or Fahrenheit")
    wind_speed: float = Field(..., description="Wind speed in km/h or m/s")
    precipitation: float = Field(..., description="Precipitation amount in mm or inches (e.g., for the last hour)")
    conditions: str = Field(..., description="Textual description of weather conditions (e.g., 'Sunny', 'Partly Cloudy', 'Heavy Rain')")
    timestamp: datetime = Field(..., description="Timestamp of the weather data (UTC)")
    location: Optional[str] = Field(None, description="Optional name of the location")


class WeatherImpactResponse(BaseModel):
    impact_level: str = Field(..., description="Overall weather impact level (e.g., 'low', 'medium', 'high', 'critical')")
    description: str = Field(..., description="Description of the weather impact on traffic or operations")
    recommendations: List[str] = Field(..., description="List of recommended actions or precautions")
    timestamp: datetime = Field(..., description="Timestamp of the impact assessment (UTC)")
    location: Optional[str] = Field(None, description="Optional name of the location")
