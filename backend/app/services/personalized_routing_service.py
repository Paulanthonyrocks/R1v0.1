import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta # Added timedelta for time-based filtering
from collections import Counter, defaultdict # Added for proactive suggestions
from sqlalchemy import create_engine, Column, String, DateTime, JSON, Float, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session # Added Session for type hint
from sqlalchemy.sql import func
from sqlalchemy.exc import SQLAlchemyError # For more specific exception handling
import uuid # For generating unique suggestion IDs & pattern_ids
from pydantic import BaseModel # For CommonTravelPattern

from app.models.traffic import LocationModel # For CommonTravelPattern
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


class ProactiveSuggestionFeedbackLog(Base):
    __tablename__ = 'proactive_suggestion_feedback_log'

    id = Column(String, primary_key=True) # A unique ID for this feedback entry, e.g., str(uuid.uuid4())
    suggestion_id = Column(String, index=True, unique=True) # The ID of the suggestion this feedback is for
    user_id = Column(String, index=True)
    timestamp = Column(DateTime, server_default=func.now(), onupdate=func.now()) # Record creation/update time
    suggestion_details = Column(JSON) # Store what was suggested, e.g., route, destination, type of suggestion
    interaction_status = Column(String) # e.g., "suggested", "accepted", "rejected", "ignored", "modified", "pending_feedback", "error_in_suggestion"
    user_feedback_text = Column(String, nullable=True)
    user_rating = Column(Integer, nullable=True) # e.g., 1-5 stars
    created_at = Column(DateTime, server_default=func.now()) # Record creation time


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
        """Record a new route history entry.
        If the route history entry is linked to a proactive suggestion,
        this method also updates the feedback log for that suggestion.
        """
        session: Session = self.Session()
        try:
            # Record the main route history
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

            # Check if this route history corresponds to a proactive suggestion
            # This assumes RouteHistoryEntry might have an optional suggestion_id field.
            suggestion_id_linked = getattr(entry, 'suggestion_id', None)

            if suggestion_id_linked:
                feedback_log_entry = session.query(ProactiveSuggestionFeedbackLog).filter_by(suggestion_id=suggestion_id_linked).first()
                if feedback_log_entry:
                    feedback_log_entry.interaction_status = "accepted_and_completed"
                    feedback_log_entry.timestamp = datetime.utcnow() # Update timestamp to reflect this interaction
                    logger.info(f"Updated proactive suggestion log for suggestion_id {suggestion_id_linked} to 'accepted_and_completed'.")
                else:
                    logger.warning(f"Route history recorded with suggestion_id {suggestion_id_linked}, but no corresponding feedback log entry found.")

            session.commit()
            
            # Update user profile
            await self.update_user_profile(entry.user_id)
            
        except SQLAlchemyError as e:
            logger.error(f"Database error recording route history for user {entry.user_id}: {e}")
            if session:
                session.rollback()
            raise
        except Exception as e:
            logger.error(f"Error recording route history for user {entry.user_id}: {e}")
            if session: # Should always be true here but good practice
                session.rollback()
            raise
        finally:
            if session:
                session.close()

    def _get_most_frequent_destination(self, user_id: str, limit: int = 20) -> Optional[Dict[str, Any]]:
        """
        Identifies the most frequent destination for a user from their route history.
        """
        session = self.Session()
        try:
            history_records = (
                session.query(RouteHistoryModel.end_location)
                .filter(RouteHistoryModel.user_id == user_id)
                .order_by(RouteHistoryModel.start_time.desc())
                .limit(limit)
                .all()
            )

            if not history_records:
                return None

            # Convert location dicts to frozenset of items to make them hashable for Counter
            # Assuming end_location is a dictionary like {'latitude': ..., 'longitude': ...}
            destinations = [
                frozenset(record.end_location.items()) if isinstance(record.end_location, dict) else str(record.end_location)
                for record in history_records
            ]

            if not destinations:
                return None

            most_common_dest_tuple, count = Counter(destinations).most_common(1)[0]

            # Require a minimum frequency to consider it "common"
            if count > 1 or (count == 1 and len(destinations) == 1) : # If only one route, it's common by default
                # Convert frozenset back to dict
                if isinstance(most_common_dest_tuple, frozenset):
                    return dict(most_common_dest_tuple)
                else: # Fallback for non-dict locations, though current model implies dict
                    # This path is less likely if end_location is always a JSON dict
                    # Attempt to parse if it's a stringified dict, otherwise use as is
                    try:
                        # This is a simple attempt; complex string structures might fail
                        import json
                        return json.loads(most_common_dest_tuple)
                    except (json.JSONDecodeError, TypeError):
                        # If it's not a dict-like string, we can't reliably reconstruct the location object
                        # For now, we'll log a warning and skip suggesting for this case.
                        logger.warning(f"Could not reconstruct destination for user {user_id}: {most_common_dest_tuple}")
                        return None
            return None
        except Exception as e:
            logger.error(f"Error getting most frequent destination for user {user_id}: {e}")
            return None
        finally:
            session.close()


