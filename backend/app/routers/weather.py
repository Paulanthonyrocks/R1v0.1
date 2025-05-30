from fastapi import APIRouter, Query, HTTPException, Depends
from typing import Optional, Dict, Any
from app.services.weather_service import WeatherService
from app.dependencies import get_weather_service_api, get_current_active_user

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
    weather_service: WeatherService = Depends(get_weather_service_api),
    current_user: dict = Depends(get_current_active_user)
):
    """Get current weather conditions"""
    try:
        return await weather_service.get_current_weather(lat=lat, lon=lon)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching current weather: {str(e)}"
        )

@router.get(
    "/impact",
    response_model=Dict[str, Any],
    summary="Get weather impact assessment",
    description="Get weather impact assessment for route planning"
)
async def get_weather_impact(
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    weather_service: WeatherService = Depends(get_weather_service_api),
    current_user: dict = Depends(get_current_active_user)
):
    """Get weather impact assessment"""
    try:
        return await weather_service.get_weather_impact(lat=lat, lon=lon)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching weather impact assessment: {str(e)}"
        )
