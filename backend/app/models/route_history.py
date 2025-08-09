from sqlalchemy import Column, String, DateTime, JSON, Float, Integer, func
from app.models.base import Base

class RouteHistoryModel(Base):
    __tablename__ = "route_history"

    id = Column(String, primary_key=True)
    user_id = Column(String, index=True)
    start_location = Column(JSON)
    end_location = Column(JSON)
    start_time = Column(DateTime)
    end_time = Column(DateTime)
    route_preference_used = Column(String)
    road_types_used = Column(JSON)
    distance_km = Column(Float)
    duration_minutes = Column(Float)
    traffic_conditions = Column(String)
    weather_conditions = Column(String, nullable=True)
    user_rating = Column(Integer, nullable=True)
    feedback = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
