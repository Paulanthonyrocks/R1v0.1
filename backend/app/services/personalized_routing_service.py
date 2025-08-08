import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta  # Added timedelta for time-based filtering
from sqlalchemy import Column, String, DateTime, JSON, Float, Integer, func, select
from typing import Tuple
from app.models.base import Base
from sqlalchemy.exc import SQLAlchemyError  # For more specific exception handling
import uuid  # For generating unique suggestion IDs & pattern_ids
from pydantic import BaseModel  # For CommonTravelPattern

from app.models.routing import UserRoutingProfile, RouteHistoryEntry
from app.ml.preference_learner import UserPreferenceLearner
from app.ml.route_optimizer import RouteOptimizer

logger = logging.getLogger(__name__)




from app.models.route_history import RouteHistoryModel


from app.models.proactive_suggestion_feedback_log import ProactiveSuggestionFeedbackLog


from app.models.user_profile import UserProfileModel


# Pydantic model for common travel patterns
class CommonTravelPattern(BaseModel):
    pattern_id: str
    user_id: str
    start_location_summary: Dict[
        str, Any
    ]  # e.g., {"latitude": 34.050, "longitude": -118.240, "name": "Approx Start"}
    end_location_summary: Dict[
        str, Any
    ]  # e.g., {"latitude": 34.150, "longitude": -118.340, "name": "Approx End"}
    time_of_day_group: str  # e.g., "morning_commute_weekdays", "evening_commute_weekdays", "weekend_afternoon"
    days_of_week: List[
        int
    ]  # 0=Monday, 6=Sunday (actual days pattern was observed on for this group)
    frequency_score: float  # How often this pattern is observed (e.g., count of trips)
    average_duration_minutes: Optional[float] = None
    last_traveled_at: Optional[datetime] = None