# Pydantic model for common travel patterns
class CommonTravelPattern(BaseModel):
    pattern_id: str
    user_id: str
    start_location_summary: Dict[str, Any] # e.g., {"latitude": 34.050, "longitude": -118.240, "name": "Approx Start"}
    end_location_summary: Dict[str, Any]   # e.g., {"latitude": 34.150, "longitude": -118.340, "name": "Approx End"}
    time_of_day_group: str  # e.g., "morning_commute_weekdays", "evening_commute_weekdays", "weekend_afternoon"
    days_of_week: List[int] # 0=Monday, 6=Sunday (actual days pattern was observed on for this group)
    frequency_score: float  # How often this pattern is observed (e.g., count of trips)
    average_duration_minutes: Optional[float] = None
    last_traveled_at: Optional[datetime] = None


class PersonalizedRoutingService:
    # ... (rest of the class is unchanged before __init__)
    def __init__(self, db_url: str, traffic_predictor, data_cache):
        self.engine = create_engine(db_url)
        Base.metadata.create_all(self.engine) # Ensures RouteHistoryModel, ProactiveSuggestionFeedbackLog, UserProfileModel are created
        self.Session = sessionmaker(bind=self.engine)

        self.preference_learner = UserPreferenceLearner()
        self.route_optimizer = RouteOptimizer(traffic_predictor, data_cache)

    # ... (existing methods like get_personalized_route, record_route_history, etc.)

    async def get_user_common_travel_patterns(self, user_id: str, top_n: int = 5, history_limit: int = 200) -> List[CommonTravelPattern]:
        """
        Identifies common travel patterns for a user based on their route history.
        This version uses Python-based grouping after fetching recent routes.
        """
        session: Session = self.Session()
        try:
            history_records_db = (
                session.query(RouteHistoryModel)
                .filter(RouteHistoryModel.user_id == user_id)
                .order_by(RouteHistoryModel.start_time.desc())
                .limit(history_limit) # Limit data pulled for processing
                .all()
            )

            if not history_records_db:
                return []

            # Helper to create a grouping key for locations (rounded lat/lon)
            def get_location_group_key(loc_json: Dict[str, Any], precision: int = 3) -> Optional[str]:
                if not loc_json or 'latitude' not in loc_json or 'longitude' not in loc_json:
                    return None
                # Ensure lat/lon are floats before rounding
                try:
                    lat = float(loc_json['latitude'])
                    lon = float(loc_json['longitude'])
                    return f"{lat:.{precision}f}_{lon:.{precision}f}"
                except (ValueError, TypeError):
                    return None # Could not parse lat/lon

            # Helper to determine time of day group and day type
            def get_time_group(start_time: datetime) -> Tuple[str, str]: # (time_of_day_group, day_type_group)
                hour = start_time.hour
                weekday = start_time.weekday() # Monday=0, Sunday=6

                time_group = "night_late" # Default
                if 6 <= hour < 10: time_group = "morning"
                elif 10 <= hour < 16: time_group = "midday"
                elif 16 <= hour < 20: time_group = "evening"
                elif 20 <= hour < 24: time_group = "night_early"

                day_type = "weekend" if weekday >= 5 else "weekday"
                return f"{time_group}_{day_type}", day_type


            processed_routes = []
            for record in history_records_db:
                start_loc_key = get_location_group_key(record.start_location)
                end_loc_key = get_location_group_key(record.end_location)

                if not start_loc_key or not end_loc_key or not record.start_time:
                    logger.debug(f"Skipping record {record.id} due to missing location or start_time.")
                    continue

                time_group, _ = get_time_group(record.start_time)

                processed_routes.append({
                    "start_loc_key": start_loc_key,
                    "end_loc_key": end_loc_key,
                    "time_group": time_group,
                    "day_of_week": record.start_time.weekday(),
                    "duration_minutes": record.duration_minutes,
                    "start_time": record.start_time, # Keep original for last_traveled_at
                    "original_start_loc": record.start_location, # For summary
                    "original_end_loc": record.end_location # For summary
                })

            if not processed_routes:
                return []

            # Group by (start_loc_key, end_loc_key, time_group)
            from itertools import groupby
            def group_key_func(x):
                return (x['start_loc_key'], x['end_loc_key'], x['time_group'])

            sorted_routes = sorted(processed_routes, key=group_key_func)

            pattern_candidates = []
            for key, group_iter in groupby(sorted_routes, key=group_key_func):
                group_list = list(group_iter)
                if not group_list: continue

                # Calculate stats for this group
                frequency = len(group_list)
                avg_duration = None
                durations = [r['duration_minutes'] for r in group_list if r['duration_minutes'] is not None]
                if durations:
                    avg_duration = sum(durations) / len(durations)

                # Collect days of week this pattern was observed on
                observed_days = sorted(list(set(r['day_of_week'] for r in group_list)))

                # Get the most recent travel time for this pattern
                last_traveled = max(r['start_time'] for r in group_list)

                # Use the start/end location from the first record in the group for summary
                # (assuming locations within a group are similar enough)
                sample_record_for_loc = group_list[0]

                pattern_candidates.append({
                    "user_id": user_id,
                    "start_loc_key": key[0],
                    "end_loc_key": key[1],
                    "time_of_day_group": key[2],
                    "days_of_week": observed_days,
                    "frequency_score": float(frequency),
                    "average_duration_minutes": avg_duration,
                    "last_traveled_at": last_traveled,
                    "start_location_summary": sample_record_for_loc['original_start_loc'],
                    "end_location_summary": sample_record_for_loc['original_end_loc']
                })

            # Sort by frequency to get top N
            sorted_patterns = sorted(pattern_candidates, key=lambda x: x['frequency_score'], reverse=True)

            final_patterns = []
            for p_data in sorted_patterns[:top_n]:
                pattern_id = str(uuid.uuid4()) # Generate unique ID for the pattern instance
                # Or create a more deterministic ID:
                # pattern_id_str = f"{user_id}_{p_data['start_loc_key']}_{p_data['end_loc_key']}_{p_data['time_of_day_group']}"
                # pattern_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, pattern_id_str))

                final_patterns.append(CommonTravelPattern(
                    pattern_id=pattern_id,
                    user_id=p_data['user_id'],
                    start_location_summary=p_data['start_location_summary'],
                    end_location_summary=p_data['end_location_summary'],
                    time_of_day_group=p_data['time_of_day_group'],
                    days_of_week=p_data['days_of_week'],
                    frequency_score=p_data['frequency_score'],
                    average_duration_minutes=p_data['average_duration_minutes'],
                    last_traveled_at=p_data['last_traveled_at']
                ))

            return final_patterns

        except SQLAlchemyError as e:
            logger.error(f"Database error identifying common travel patterns for user {user_id}: {e}")
            return [] # Return empty list on DB error
        except Exception as e:
            logger.error(f"Unexpected error identifying common travel patterns for user {user_id}: {e}", exc_info=True)
            return [] # Return empty list on other errors
        finally:
            if session:
                session.close()
    async def proactively_suggest_route(self, user_id: str) -> Optional[str]:
        """
        Proactively suggests a route to the user based on their most common destination.
        For now, simulates this by logging a placeholder suggestion.
        """
        session: Session = self.Session()
        try:
            most_common_destination = self._get_most_frequent_destination(user_id)

            if not most_common_destination:
                logger.info(f"No common destination found for user {user_id} to make a proactive suggestion.")
                return None

            dest_name = f"({most_common_destination.get('latitude')}, {most_common_destination.get('longitude')})" # Simplified name

            # Query for recent negative feedback for similar suggestions
            seven_days_ago = datetime.utcnow() - timedelta(days=7)
            recent_feedback = (
                session.query(ProactiveSuggestionFeedbackLog)
                .filter(
                    ProactiveSuggestionFeedbackLog.user_id == user_id,
                    ProactiveSuggestionFeedbackLog.created_at >= seven_days_ago,
                    # Assuming suggestion_details stores the destination in a queryable way,
                    # This might need adjustment based on actual suggestion_details structure.
                    # For now, let's assume a simple match on a 'destination_name' key.
                    # ProactiveSuggestionFeedbackLog.suggestion_details['destination_name'] == dest_name, # This requires JSON path support or careful structuring
                    # A simpler approach if dest_name is stored directly or if we filter post-query:
                    # For this example, let's assume we filter for "rejected" or low ratings.
                    (ProactiveSuggestionFeedbackLog.interaction_status == "rejected") |
                    (ProactiveSuggestionFeedbackLog.user_rating <= 2) # Assuming 1 or 2 is a low rating
                )
                .order_by(ProactiveSuggestionFeedbackLog.created_at.desc())
                .all()
            )

            # Filter further if suggestion_details['destination_name'] was not used in query
            # This is a placeholder for more robust filtering based on suggestion_details content
            negative_feedback_for_destination = [
                fb for fb in recent_feedback
                if fb.suggestion_details and fb.suggestion_details.get('destination_name') == dest_name
            ]


            if negative_feedback_for_destination:
                logger.warning(
                    f"User {user_id} has recent negative feedback for suggestions to {dest_name}. "
                    f"Found {len(negative_feedback_for_destination)} relevant feedback entries. Skipping new suggestion for now."
                )
                # Potentially alter suggestion:
                # suggestion_text = f"We noticed our previous suggestions for {dest_name} weren't quite right. Would you like to try planning a route to {dest_name} manually, or provide more feedback?"
                # logger.info(f"Altered suggestion for user {user_id}: {suggestion_text}")
                # return suggestion_text
                return None # Or return an altered suggestion

            # If no significant negative feedback, proceed with suggestion
            suggestion_text = (
                f"Proactive suggestion: Traffic looks reasonable on your usual route to {dest_name}. "
                "Consider leaving soon for a smooth commute!"
            )

            new_suggestion_id = str(uuid.uuid4())

            # Log preliminary entry to ProactiveSuggestionFeedbackLog
            suggestion_log_entry = ProactiveSuggestionFeedbackLog(
                id=str(uuid.uuid4()), # Unique ID for the log entry itself
                suggestion_id=new_suggestion_id,
                user_id=user_id,
                suggestion_details={
                    "type": "proactive_route_to_common_destination",
                    "destination_name": dest_name,
                    "destination_coordinates": most_common_destination,
                    "message": suggestion_text
                },
                interaction_status="suggested", # Or "pending_feedback"
                timestamp=datetime.utcnow() # Ensure timestamp is set
            )
            session.add(suggestion_log_entry)
            session.commit()

            logger.info(f"Proactive suggestion for user {user_id} (ID: {new_suggestion_id}): {suggestion_text}")
            return suggestion_text # Or return a more structured suggestion object including the suggestion_id

        except SQLAlchemyError as e:
            logger.error(f"Database error during proactive suggestion for user {user_id}: {e}")
            if session: # Check if session was initialized
                session.rollback()
            return None
        except Exception as e:
            logger.error(f"Error in proactively_suggest_route for user {user_id}: {e}")
            if session: # Check if session was initialized
                session.rollback() # Rollback on other errors too if session was used
            return None
        finally:
            if session: # Check if session was initialized
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
            if session:
                session.close()

    async def record_suggestion_feedback(
        self,
        suggestion_id: str,
        user_id: str, # Though user_id might be redundant if suggestion_id is globally unique and links to user_id
        interaction_status: str,
        feedback_text: Optional[str] = None,
        rating: Optional[int] = None
    ) -> bool:
        """
        Records feedback for a proactive suggestion.
        Updates an existing ProactiveSuggestionFeedbackLog entry.
        """
        session: Session = self.Session()
        try:
            feedback_log_entry = (
                session.query(ProactiveSuggestionFeedbackLog)
                .filter_by(suggestion_id=suggestion_id)
                .first()
            )

            if not feedback_log_entry:
                logger.error(f"No ProactiveSuggestionFeedbackLog entry found for suggestion_id: {suggestion_id} to record feedback.")
                # Optionally, create one if it's missing and status is not 'suggested'
                # For now, we'll assume it must exist from the `proactively_suggest_route` log.
                return False

            # Verify user_id if necessary, though suggestion_id should be the primary key for lookup here.
            if feedback_log_entry.user_id != user_id:
                logger.error(
                    f"User ID mismatch for suggestion_id {suggestion_id}. "
                    f"Log entry user: {feedback_log_entry.user_id}, Provided user: {user_id}."
                )
                # Decide on handling: error out, or trust suggestion_id and update anyway.
                # For now, let's be strict.
                return False

            # Update fields
            feedback_log_entry.interaction_status = interaction_status
            feedback_log_entry.timestamp = datetime.utcnow() # Update timestamp to reflect this interaction

            if feedback_text is not None:
                feedback_log_entry.user_feedback_text = feedback_text

            if rating is not None:
                feedback_log_entry.user_rating = rating

            session.commit()
            logger.info(f"Successfully recorded feedback for suggestion_id {suggestion_id}. Status: {interaction_status}, Rating: {rating}")
            return True

        except SQLAlchemyError as e:
            logger.error(f"Database error recording suggestion feedback for suggestion_id {suggestion_id}: {e}")
            if session:
                session.rollback()
            return False
        except Exception as e:
            logger.error(f"Unexpected error recording suggestion feedback for suggestion_id {suggestion_id}: {e}")
            if session:
                session.rollback()
            return False
        finally:
            if session:
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
