from fastapi import APIRouter, Query, HTTPException, Depends
from typing import Optional, Dict, Any
from app.services.weather_service import WeatherService
from app.dependencies import get_weather_service_api

router = APIRouter()

@router.get(
    "/current",
    response_model=Dict[str, Any],
    summary="Get current weather for a location",
    description="Get current weather conditions including temperature, wind speed, and precipitation"
)
async def get_current_weather(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    weather_service: WeatherService = Depends(get_weather_service_api)
):
    """Get current weather conditions"""
    return await weather_service.get_current_weather(lat, lon)

@router.get(
    "/impact",
    response_model=Dict[str, Any],
    summary="Get weather impact assessment",
    description="Get weather impact assessment for route planning"
)
async def get_weather_impact(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    weather_service: WeatherService = Depends(get_weather_service_api)
):
    """Get weather impact assessment"""
    return await weather_service.get_weather_impact(lat, lon)
