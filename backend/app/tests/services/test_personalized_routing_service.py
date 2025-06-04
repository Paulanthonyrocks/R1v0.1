import asyncio
import unittest
from unittest.mock import patch, MagicMock, ANY
from collections import Counter

from app.services.personalized_routing_service import PersonalizedRoutingService, RouteHistoryModel
from app.models.routing import RouteHistoryEntry # Assuming this is the correct model for entries
from app.ml.preference_learner import UserPreferenceLearner # For mock
from app.ml.route_optimizer import RouteOptimizer # For mock


class TestPersonalizedRoutingService(unittest.TestCase):

    def setUp(self):
        # Mock dependencies for PersonalizedRoutingService
        self.mock_db_url = "sqlite:///:memory:" # Not actually used due to session mocking
        self.mock_traffic_predictor = MagicMock()
        self.mock_data_cache = MagicMock()

        # Patch __init__ of dependencies if they have complex setup
        with patch.object(UserPreferenceLearner, '__init__', return_value=None), \
             patch.object(RouteOptimizer, '__init__', return_value=None):
            self.service = PersonalizedRoutingService(
                db_url=self.mock_db_url,
                traffic_predictor=self.mock_traffic_predictor,
                data_cache=self.mock_data_cache
            )

        # Mock the SQLAlchemy Session
        self.mock_session = MagicMock()
        self.service.Session = MagicMock(return_value=self.mock_session)

    def test_get_most_frequent_destination_success(self):
        user_id = "user1"
        sample_location_1 = {"latitude": 10.0, "longitude": 20.0, "name": "Work"}
        sample_location_2 = {"latitude": 30.0, "longitude": 40.0, "name": "Home"}

        # Mock RouteHistoryModel instances (what query would return)
        history_records_mocks = [
            MagicMock(spec=RouteHistoryModel, end_location=sample_location_1), # Freq: 2
            MagicMock(spec=RouteHistoryModel, end_location=sample_location_1),
            MagicMock(spec=RouteHistoryModel, end_location=sample_location_2), # Freq: 1
        ]

        # Configure the mock query chain
        self.mock_session.query(RouteHistoryModel.end_location).filter().order_by().limit().all.return_value = history_records_mocks

        result = self.service._get_most_frequent_destination(user_id, limit=3)

        self.assertEqual(result, sample_location_1)
        self.mock_session.query(RouteHistoryModel.end_location).filter(RouteHistoryModel.user_id == user_id).order_by(RouteHistoryModel.start_time.desc()).limit(3).all.assert_called_once()

    def test_get_most_frequent_destination_single_entry(self):
        user_id = "user_single"
        sample_location_1 = {"latitude": 10.0, "longitude": 20.0}
        history_records_mocks = [
            MagicMock(spec=RouteHistoryModel, end_location=sample_location_1),
        ]
        self.mock_session.query(RouteHistoryModel.end_location).filter().order_by().limit().all.return_value = history_records_mocks
        result = self.service._get_most_frequent_destination(user_id, limit=1)
        self.assertEqual(result, sample_location_1)

    def test_get_most_frequent_destination_no_history(self):
        user_id = "user_no_history"
        self.mock_session.query(RouteHistoryModel.end_location).filter().order_by().limit().all.return_value = []
        result = self.service._get_most_frequent_destination(user_id)
        self.assertIsNone(result)

    def test_get_most_frequent_destination_no_single_frequent(self):
        user_id = "user_no_frequent"
        # All destinations appear only once, and there's more than one
        history_records_mocks = [
            MagicMock(spec=RouteHistoryModel, end_location={"lat": 10, "lon": 20}),
            MagicMock(spec=RouteHistoryModel, end_location={"lat": 30, "lon": 40}),
        ]
        self.mock_session.query(RouteHistoryModel.end_location).filter().order_by().limit().all.return_value = history_records_mocks
        result = self.service._get_most_frequent_destination(user_id)
        self.assertIsNone(result)

    @patch('app.services.personalized_routing_service.logger')
    async def test_proactively_suggest_route_suggestion_generated(self, mock_logger):
        user_id = "user_proactive_test"
        common_destination = {"latitude": 12.34, "longitude": 56.78}

        # Mock _get_most_frequent_destination to return our sample destination
        with patch.object(self.service, '_get_most_frequent_destination', return_value=common_destination) as mock_get_freq_dest:
            suggestion = await self.service.proactively_suggest_route(user_id)

            mock_get_freq_dest.assert_called_once_with(user_id)
            self.assertIsNotNone(suggestion)
            self.assertIn(str(common_destination['latitude']), suggestion)
            self.assertIn(str(common_destination['longitude']), suggestion)
            self.assertIn("Proactive suggestion:", suggestion)

            # Check logger call
            mock_logger.info.assert_any_call(f"Proactive suggestion for user {user_id}: {suggestion}")

    @patch('app.services.personalized_routing_service.logger')
    async def test_proactively_suggest_route_no_common_destination(self, mock_logger):
        user_id = "user_proactive_none"

        # Mock _get_most_frequent_destination to return None
        with patch.object(self.service, '_get_most_frequent_destination', return_value=None) as mock_get_freq_dest:
            suggestion = await self.service.proactively_suggest_route(user_id)

            mock_get_freq_dest.assert_called_once_with(user_id)
            self.assertIsNone(suggestion)
            mock_logger.info.assert_any_call(f"No common destination found for user {user_id} to make a proactive suggestion.")


