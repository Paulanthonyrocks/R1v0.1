import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy import create_engine, Column, String, DateTime, JSON, Float, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import func

from app.models.routing import (
    UserRoutingProfile,
    RouteHistoryEntry,
    UserRoutePreferences,
    PersonalizedRouteRequest,
    PersonalizedRouteResponse
)
from app.ml.preference_learner import UserPreferenceLearner
from app.ml.route_optimizer import RouteOptimizer

logger = logging.getLogger(__name__)

Base = declarative_base()

class RouteHistoryModel(Base):
    __tablename__ = 'route_history'
    
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

class UserProfileModel(Base):
    __tablename__ = 'user_profiles'
    
    user_id = Column(String, primary_key=True)
    profile_data = Column(JSON)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class PersonalizedRoutingService:
    def __init__(self, db_url: str, traffic_predictor, data_cache):
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        
        self.preference_learner = UserPreferenceLearner()
        self.route_optimizer = RouteOptimizer(traffic_predictor, data_cache)
        
    async def get_personalized_route(
        self,
        request: PersonalizedRouteRequest
    ) -> PersonalizedRouteResponse:
        """Get personalized route based on user preferences and real-time conditions"""
        try:
            # Get user profile
            profile = await self.get_user_profile(request.user_id)
            
            # Get routing recommendations based on user profile
            recommendations = self.preference_learner.get_route_recommendations(
                profile=profile,
                start_location=request.start_location,
                end_location=request.end_location,
                departure_time=request.departure_time
            )
            
            # Get optimized route using recommendations
            route = await self.route_optimizer.optimize_route(
                start_lat=request.start_location['latitude'],
                start_lon=request.start_location['longitude'],
                end_lat=request.end_location['latitude'],
                end_lon=request.end_location['longitude'],
                departure_time=request.departure_time,
                user_preferences=recommendations
            )
            
            # Prepare response
            response = PersonalizedRouteResponse(
                primary_route=route.segments[0],
                alternative_routes=[r.segments for r in route.alternative_routes],
                route_metadata={
                    'total_distance': route.total_distance_km,
                    'estimated_duration': route.estimated_duration_mins,
                    'route_type': 'personalized',
                    'optimization_factors': recommendations
                },
                user_preferences_applied=list(recommendations['preferred_road_types']),
                weather_impact=route.weather_impact if hasattr(route, 'weather_impact') else None,
                event_impacts=route.event_impacts if hasattr(route, 'event_impacts') else [],
                confidence_score=route.confidence_score
            )
            
            return response
            
        except Exception as e:
            logger.error(f"Error getting personalized route: {e}")
            raise

    async def record_route_history(self, entry: RouteHistoryEntry) -> None:
        """Record a new route history entry"""
        try:
            session = self.Session()
            
            db_entry = RouteHistoryModel(
                id=entry.route_id,
                user_id=entry.user_id,
                start_location=entry.start_location,
                end_location=entry.end_location,
                start_time=entry.start_time,
                end_time=entry.end_time,
                route_preference_used=entry.route_preference_used,
                road_types_used=entry.road_types_used,
                distance_km=entry.distance_km,
                duration_minutes=entry.duration_minutes,
                traffic_conditions=entry.traffic_conditions,
                weather_conditions=entry.weather_conditions,
                user_rating=entry.user_rating,
                feedback=entry.feedback
            )
            
            session.add(db_entry)
            session.commit()
            
            # Update user profile
            await self.update_user_profile(entry.user_id)
            
        except Exception as e:
            logger.error(f"Error recording route history: {e}")
            session.rollback()
            raise
        finally:
            session.close()

    async def get_user_profile(self, user_id: str) -> UserRoutingProfile:
        """Get user routing profile"""
        try:
            session = self.Session()
            
            profile_record = session.query(UserProfileModel).get(user_id)
            if profile_record:
                return UserRoutingProfile(**profile_record.profile_data)
                
            # If no profile exists, create a new one
            history = await self.get_user_route_history(user_id)
            profile = self.preference_learner.update_user_profile(user_id, history)
            
            # Save new profile
            session.add(UserProfileModel(
                user_id=user_id,
                profile_data=profile.dict()
            ))
            session.commit()
            
            return profile
            
        except Exception as e:
            logger.error(f"Error getting user profile: {e}")
            raise
        finally:
            session.close()

    async def update_user_profile(self, user_id: str) -> None:
        """Update user profile based on route history"""
        try:
            session = self.Session()
            
            # Get user's route history
            history = await self.get_user_route_history(user_id)
            
            # Get current profile if exists
            profile_record = session.query(UserProfileModel).get(user_id)
            current_profile = None
            if profile_record:
                current_profile = UserRoutingProfile(**profile_record.profile_data)
            
            # Update profile
            updated_profile = self.preference_learner.update_user_profile(
                user_id, history, current_profile
            )
            
            # Save updated profile
            if profile_record:
                profile_record.profile_data = updated_profile.dict()
                profile_record.updated_at = datetime.utcnow()
            else:
                session.add(UserProfileModel(
                    user_id=user_id,
                    profile_data=updated_profile.dict()
                ))
                
            session.commit()
            
        except Exception as e:
            logger.error(f"Error updating user profile: {e}")
            session.rollback()
            raise
        finally:
            session.close()

    async def get_user_route_history(
        self,
        user_id: str,
        limit: int = 100
    ) -> List[RouteHistoryEntry]:
        """Get user's route history"""
        try:
            session = self.Session()
            
            history_records = (
                session.query(RouteHistoryModel)
                .filter(RouteHistoryModel.user_id == user_id)
                .order_by(RouteHistoryModel.start_time.desc())
                .limit(limit)
                .all()
            )
            
            return [
                RouteHistoryEntry(
                    route_id=record.id,
                    user_id=record.user_id,
                    start_location=record.start_location,
                    end_location=record.end_location,
                    start_time=record.start_time,
                    end_time=record.end_time,
                    route_preference_used=record.route_preference_used,
                    road_types_used=record.road_types_used,
                    distance_km=record.distance_km,
                    duration_minutes=record.duration_minutes,
                    traffic_conditions=record.traffic_conditions,
                    weather_conditions=record.weather_conditions,
                    user_rating=record.user_rating,
                    feedback=record.feedback
                )
                for record in history_records
            ]
            
        except Exception as e:
            logger.error(f"Error getting user route history: {e}")
            raise
        finally:
            session.close()

    def get_route_history_analytics(self, user_id: str, limit: int = 20) -> dict:
        """
        Compute analytics on a user's route history: most common routes, time-of-day patterns, etc.
        """
        session = self.Session()
        try:
            history_records = (
                session.query(RouteHistoryModel)
                .filter(RouteHistoryModel.user_id == user_id)
                .order_by(RouteHistoryModel.start_time.desc())
                .limit(limit)
                .all()
            )
            if not history_records:
                return {"message": "No route history found."}

            # Most common start-end pairs
            from collections import Counter, defaultdict
            route_pairs = [
                (str(r.start_location), str(r.end_location)) for r in history_records
            ]
            most_common_routes = Counter(route_pairs).most_common(3)

            # Time-of-day histogram
            hour_counts = defaultdict(int)
            for r in history_records:
                if r.start_time:
                    hour_counts[r.start_time.hour] += 1
            time_of_day_histogram = [hour_counts.get(h, 0) for h in range(24)]

            # Average distance and duration
            avg_distance = sum(r.distance_km or 0 for r in history_records) / len(history_records)
            avg_duration = sum(r.duration_minutes or 0 for r in history_records) / len(history_records)

            return {
                "most_common_routes": [
                    {"start": s, "end": e, "count": c} for ((s, e), c) in most_common_routes
                ],
                "time_of_day_histogram": time_of_day_histogram,
                "average_distance_km": round(avg_distance, 2),
                "average_duration_min": round(avg_duration, 1),
                "total_routes_analyzed": len(history_records)
            }
        except Exception as e:
            logger.error(f"Error computing route history analytics: {e}")
            raise
        finally:
            session.close()