class PersonalizedRoutingService:
    def __init__(self, database_manager, traffic_predictor, data_cache):
        self._db_manager = database_manager
        self.preference_learner = UserPreferenceLearner()
        self.route_optimizer = RouteOptimizer(traffic_predictor, data_cache)

    async def initialize_tables(self):
        """Ensures the necessary tables for PersonalizedRoutingService are created."""
        if self._db_manager.async_engine:
            async with self._db_manager.async_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            logger.info(
                "PersonalizedRoutingService tables created/checked successfully."
            )
        else:
            logger.error(
                "Async database engine not available for PersonalizedRoutingService table initialization."
            )
            raise RuntimeError("Database async engine not configured.")

    async def _get_most_frequent_destination(
        self, user_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Helper method to find the most frequent destination for a user from their route history.
        """
        async with self._db_manager.get_session() as session:
            try:
                # Fetch all route history for the user
                stmt = select(RouteHistoryModel).filter(
                    RouteHistoryModel.user_id == user_id
                )
                result = await session.execute(stmt)
                history_records = result.scalars().all()

                if not history_records:
                    return None

                # Group by end_location and count occurrences
                destination_counts = {}
                for record in history_records:
                    end_loc_str = str(
                        record.end_location
                    )  # Convert dict to string for hashing
                    if end_loc_str:
                        destination_counts[end_loc_str] = (
                            destination_counts.get(end_loc_str, 0) + 1
                        )

                if not destination_counts:
                    return None

                # Find the most frequent destination
                most_frequent_dest_str = max(
                    destination_counts, key=destination_counts.get
                )

                # Convert back to original dictionary format
                # This assumes the string conversion is reversible, which it should be for simple dicts
                import json

                most_frequent_destination = json.loads(most_frequent_dest_str)
                return most_frequent_destination

            except SQLAlchemyError as e:
                logger.error(
                    f"Database error getting most frequent destination for user {user_id}: {e}"
                )
                return None
            except Exception as e:
                logger.error(
                    f"Unexpected error getting most frequent destination for user {user_id}: {e}",
                    exc_info=True,
                )
                return None

    # ... (existing methods like get_personalized_route, record_route_history, etc.)

    async def get_user_common_travel_patterns(
        self, user_id: str, top_n: int = 5, history_limit: int = 200
    ) -> List[CommonTravelPattern]:
        """
        Identifies common travel patterns for a user based on their route history.
        This version uses Python-based grouping after fetching recent routes.
        """
        async with self._db_manager.get_session() as session:
            try:
                stmt = (
                    select(RouteHistoryModel)
                    .filter(RouteHistoryModel.user_id == user_id)
                    .order_by(RouteHistoryModel.start_time.desc())
                    .limit(history_limit)
                )
                result = await session.execute(stmt)
                history_records_db = result.scalars().all()

                if not history_records_db:
                    return []

                # Helper to create a grouping key for locations (rounded lat/lon)
                def get_location_group_key(
                    loc_json: Dict[str, Any], precision: int = 3
                ) -> Optional[str]:
                    if (
                        not loc_json
                        or "latitude" not in loc_json
                        or "longitude" not in loc_json
                    ):
                        return None
                    # Ensure lat/lon are floats before rounding
                    try:
                        lat = float(loc_json["latitude"])
                        lon = float(loc_json["longitude"])
                        return f"{lat:.{precision}f}_{lon:.{precision}f}"
                    except (ValueError, TypeError):
                        return None  # Could not parse lat/lon

                # Helper to determine time of day group and day type
                def get_time_group(
                    start_time: datetime,
                ) -> Tuple[str, str]:  # (time_of_day_group, day_type_group)
                    hour = start_time.hour
                    weekday = start_time.weekday()  # Monday=0, Sunday=6

                    time_group = "night_late"  # Default
                    if 6 <= hour < 10:
                        time_group = "morning"
                    elif 10 <= hour < 16:
                        time_group = "midday"
                    elif 16 <= hour < 20:
                        time_group = "evening"
                    elif 20 <= hour < 24:
                        time_group = "night_early"

                    day_type = "weekend" if weekday >= 5 else "weekday"
                    return f"{time_group}_{day_type}", day_type

                processed_routes = []
                for record in history_records_db:
                    start_loc_key = get_location_group_key(record.start_location)
                    end_loc_key = get_location_group_key(record.end_location)

                    if not start_loc_key or not end_loc_key or not record.start_time:
                        logger.debug(
                            f"Skipping record {record.id} due to missing location or start_time."
                        )
                        continue

                    time_group, _ = get_time_group(record.start_time)

                    processed_routes.append(
                        {
                            "start_loc_key": start_loc_key,
                            "end_loc_key": end_loc_key,
                            "time_group": time_group,
                            "day_of_week": record.start_time.weekday(),
                            "duration_minutes": record.duration_minutes,
                            "start_time": record.start_time,  # Keep original for last_traveled_at
                            "original_start_loc": record.start_location,  # For summary
                            "original_end_loc": record.end_location,  # For summary
                        }
                    )

                if not processed_routes:
                    return []

                # Group by (start_loc_key, end_loc_key, time_group)
                from itertools import groupby

                def group_key_func(x):
                    return (x["start_loc_key"], x["end_loc_key"], x["time_group"])

                sorted_routes = sorted(processed_routes, key=group_key_func)

                pattern_candidates = []
                for key, group_iter in groupby(sorted_routes, key=group_key_func):
                    group_list = list(group_iter)
                    if not group_list:
                        continue

                    # Calculate stats for this group
                    frequency = len(group_list)
                    avg_duration = None
                    durations = [
                        r["duration_minutes"]
                        for r in group_list
                        if r["duration_minutes"] is not None
                    ]
                    if durations:
                        avg_duration = sum(durations) / len(durations)

                    # Collect days of week this pattern was observed on
                    observed_days = sorted(
                        list(set(r["day_of_week"] for r in group_list))
                    )

                    # Get the most recent travel time for this pattern
                    last_traveled = max(r["start_time"] for r in group_list)

                    # Use the start/end location from the first record in the group for summary
                    # (assuming locations within a group are similar enough)
                    sample_record_for_loc = group_list[0]

                    pattern_candidates.append(
                        {
                            "user_id": user_id,
                            "start_loc_key": key[0],
                            "end_loc_key": key[1],
                            "time_of_day_group": key[2],
                            "days_of_week": observed_days,
                            "frequency_score": float(frequency),
                            "average_duration_minutes": avg_duration,
                            "last_traveled_at": last_traveled,
                            "start_location_summary": sample_record_for_loc[
                                "original_start_loc"
                            ],
                            "end_location_summary": sample_record_for_loc[
                                "original_end_loc"
                            ],
                        }
                    )

                # Sort by frequency to get top N
                sorted_patterns = sorted(
                    pattern_candidates, key=lambda x: x["frequency_score"], reverse=True
                )

                final_patterns = []
                for p_data in sorted_patterns[:top_n]:
                    pattern_id = str(
                        uuid.uuid4()
                    )  # Generate unique ID for the pattern instance
                    # Or create a more deterministic ID:
                    # pattern_id_str = f"{user_id}_{p_data['start_loc_key']}_{p_data['end_loc_key']}_{p_data['time_of_day_group']}"
                    # pattern_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, pattern_id_str))

                    final_patterns.append(
                        CommonTravelPattern(
                            pattern_id=pattern_id,
                            user_id=p_data["user_id"],
                            start_location_summary=p_data["start_location_summary"],
                            end_location_summary=p_data["end_location_summary"],
                            time_of_day_group=p_data["time_of_day_group"],
                            days_of_week=p_data["days_of_week"],
                            frequency_score=p_data["frequency_score"],
                            average_duration_minutes=p_data["average_duration_minutes"],
                            last_traveled_at=p_data["last_traveled_at"],
                        )
                    )

                return final_patterns

            except SQLAlchemyError as e:
                logger.error(
                    f"Database error identifying common travel patterns for user {user_id}: {e}"
                )
                return []  # Return empty list on DB error
            except Exception as e:
                logger.error(
                    f"Unexpected error identifying common travel patterns for user {user_id}: {e}",
                    exc_info=True,
                )
                return []  # Return empty list on other errors

    async def proactively_suggest_route(self, user_id: str) -> Optional[str]:
        """
        Proactively suggests a route to the user based on their most common destination.
        For now, simulates this by logging a placeholder suggestion.
        """
        async with self._db_manager.get_session() as session:
            try:
                most_common_destination = await self._get_most_frequent_destination(
                    user_id
                )

                if not most_common_destination:
                    logger.info(
                        f"No common destination found for user {user_id} to make a proactive suggestion."
                    )
                    return None

                dest_name = f"({most_common_destination.get('latitude')}, {most_common_destination.get('longitude')})"  # Simplified name

                # Query for recent negative feedback for similar suggestions
                seven_days_ago = datetime.utcnow() - timedelta(days=7)

                stmt = (
                    select(ProactiveSuggestionFeedbackLog)
                    .filter(
                        ProactiveSuggestionFeedbackLog.user_id == user_id,
                        ProactiveSuggestionFeedbackLog.created_at >= seven_days_ago,
                        (
                            ProactiveSuggestionFeedbackLog.interaction_status
                            == "rejected"
                        )
                        | (ProactiveSuggestionFeedbackLog.user_rating <= 2),
                    )
                    .order_by(ProactiveSuggestionFeedbackLog.created_at.desc())
                )

                result = await session.execute(stmt)
                recent_feedback = result.scalars().all()

                # Filter further if suggestion_details['destination_name'] was not used in query
                negative_feedback_for_destination = [
                    fb
                    for fb in recent_feedback
                    if fb.suggestion_details
                    and fb.suggestion_details.get("destination_name") == dest_name
                ]

                if negative_feedback_for_destination:
                    logger.warning(
                        f"User {user_id} has recent negative feedback for suggestions to {dest_name}. "
                        f"Found {len(negative_feedback_for_destination)} relevant feedback entries. Skipping new suggestion for now."
                    )
                    return None

                # If no significant negative feedback, proceed with suggestion
                suggestion_text = (
                    f"Proactive suggestion: Traffic looks reasonable on your usual route to {dest_name}. "
                    "Consider leaving soon for a smooth commute!"
                )

                new_suggestion_id = str(uuid.uuid4())

                # Log preliminary entry to ProactiveSuggestionFeedbackLog
                suggestion_log_entry = ProactiveSuggestionFeedbackLog(
                    id=str(uuid.uuid4()),
                    suggestion_id=new_suggestion_id,
                    user_id=user_id,
                    suggestion_details={
                        "type": "proactive_route_to_common_destination",
                        "destination_name": dest_name,
                        "destination_coordinates": most_common_destination,
                        "message": suggestion_text,
                    },
                    interaction_status="suggested",
                    timestamp=datetime.utcnow(),
                )
                session.add(suggestion_log_entry)
                await session.commit()

                logger.info(
                    f"Proactive suggestion for user {user_id} (ID: {new_suggestion_id}): {suggestion_text}"
                )
                return suggestion_text

            except SQLAlchemyError as e:
                logger.error(
                    f"Database error during proactive suggestion for user {user_id}: {e}"
                )
                await session.rollback()
                return None
            except Exception as e:
                logger.error(
                    f"Error in proactively_suggest_route for user {user_id}: {e}"
                )
                await session.rollback()
                return None

    async def get_user_profile(self, user_id: str) -> UserRoutingProfile:
        """Get user routing profile"""
        async with self._db_manager.get_session() as session:
            try:
                profile_record = await session.get(UserProfileModel, user_id)
                if profile_record:
                    return UserRoutingProfile(**profile_record.profile_data)

                # If no profile exists, create a new one
                history = await self.get_user_route_history(user_id)
                profile = self.preference_learner.update_user_profile(user_id, history)

                # Save new profile
                session.add(
                    UserProfileModel(user_id=user_id, profile_data=profile.model_dump())
                )
                await session.commit()

                return profile

            except Exception as e:
                logger.error(f"Error getting user profile: {e}")
                await session.rollback()
                raise

    async def update_user_profile(self, user_id: str) -> None:
        """Update user profile based on route history"""
        async with self._db_manager.get_session() as session:
            try:
                # Get user's route history
                history = await self.get_user_route_history(user_id)

                # Get current profile if exists
                profile_record = await session.get(UserProfileModel, user_id)
                current_profile = None
                if profile_record:
                    current_profile = UserRoutingProfile(**profile_record.profile_data)

                # Update profile
                updated_profile = self.preference_learner.update_user_profile(
                    user_id, history, current_profile
                )

                # Save updated profile
                if profile_record:
                    profile_record.profile_data = updated_profile.model_dump()
                    profile_record.updated_at = datetime.utcnow()
                else:
                    session.add(
                        UserProfileModel(
                            user_id=user_id, profile_data=updated_profile.model_dump()
                        )
                    )

                await session.commit()

            except Exception as e:
                logger.error(f"Error updating user profile: {e}")
                await session.rollback()
                raise

    async def get_user_route_history(
        self, user_id: str, limit: int = 100
    ) -> List[RouteHistoryEntry]:
        """Get user's route history"""
        async with self._db_manager.get_session() as session:
            try:
                stmt = (
                    select(RouteHistoryModel)
                    .filter(RouteHistoryModel.user_id == user_id)
                    .order_by(RouteHistoryModel.start_time.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                history_records = result.scalars().all()

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
                        feedback=record.feedback,
                    )
                    for record in history_records
                ]

            except Exception as e:
                logger.error(f"Error getting user route history: {e}")
                raise

    async def record_suggestion_feedback(
        self,
        suggestion_id: str,
        user_id: str,
        interaction_status: str,
        feedback_text: Optional[str] = None,
        rating: Optional[int] = None,
    ) -> bool:
        """
        Records feedback for a proactive suggestion.
        Updates an existing ProactiveSuggestionFeedbackLog entry.
        """
        async with self._db_manager.get_session() as session:
            try:
                stmt = select(ProactiveSuggestionFeedbackLog).filter_by(
                    suggestion_id=suggestion_id
                )
                feedback_log_entry = await session.execute(stmt)
                feedback_log_entry = feedback_log_entry.scalar_one_or_none()

                if not feedback_log_entry:
                    logger.error(
                        f"No ProactiveSuggestionFeedbackLog entry found for suggestion_id: {suggestion_id} to record feedback."
                    )
                    return False

                if feedback_log_entry.user_id != user_id:
                    logger.error(
                        f"User ID mismatch for suggestion_id {suggestion_id}. "
                        f"Log entry user: {feedback_log_entry.user_id}, Provided user: {user_id}."
                    )
                    return False

                feedback_log_entry.interaction_status = interaction_status
                feedback_log_entry.timestamp = datetime.utcnow()

                if feedback_text is not None:
                    feedback_log_entry.user_feedback_text = feedback_text

                if rating is not None:
                    feedback_log_entry.user_rating = rating

                await session.commit()
                logger.info(
                    f"Successfully recorded feedback for suggestion_id {suggestion_id}. Status: {interaction_status}, Rating: {rating}"
                )
                return True

            except SQLAlchemyError as e:
                logger.error(
                    f"Database error recording suggestion feedback for suggestion_id {suggestion_id}: {e}"
                )
                await session.rollback()
                return False
            except Exception as e:
                logger.error(
                    f"Unexpected error recording suggestion feedback for suggestion_id {suggestion_id}: {e}"
                )
                await session.rollback()
                return False

    async def get_route_history_analytics(self, user_id: str, limit: int = 20) -> dict:
        """
        Compute analytics on a user's route history: most common routes, time-of-day patterns, etc.
        """
        async with self._db_manager.get_session() as session:
            try:
                stmt = (
                    select(RouteHistoryModel)
                    .filter(RouteHistoryModel.user_id == user_id)
                    .order_by(RouteHistoryModel.start_time.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                history_records = result.scalars().all()

                if not history_records:
                    return {"message": "No route history found."}

                # Most common start-end pairs
                from collections import Counter, defaultdict

                route_pairs = [
                    (str(r.start_location), str(r.end_location))
                    for r in history_records
                ]
                most_common_routes = Counter(route_pairs).most_common(3)

                # Time-of-day histogram
                hour_counts = defaultdict(int)
                for r in history_records:
                    if r.start_time:
                        hour_counts[r.start_time.hour] += 1
                time_of_day_histogram = [hour_counts.get(h, 0) for h in range(24)]

                # Average distance and duration
                avg_distance = sum(r.distance_km or 0 for r in history_records) / len(
                    history_records
                )
                avg_duration = sum(
                    r.duration_minutes or 0 for r in history_records
                ) / len(history_records)

                return {
                    "most_common_routes": [
                        {"start": s, "end": e, "count": c}
                        for ((s, e), c) in most_common_routes
                    ],
                    "time_of_day_histogram": time_of_day_histogram,
                    "average_distance_km": round(avg_distance, 2),
                    "average_duration_min": round(avg_duration, 1),
                    "total_routes_analyzed": len(history_records),
                }
            except Exception as e:
                logger.error(f"Error computing route history analytics: {e}")
                raise