# Wrapper for async tests
def async_test(f):
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper

TestPersonalizedRoutingService.test_proactively_suggest_route_suggestion_generated = async_test(TestPersonalizedRoutingService.test_proactively_suggest_route_suggestion_generated)
TestPersonalizedRoutingService.test_proactively_suggest_route_no_common_destination = async_test(TestPersonalizedRoutingService.test_proactively_suggest_route_no_common_destination)


if __name__ == '__main__':
    unittest.main()


# --- New Test Class for DB-dependent tests ---
import uuid # For generating unique IDs
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker # Note: Session is already imported in PersonalizedRoutingService for type hints, but good to have here too.
# Explicitly import Base and models needed for table creation and direct querying in tests
from app.services.personalized_routing_service import Base, ProactiveSuggestionFeedbackLog, CommonTravelPattern
# RouteHistoryModel is already imported at the top by the existing test class
from app.models.traffic import LocationModel # For constructing test data for patterns


# Helper data for tests
USER_ID_DB_TEST_1 = "db_user_1"
USER_ID_DB_TEST_2 = "db_user_2"
SUGGESTION_ID_DB_TEST_1 = str(uuid.uuid4())
SUGGESTION_ID_DB_TEST_2 = str(uuid.uuid4())

class TestPersonalizedRoutingServiceWithDb(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        # Base is imported from personalized_routing_service where all relevant models are registered
        Base.metadata.create_all(self.engine)

        self.TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

        self.mock_traffic_predictor = MagicMock()
        self.mock_data_cache = MagicMock()

        # Using context managers for patching to ensure they are properly managed
        self.patcher_learner = patch('app.ml.preference_learner.UserPreferenceLearner')
        self.patcher_optimizer = patch('app.ml.route_optimizer.RouteOptimizer')

        self.MockUserPreferenceLearner = self.patcher_learner.start()
        self.MockRouteOptimizer = self.patcher_optimizer.start()

        self.mock_preference_learner = self.MockUserPreferenceLearner.return_value
        self.mock_route_optimizer = self.MockRouteOptimizer.return_value

        self.service = PersonalizedRoutingService(
            db_url="sqlite:///:memory:", # This URL is for the service's own engine if it were not overridden
            traffic_predictor=self.mock_traffic_predictor,
            data_cache=self.mock_data_cache
        )
        # Crucially, override the service's Session factory to use our in-memory test session
        self.service.Session = self.TestSessionLocal

    async def asyncTearDown(self):
        self.patcher_learner.stop()
        self.patcher_optimizer.stop()

        # Base.metadata.drop_all(self.engine) # Clean up tables - good practice
        # For in-memory, connection close/dispose might be enough and tables are dropped with engine
        async with self.engine.connect() as conn: # Ensure connection is active for drop_all
             await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()


    async def _add_suggestion_log_entry(self, session, **kwargs): # session is SQLAlchemy Session
        entry_data = {
            "id": str(uuid.uuid4()),
            "suggestion_id": str(uuid.uuid4()), # Default, can be overridden by kwargs
            "user_id": USER_ID_DB_TEST_1,
            "timestamp": datetime.utcnow(),
            "suggestion_details": {"type": "test_suggestion", "destination_name": "Test Dest"},
            "interaction_status": "suggested",
            **kwargs
        }
        entry = ProactiveSuggestionFeedbackLog(**entry_data)
        session.add(entry)
        await session.commit() # Use await for async session commit
        return entry

    async def _add_route_history_entry(self, session, **kwargs): # session is SQLAlchemy Session
        entry_data = {
            "id": str(uuid.uuid4()),
            "user_id": USER_ID_DB_TEST_1, # Default, can be overridden
            "start_location": {"latitude": 34.0, "longitude": -118.0, "name": "Start"},
            "end_location": {"latitude": 34.1, "longitude": -118.1, "name": "End"},
            "start_time": datetime.utcnow() - timedelta(hours=1),
            "end_time": datetime.utcnow(),
            "route_preference_used": "fastest",
            "road_types_used": ["highway", "street"],
            "distance_km": 10.0,
            "duration_minutes": 15.0,
            "traffic_conditions": "moderate",
            **kwargs
        }
        entry = RouteHistoryModel(**entry_data)
        session.add(entry)
        await session.commit()
        return entry

    # 1. Test ProactiveSuggestionFeedbackLog model interaction
    async def test_proactive_suggestion_feedback_log_model(self):
        async with self.TestSessionLocal() as session:
            entry = await self._add_suggestion_log_entry(session, suggestion_id=SUGGESTION_ID_DB_TEST_1, user_id=USER_ID_DB_TEST_1)

            retrieved_entry = await session.get(ProactiveSuggestionFeedbackLog, entry.id) # Use get for PK lookup
            self.assertIsNotNone(retrieved_entry)
            self.assertEqual(retrieved_entry.user_id, USER_ID_DB_TEST_1)
            self.assertEqual(retrieved_entry.interaction_status, "suggested")
            self.assertEqual(retrieved_entry.suggestion_id, SUGGESTION_ID_DB_TEST_1)

    # 2. Test proactively_suggest_route logs a suggestion
    async def test_proactively_suggest_route_logs_suggestion(self):
        common_dest = {"latitude": 34.0522, "longitude": -118.2437, "name": "Downtown LA"}
        self.service._get_most_frequent_destination = MagicMock(return_value=common_dest) # Mock the helper

        suggestion_text = await self.service.proactively_suggest_route(user_id=USER_ID_DB_TEST_1)
        self.assertIsNotNone(suggestion_text)

        async with self.TestSessionLocal() as session:
            # Query for the log entry
            # Need to be careful with created_at if there are other tests; filter more specifically
            log_entries = (await session.execute(
                select(ProactiveSuggestionFeedbackLog).filter_by(user_id=USER_ID_DB_TEST_1, interaction_status="suggested")
            )).scalars().all()

            self.assertGreater(len(log_entries), 0, "No suggestion log found")
            # Find the one most likely created by this test
            found_log = None
            for log_entry in log_entries:
                 if log_entry.suggestion_details["destination_name"] == f"({common_dest['latitude']}, {common_dest['longitude']})":
                    found_log = log_entry
                    break
            self.assertIsNotNone(found_log, "Specific suggestion log not found")
            self.assertEqual(found_log.user_id, USER_ID_DB_TEST_1)

    # 3. Test proactively_suggest_route avoids on negative feedback
    async def test_proactively_suggest_route_avoids_on_negative_feedback(self):
        common_dest = {"latitude": 35.0, "longitude": -119.0, "name": "Risky Area"}
        dest_name_key = f"({common_dest['latitude']}, {common_dest['longitude']})"

        async with self.TestSessionLocal() as session:
            await self._add_suggestion_log_entry(
                session,
                user_id=USER_ID_DB_TEST_1,
                suggestion_details={"type": "proactive_route_to_common_destination", "destination_name": dest_name_key, "destination_coordinates": common_dest},
                interaction_status="rejected",
                user_rating=1,
                created_at=datetime.utcnow() - timedelta(days=1)
            )

        self.service._get_most_frequent_destination = MagicMock(return_value=common_dest)

        suggestion_text = await self.service.proactively_suggest_route(user_id=USER_ID_DB_TEST_1)
        self.assertIsNone(suggestion_text, "Should avoid suggestion due to negative feedback")

        async with self.TestSessionLocal() as session:
            new_suggestions = (await session.execute(
                select(ProactiveSuggestionFeedbackLog).filter(
                    ProactiveSuggestionFeedbackLog.user_id == USER_ID_DB_TEST_1,
                    ProactiveSuggestionFeedbackLog.interaction_status == "suggested",
                    ProactiveSuggestionFeedbackLog.created_at > (datetime.utcnow() - timedelta(minutes=1)) # Check for very recent entries
                )
            )).scalars().all()
            self.assertEqual(len(new_suggestions), 0, "A new 'suggested' log was created despite negative feedback")

    # 4. Test record_suggestion_feedback updates log
    async def test_record_suggestion_feedback_updates_log(self):
        async with self.TestSessionLocal() as session:
            original_entry = await self._add_suggestion_log_entry(session, suggestion_id=SUGGESTION_ID_DB_TEST_2, user_id=USER_ID_DB_TEST_1, interaction_status="suggested")

        feedback_updated = await self.service.record_suggestion_feedback(
            suggestion_id=SUGGESTION_ID_DB_TEST_2,
            user_id=USER_ID_DB_TEST_1,
            interaction_status="accepted",
            feedback_text="Fantastic route!",
            rating=5
        )
        self.assertTrue(feedback_updated)

        async with self.TestSessionLocal() as session:
            updated_entry = await session.get(ProactiveSuggestionFeedbackLog, original_entry.id)
            self.assertIsNotNone(updated_entry)
            self.assertEqual(updated_entry.interaction_status, "accepted")
            self.assertEqual(updated_entry.user_feedback_text, "Fantastic route!")
            self.assertEqual(updated_entry.user_rating, 5)
            self.assertGreater(updated_entry.timestamp, original_entry.timestamp)

    # 5. Test record_suggestion_feedback not found
    async def test_record_suggestion_feedback_not_found(self):
        feedback_updated = await self.service.record_suggestion_feedback(
            suggestion_id="non_existent_suggestion_id",
            user_id=USER_ID_DB_TEST_1,
            interaction_status="accepted"
        )
        self.assertFalse(feedback_updated)

    async def test_record_suggestion_feedback_returns_false_for_user_id_mismatch(self):
        suggestion_id_for_mismatch = str(uuid.uuid4())
        original_user_id = USER_ID_DB_TEST_1
        mismatch_user_id = USER_ID_DB_TEST_2 # Different user

        async with self.TestSessionLocal() as session:
            await self._add_suggestion_log_entry(
                session,
                suggestion_id=suggestion_id_for_mismatch,
                user_id=original_user_id,
                interaction_status="suggested"
            )

        feedback_updated = await self.service.record_suggestion_feedback(
            suggestion_id=suggestion_id_for_mismatch,
            user_id=mismatch_user_id, # Attempting to update with a different user's ID
            interaction_status="accepted",
            feedback_text="Tried to accept with wrong user",
            rating=4
        )
        self.assertFalse(feedback_updated, "Feedback update should fail due to user ID mismatch.")

        # Verify the original entry was not changed
        async with self.TestSessionLocal() as session:
            unchanged_entry = await session.get(ProactiveSuggestionFeedbackLog, {"suggestion_id": suggestion_id_for_mismatch})
            # If using composite primary key or if id is different from suggestion_id, adjust lookup.
            # The _add_suggestion_log_entry uses a random uuid for `id` and passes suggestion_id separately.
            # We need to find the original entry's PK `id` if it's not the same as suggestion_id.
            # For simplicity, if `id` is the PK and it's not `suggestion_id`, this test would need adjustment
            # or _add_suggestion_log_entry should return the actual PK.
            # The current _add_suggestion_log_entry returns the created object, so we can get its PK.

            # Let's re-fetch based on suggestion_id to be sure, as that's how record_suggestion_feedback finds it.
            # The PK 'id' of ProactiveSuggestionFeedbackLog is a uuid, suggestion_id is also a uuid but indexed and unique.
            # record_suggestion_feedback queries by suggestion_id.

            # Re-querying the log entry
            log_entry_after_failed_update = (await session.execute(
                select(ProactiveSuggestionFeedbackLog).filter_by(suggestion_id=suggestion_id_for_mismatch)
            )).scalar_one_or_none()

            self.assertIsNotNone(log_entry_after_failed_update)
            self.assertEqual(log_entry_after_failed_update.user_id, original_user_id) # Still original user
            self.assertEqual(log_entry_after_failed_update.interaction_status, "suggested") # Still original status
            self.assertIsNone(log_entry_after_failed_update.user_feedback_text) # Still no feedback text
            self.assertIsNone(log_entry_after_failed_update.user_rating) # Still no rating


    # 6. Test record_route_history updates suggestion log
    async def test_record_route_history_updates_suggestion_log(self):
        suggestion_to_accept = str(uuid.uuid4())
        async with self.TestSessionLocal() as session:
            await self._add_suggestion_log_entry(session, suggestion_id=suggestion_to_accept, user_id=USER_ID_DB_TEST_1, interaction_status="suggested")

        # RouteHistoryEntry is a Pydantic model from app.models.routing
        route_entry_pydantic = RouteHistoryEntry(
            route_id=str(uuid.uuid4()), # This is id for RouteHistoryModel, not suggestion_id
            user_id=USER_ID_DB_TEST_1,
            start_location={"latitude": 30.0, "longitude": -120.0},
            end_location={"latitude": 30.1, "longitude": -120.1},
            start_time=datetime.utcnow() - timedelta(minutes=30),
            end_time=datetime.utcnow(),
            route_preference_used="fastest",
            road_types_used=["highway"],
            distance_km=5.0,
            duration_minutes=10.0,
            traffic_conditions="light"
        )
        # Manually set suggestion_id as it's not part of the Pydantic model schema
        setattr(route_entry_pydantic, 'suggestion_id', suggestion_to_accept)

        await self.service.record_route_history(route_entry_pydantic)

        async with self.TestSessionLocal() as session:
            updated_log = (await session.execute(
                select(ProactiveSuggestionFeedbackLog).filter_by(suggestion_id=suggestion_to_accept)
            )).scalar_one_or_none()
            self.assertIsNotNone(updated_log)
            self.assertEqual(updated_log.interaction_status, "accepted_and_completed")

    # 7. Test get_user_common_travel_patterns
    async def test_get_user_common_travel_patterns(self):
        now_utc = datetime.utcnow()
        # Pattern 1: Home to Work, Morning Weekday (3 times)
        m_w_start_loc = {"latitude": 34.001, "longitude": -118.001, "name": "Home"}
        m_w_end_loc = {"latitude": 34.101, "longitude": -118.101, "name": "Work"}

        async with self.TestSessionLocal() as session:
            # Ensure these are actual weekdays
            days_added = 0
            for i in range(3):
                day_offset = days_added
                while (now_utc - timedelta(days=day_offset)).weekday() >= 5: # if weekend, skip
                    days_added += 1
                    day_offset = days_added
                await self._add_route_history_entry(session, user_id=USER_ID_DB_TEST_1, start_location=m_w_start_loc, end_location=m_w_end_loc, start_time=(now_utc - timedelta(days=day_offset)).replace(hour=8, minute=0), duration_minutes=30.0 + i)
                days_added += 1 # ensure different days for variety if needed by test logic

            # Pattern 2: Work to Home, Evening Weekday (2 times)
            e_w_start_loc = {"latitude": 34.101, "longitude": -118.101, "name": "Work"}
            e_w_end_loc = {"latitude": 34.001, "longitude": -118.001, "name": "Home"}
            days_added = 0
            for i in range(2):
                day_offset = days_added
                while (now_utc - timedelta(days=day_offset)).weekday() >= 5:
                    days_added += 1
                    day_offset = days_added
                await self._add_route_history_entry(session, user_id=USER_ID_DB_TEST_1, start_location=e_w_start_loc, end_location=e_w_end_loc, start_time=(now_utc - timedelta(days=day_offset)).replace(hour=17, minute=0), duration_minutes=40.0 + i)
                days_added += 1

            # A different, less frequent pattern
            await self._add_route_history_entry(session, user_id=USER_ID_DB_TEST_1, start_location=m_w_start_loc, end_location={"latitude": 35.0, "longitude": -119.0, "name":"Gym"}, start_time=now_utc.replace(hour=13, minute=0), duration_minutes=60)


        patterns_top2 = await self.service.get_user_common_travel_patterns(user_id=USER_ID_DB_TEST_1, top_n=2)
        self.assertEqual(len(patterns_top2), 2)

        top_pattern = patterns_top2[0]
        self.assertEqual(top_pattern.user_id, USER_ID_DB_TEST_1)
        # Check rounded coords based on precision 3 used in service
        self.assertAlmostEqual(top_pattern.start_location_summary['latitude'], 34.001, places=3)
        self.assertAlmostEqual(top_pattern.end_location_summary['latitude'], 34.101, places=3)
        self.assertTrue("morning_weekday" in top_pattern.time_of_day_group)
        self.assertEqual(top_pattern.frequency_score, 3)
        self.assertAlmostEqual(top_pattern.average_duration_minutes, 31.0, places=1) # (30+31+32)/3
        self.assertTrue(all(d < 5 for d in top_pattern.days_of_week)) # Check they are weekdays

        second_pattern = patterns_top2[1]
        self.assertEqual(second_pattern.frequency_score, 2)
        self.assertTrue("evening_weekday" in second_pattern.time_of_day_group)

        patterns_top1 = await self.service.get_user_common_travel_patterns(user_id=USER_ID_DB_TEST_1, top_n=1)
        self.assertEqual(len(patterns_top1), 1)
        self.assertEqual(patterns_top1[0].frequency_score, 3)


    # 8. Test get_user_common_travel_patterns with no history
    async def test_get_user_common_travel_patterns_no_history(self):
        patterns = await self.service.get_user_common_travel_patterns(user_id="user_with_absolutely_no_history", top_n=3)
        self.assertEqual(len(patterns), 0)

    async def test_get_user_common_travel_patterns_precision_grouping(self):
        loc1_a = {"latitude": 34.0001, "longitude": -118.0001, "name": "Near Home A"}
        loc1_b = {"latitude": 34.0002, "longitude": -118.0002, "name": "Near Home B"}
        loc2 = {"latitude": 34.1000, "longitude": -118.1000, "name": "Work"}

        async with self.TestSessionLocal() as session:
            # These should group together due to 3-decimal place rounding in get_location_group_key
            await self._add_route_history_entry(session, user_id=USER_ID_DB_TEST_2, start_location=loc1_a, end_location=loc2, start_time=datetime.utcnow().replace(hour=8, minute=0))
            await self._add_route_history_entry(session, user_id=USER_ID_DB_TEST_2, start_location=loc1_b, end_location=loc2, start_time=datetime.utcnow().replace(hour=8, minute=5))

        patterns = await self.service.get_user_common_travel_patterns(user_id=USER_ID_DB_TEST_2, top_n=1)
        self.assertEqual(len(patterns), 1)
        self.assertEqual(patterns[0].frequency_score, 2)
        # Check if the summary location is one of the originals (e.g. first one added)
        self.assertAlmostEqual(patterns[0].start_location_summary['latitude'], 34.0001, places=4)
        self.assertAlmostEqual(patterns[0].start_location_summary['longitude'], -118.0001, places=4)
