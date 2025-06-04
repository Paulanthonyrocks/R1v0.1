import asyncio
import unittest
from unittest.mock import MagicMock, patch, call, ANY # Added ANY
import json # For checking log messages with JSON

from app.core.agent_core import AgentCore
from app.tasks.prediction_scheduler import PredictionScheduler
from app.services.personalized_routing_service import PersonalizedRoutingService, CommonTravelPattern
from app.services.analytics_service import AnalyticsService
from app.models.traffic import LocationModel
from app.models.websocket import UserSpecificConditionAlert
from datetime import datetime, timedelta # Ensure datetime and timedelta are imported

class TestAgentCore(unittest.TestCase):

    def setUp(self):
        self.mock_prediction_scheduler = MagicMock(spec=PredictionScheduler)
        self.mock_personalized_routing_service = MagicMock(spec=PersonalizedRoutingService)
        self.mock_analytics_service = MagicMock(spec=AnalyticsService) # Mock AnalyticsService

        # Configure async methods on mocks to be awaitable if they are called with await
        # For sync methods called from async, MagicMock is fine.
        self.mock_prediction_scheduler._predict_and_notify = AsyncMock(return_value=None)
        self.mock_prediction_scheduler.set_priority_locations = AsyncMock() # Make set_priority_locations an AsyncMock

        self.mock_personalized_routing_service.proactively_suggest_route = AsyncMock(return_value="Sample suggestion")

        # Configure AnalyticsService mocks
        self.mock_kpi_summary_default = {"overall_congestion_level": "LOW", "active_monitored_locations": 2, "average_speed_kmh": 60, "total_vehicle_flow_estimate": 1000}
        self.mock_alert_summary_default = {"critical_unack_alert_count": 0, "recent_critical_types": []}

        self.mock_analytics_service.get_current_system_kpis_summary.return_value = self.mock_kpi_summary_default
        self.mock_analytics_service.get_critical_alert_summary = AsyncMock(return_value=self.mock_alert_summary_default)
        self.mock_analytics_service.broadcast_operational_alert = AsyncMock()
        self.mock_analytics_service.send_user_specific_alert = AsyncMock()
        self.mock_analytics_service.predict_incident_likelihood = AsyncMock() # Added for new predictive logic

        # Mock for PersonalizedRoutingService method used in user-specific alerts
        self.mock_personalized_routing_service.get_user_common_travel_patterns = AsyncMock(return_value=[]) # Default to no patterns


        self.agent_core = AgentCore(
            prediction_scheduler=self.mock_prediction_scheduler,
            personalized_routing_service=self.mock_personalized_routing_service,
            analytics_service=self.mock_analytics_service # Pass mock AnalyticsService
        )
        # AgentCore uses the module-level logger directly, so we patch that.
        # If AgentCore took a logger instance, we'd pass a mock logger.

    def test_init_stores_services(self):
        self.assertEqual(self.agent_core.prediction_scheduler, self.mock_prediction_scheduler)
        self.assertEqual(self.agent_core.personalized_routing_service, self.mock_personalized_routing_service)
        self.assertEqual(self.agent_core.analytics_service, self.mock_analytics_service) # Verify AnalyticsService

    @patch('app.core.agent_core.logger') # Patch the logger used by AgentCore instance
    async def test_run_decision_cycle_happy_path(self, mock_agent_logger):
        sample_user_id = "test_user_123"

        # Setup mock for _load_monitored_locations and its side effect
        locations_to_monitor = [
            LocationModel(latitude=1.0, longitude=1.0),
            LocationModel(latitude=2.0, longitude=2.0)
        ]
        # _load_monitored_locations is sync and modifies an attribute on the scheduler
        self.mock_prediction_scheduler._load_monitored_locations = MagicMock()
        self.mock_prediction_scheduler.monitored_locations = locations_to_monitor # Set after load is called

        # Make _predict_and_notify an async mock
        self.mock_prediction_scheduler._predict_and_notify = MagicMock(side_effect=lambda loc: asyncio.sleep(0))


        await self.agent_core.run_decision_cycle(sample_user_id=sample_user_id)

        # Verify PredictionScheduler calls (these are removed from AgentCore's direct responsibility)
        # self.mock_prediction_scheduler._load_monitored_locations.assert_called_once()
        # self.assertEqual(self.mock_prediction_scheduler._predict_and_notify.call_count, len(locations_to_monitor))

        # Verify set_priority_locations is called
        self.mock_prediction_scheduler.set_priority_locations.assert_awaited_once()
        # We can check the argument type or structure if needed, e.g., isinstance(args[0], list)

        # Verify PersonalizedRoutingService calls
        self.mock_personalized_routing_service.proactively_suggest_route.assert_awaited_once_with(sample_user_id)

        # Verify AnalyticsService calls
        self.mock_analytics_service.get_current_system_kpis_summary.assert_called_once()
        self.mock_analytics_service.get_critical_alert_summary.assert_awaited_once() # Now async

        # Verify logging calls
        mock_agent_logger.info.assert_any_call("Starting AgentCore decision cycle...")
        # mock_agent_logger.info.assert_any_call("Loading monitored locations for prediction...") # Removed
        # ... (other prediction phase logs removed) ...
        mock_agent_logger.info.assert_any_call(f"Proactive route suggestion for user {sample_user_id}: Sample suggestion")

        mock_agent_logger.info.assert_any_call(f"AgentCore received System KPIs: {json.dumps(self.mock_kpi_summary_default, indent=2)}")
        mock_agent_logger.info.assert_any_call(f"AgentCore received Critical Alert Summary: {json.dumps(self.mock_alert_summary_default, indent=2)}")

        # Check for system status summary log
        expected_summary_log_part = "System Status Summary (for AgentCore decision):"
        self.assertTrue(any(expected_summary_log_part in call_arg[0][0] for call_arg in mock_agent_logger.info.call_args_list))

        # Default mocks should result in "no operational alert issued"
        self.mock_analytics_service.broadcast_operational_alert.assert_not_awaited()
        mock_agent_logger.info.assert_any_call("AgentCore action: System status within acceptable parameters, no new global operational alert issued by AgentCore.")

        mock_agent_logger.info.assert_any_call("AgentCore decision cycle completed.")

    @patch('app.core.agent_core.logger')
    async def test_run_decision_cycle_triggers_high_congestion_alert(self, mock_agent_logger):
        # Scenario: High congestion, no critical alerts
        high_congestion_kpis = {"overall_congestion_level": "HIGH", "average_speed_kmh": 30, "total_vehicle_flow_estimate": 2000, "active_monitored_locations": 5}
        no_critical_alerts = {"critical_unack_alert_count": 0, "recent_critical_types": []}
        self.mock_analytics_service.get_current_system_kpis_summary.return_value = high_congestion_kpis
        self.mock_analytics_service.get_critical_alert_summary.return_value = no_critical_alerts

        await self.agent_core.run_decision_cycle()

        self.mock_analytics_service.broadcast_operational_alert.assert_awaited_once_with(
            title="High System Congestion",
            message_text=ANY,
            severity="error", # Severity was updated in AgentCore logic for HIGH congestion
            suggested_actions=ANY
        )
        # Log message now includes actions, so we might need to check for part of it or use ANY if actions are complex
        self.assertTrue(any(
            "AgentCore action: Issued OPERATIONAL ALERT. Title: 'High System Congestion', Severity: error" in log_call.args[0]
            for log_call in mock_agent_logger.info.call_args_list
        ))
        self.mock_prediction_scheduler.set_priority_locations.assert_awaited_once()

    @patch('app.core.agent_core.logger')
    async def test_run_decision_cycle_triggers_critical_alerts_alert(self, mock_agent_logger):
        # Scenario: Low congestion, multiple critical alerts
        low_congestion_kpis = {"overall_congestion_level": "LOW", "average_speed_kmh": 60, "total_vehicle_flow_estimate": 1000, "active_monitored_locations": 3}
        many_critical_alerts = {"critical_unack_alert_count": 3, "recent_critical_types": ["typeA", "typeB"]}
        self.mock_analytics_service.get_current_system_kpis_summary.return_value = low_congestion_kpis
        self.mock_analytics_service.get_critical_alert_summary.return_value = many_critical_alerts

        await self.agent_core.run_decision_cycle()

        # AgentCore logic for "Multiple Critical Alerts Active" (when it's the primary trigger) sets severity to "error"
        # and "Notable Critical Alerts Active" sets to "warning".
        # This test has 3 critical alerts, so it should be "Multiple Critical Alerts Active" -> "error"
        expected_title = "Multiple Critical Alerts Active"
        expected_severity = "error"
        if low_congestion_kpis["overall_congestion_level"] != "UNKNOWN": # Check if it might combine with a low congestion message
             # If a low/medium congestion alert was also triggered, title/severity might change
             # For this test, assuming critical alerts are the dominant factor or only trigger.
             pass


        self.mock_analytics_service.broadcast_operational_alert.assert_awaited_once_with(
            title=expected_title,
            message_text=ANY,
            severity=expected_severity,
            suggested_actions=ANY
        )
        self.assertTrue(any(
             f"AgentCore action: Issued OPERATIONAL ALERT. Title: '{expected_title}', Severity: {expected_severity}" in log_call.args[0]
            for log_call in mock_agent_logger.info.call_args_list
        ))

    @patch('app.core.agent_core.logger')
    async def test_run_decision_cycle_triggers_high_congestion_and_critical_alerts(self, mock_agent_logger):
        # Scenario: High congestion AND critical alerts
        high_congestion_kpis = {"overall_congestion_level": "HIGH", "average_speed_kmh": 25, "total_vehicle_flow_estimate": 2500, "active_monitored_locations": 6}
        some_critical_alerts = {"critical_unack_alert_count": 1, "recent_critical_types": ["typeC"]} # CRITICAL_ALERT_COUNT_THRESHOLD_FOR_HIGH_CONGESTION is 0
        self.mock_analytics_service.get_current_system_kpis_summary.return_value = high_congestion_kpis
        self.mock_analytics_service.get_critical_alert_summary.return_value = some_critical_alerts

        await self.agent_core.run_decision_cycle()

        self.mock_analytics_service.broadcast_operational_alert.assert_awaited_once_with(
            title="High System Load & Critical Alerts", # This specific title is used when high congestion AND critical alerts occur
            message_text=ANY,
            severity="error", # This combination results in "error"
            suggested_actions=ANY
        )
        self.assertTrue(any(
            "AgentCore action: Issued OPERATIONAL ALERT. Title: 'High System Load & Critical Alerts', Severity: error" in log_call.args[0]
            for log_call in mock_agent_logger.info.call_args_list
        ))


    @patch('app.core.agent_core.logger')
    async def test_run_decision_cycle_no_locations(self, mock_agent_logger):
        sample_user_id = "test_user_no_loc"

        self.mock_prediction_scheduler._load_monitored_locations = MagicMock()
        self.mock_prediction_scheduler.monitored_locations = [] # Simulate no locations loaded

        self.mock_prediction_scheduler._predict_and_notify = MagicMock(side_effect=lambda loc: asyncio.sleep(0))


        await self.agent_core.run_decision_cycle(sample_user_id=sample_user_id)

        self.mock_prediction_scheduler._load_monitored_locations.assert_called_once()
        self.mock_prediction_scheduler._predict_and_notify.assert_not_called()
        mock_agent_logger.warning.assert_any_call("No monitored locations loaded by PredictionScheduler. Skipping prediction notifications.")

        self.mock_personalized_routing_service.proactively_suggest_route.assert_awaited_once_with(sample_user_id)
        # Analytics service calls should still happen
        self.mock_analytics_service.get_current_system_kpis_summary.assert_called_once()
        self.mock_analytics_service.get_critical_alert_summary.assert_awaited_once() # Now async
        mock_agent_logger.info.assert_any_call("AgentCore decision cycle completed.")


    @patch('app.core.agent_core.logger')
    async def test_run_decision_cycle_routing_suggestion_is_none(self, mock_agent_logger):
        sample_user_id = "test_user_no_suggestion"

        self.mock_prediction_scheduler._load_monitored_locations = MagicMock()
        self.mock_prediction_scheduler.monitored_locations = [LocationModel(latitude=1.0, longitude=1.0)]
        self.mock_prediction_scheduler._predict_and_notify = MagicMock(side_effect=lambda loc: asyncio.sleep(0))


        # Configure mock for proactively_suggest_route to return None
        self.mock_personalized_routing_service.proactively_suggest_route = MagicMock(return_value=asyncio.Future())
        self.mock_personalized_routing_service.proactively_suggest_route.return_value.set_result(None)


        await self.agent_core.run_decision_cycle(sample_user_id=sample_user_id)

        self.mock_personalized_routing_service.proactively_suggest_route.assert_awaited_once_with(sample_user_id)
        mock_agent_logger.info.assert_any_call(f"No proactive route suggestion generated for user {sample_user_id}.")
        # Analytics service calls should still happen
        self.mock_analytics_service.get_current_system_kpis_summary.assert_called_once()
        self.mock_analytics_service.get_critical_alert_summary.assert_awaited_once() # Now async


# Wrapper for async tests
def async_test(f):
    def wrapper(*args, **kwargs):
        # Ensure a new event loop for each async test if not using pytest-asyncio
        loop = asyncio.get_event_loop_policy().new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(f(*args, **kwargs))
        finally:
            loop.close()
        return result
    return wrapper

TestAgentCore.test_run_decision_cycle_happy_path = async_test(TestAgentCore.test_run_decision_cycle_happy_path)
TestAgentCore.test_run_decision_cycle_no_locations = async_test(TestAgentCore.test_run_decision_cycle_no_locations)
TestAgentCore.test_run_decision_cycle_routing_suggestion_is_none = async_test(TestAgentCore.test_run_decision_cycle_routing_suggestion_is_none)


# --- Tests for _determine_next_travel_prediction_time ---
@patch('app.core.agent_core.datetime') # Mock datetime within the agent_core module where it's used by the method
async def test_determine_next_travel_prediction_time_logic(self, mock_core_datetime_module): # mock_core_datetime_module is the mocked datetime in agent_core
    # Scenario 1: Pattern is for "morning_weekday", current time is Sunday evening
    current_time_sunday_eve = datetime(2023, 1, 1, 20, 0, 0) # Sunday
    # mock_core_datetime_module.now.return_value = current_time_sunday_eve # Not needed if passing current_dt directly

    pattern_mw = CommonTravelPattern(
        pattern_id="p_mw", user_id="u1", start_location_summary={}, end_location_summary={},
        time_of_day_group="morning_weekday", days_of_week=[0, 1, 2, 3, 4], frequency_score=1.0
    )
    expected_time_mw = datetime(2023, 1, 2, 8, 0, 0) # Next day (Monday) at 8 AM
    result_mw = await self.agent_core._determine_next_travel_prediction_time(pattern_mw, current_time_sunday_eve)
    self.assertEqual(result_mw, expected_time_mw)

    # Scenario 2: Pattern is for "evening_weekday", current time is Monday morning
    current_time_monday_morn = datetime(2023, 1, 2, 9, 0, 0) # Monday
    pattern_ew = CommonTravelPattern(
        pattern_id="p_ew", user_id="u1", start_location_summary={}, end_location_summary={},
        time_of_day_group="evening_weekday", days_of_week=[0, 1, 2, 3, 4], frequency_score=1.0
    )
    expected_time_ew = datetime(2023, 1, 2, 17, 0, 0) # Same day (Monday) at 5 PM
    result_ew = await self.agent_core._determine_next_travel_prediction_time(pattern_ew, current_time_monday_morn)
    self.assertEqual(result_ew, expected_time_ew)

    # Scenario 3: Pattern is "morning_weekday", current time is Monday midday (too close for today, should be next day)
    current_time_monday_midday = datetime(2023, 1, 2, 12, 0, 0) # Monday noon
    expected_time_tue_morn = datetime(2023, 1, 3, 8, 0, 0) # Tuesday 8 AM
    result_tue_morn = await self.agent_core._determine_next_travel_prediction_time(pattern_mw, current_time_monday_midday)
    self.assertEqual(result_tue_morn, expected_time_tue_morn)

    # Scenario 4: Pattern is "weekend_afternoon", current time is Friday
    current_time_friday_morn = datetime(2023, 1, 6, 10, 0, 0) # Friday
    pattern_wea = CommonTravelPattern(
        pattern_id="p_wea", user_id="u1", start_location_summary={}, end_location_summary={},
        time_of_day_group="afternoon_weekend", days_of_week=[5, 6], frequency_score=1.0
    )
    expected_time_sat_aft = datetime(2023, 1, 7, 15, 0, 0) # Saturday 3 PM (target_hour for afternoon is 15)
    result_sat_aft = await self.agent_core._determine_next_travel_prediction_time(pattern_wea, current_time_friday_morn)
    self.assertEqual(result_sat_aft, expected_time_sat_aft)

    # Scenario 5: Target prediction time is less than 1 hour in the future
    current_time_just_before = datetime(2023, 1, 2, 7, 30, 0) # Monday 7:30 AM, pattern for Mon 8 AM
    # Expected: Tuesday 8 AM, because Mon 8 AM is not > (Mon 7:30 AM + 1 hr)
    expected_time_skip_today = datetime(2023, 1, 3, 8, 0, 0)
    result_skip_today = await self.agent_core._determine_next_travel_prediction_time(pattern_mw, current_time_just_before)
    self.assertEqual(result_skip_today, expected_time_skip_today)

    # Scenario 6: No matching days in the next 7 days
    pattern_no_match_days = CommonTravelPattern(
        pattern_id="p_nomatch", user_id="u1", start_location_summary={}, end_location_summary={},
        time_of_day_group="morning_weekday", days_of_week=[], frequency_score=1.0
    )
    result_no_match = await self.agent_core._determine_next_travel_prediction_time(pattern_no_match_days, current_time_sunday_eve)
    self.assertIsNone(result_no_match)

    # Scenario 7: Pattern is today, but target hour already passed
    current_time_monday_eve = datetime(2023, 1, 2, 20, 0, 0) # Monday 8 PM
    # Pattern for Monday morning (target hour 8 AM)
    # Expected: Next Monday, Jan 9, 2023, 8 AM
    expected_next_week_morn = datetime(2023, 1, 9, 8, 0, 0)
    result_next_week_morn = await self.agent_core._determine_next_travel_prediction_time(pattern_mw, current_time_monday_eve)
    self.assertEqual(result_next_week_morn, expected_next_week_morn)

TestAgentCore.test_determine_next_travel_prediction_time_logic = async_test(test_determine_next_travel_prediction_time_logic)


@patch('app.core.agent_core.logger')
async def test_run_decision_cycle_operational_alert_with_specific_suggested_actions(self, mock_agent_logger):
    # Scenario: Severe congestion based on very low speed
    severe_congestion_kpis = {
        "overall_congestion_level": "HIGH",
        "average_speed_kmh": 10, # Very low speed -> SEVERE
        "total_vehicle_flow_estimate": 3000
    }
    no_critical_alerts = {"critical_unack_alert_count": 0, "recent_critical_types": []}
    self.mock_analytics_service.get_current_system_kpis_summary.return_value = severe_congestion_kpis
    self.mock_analytics_service.get_critical_alert_summary.return_value = no_critical_alerts

    await self.agent_core.run_decision_cycle()

    self.mock_analytics_service.broadcast_operational_alert.assert_awaited_once()
    args, kwargs = self.mock_analytics_service.broadcast_operational_alert.call_args

    self.assertEqual(kwargs['title'], "Severe System Congestion")
    self.assertEqual(kwargs['severity'], "critical")

    expected_actions = [
        "Activate Stage 3 traffic management protocols.",
        "Consider widespread dynamic rerouting for affected corridors.",
        "Notify public transit authorities of major expected delays.",
        "Prepare for potential gridlock; monitor key intersections closely."
    ]
    self.assertIsInstance(kwargs['suggested_actions'], list)
    self.assertEqual(set(kwargs['suggested_actions']), set(expected_actions))

TestAgentCore.test_run_decision_cycle_operational_alert_with_specific_suggested_actions = async_test(test_run_decision_cycle_operational_alert_with_specific_suggested_actions)


# --- Tests for Predictive User-Specific Alerts ---

@patch('app.core.agent_core.logger')
async def test_run_decision_cycle_sends_predictive_user_alert_high_likelihood(self, mock_agent_logger): # Renamed
    sample_user_id = "user_predictive_alert"
    mock_loc_summary_start = {"latitude": 34.0, "longitude": -118.0, "name": "Home"}
    mock_loc_summary_end = {"latitude": 34.1, "longitude": -118.1, "name": "Work"}
    test_pattern = CommonTravelPattern(
        pattern_id="p_predict", user_id=sample_user_id,
        start_location_summary=mock_loc_summary_start, end_location_summary=mock_loc_summary_end,
        time_of_day_group="morning_weekday", days_of_week=[0,1,2,3,4], frequency_score=5.0
    )
    self.mock_personalized_routing_service.get_user_common_travel_patterns.return_value = [test_pattern]

    # Mock _determine_next_travel_prediction_time to return a fixed future time
    # Patching the instance method for this specific test
    fixed_future_time = datetime.now() + timedelta(hours=3) # Ensure this is truly in the future for the test
    # If AgentCore.datetime.now() is patched, ensure fixed_future_time is relative to that mocked 'now'

    with patch.object(self.agent_core, '_determine_next_travel_prediction_time', new_callable=AsyncMock) as mock_determine_time:
        mock_determine_time.return_value = fixed_future_time

        # Mock predict_incident_likelihood to return high likelihood
        high_likelihood_prediction = {"likelihood_score_percent": 75, "details": "High chance of delays"}
        self.mock_analytics_service.predict_incident_likelihood.return_value = high_likelihood_prediction

        await self.agent_core.run_decision_cycle(sample_user_id=sample_user_id)

        mock_determine_time.assert_awaited_once_with(test_pattern, ANY) # ANY for current_time

        # Verify predict_incident_likelihood was called with the correct LocationModel for end_location
        self.mock_analytics_service.predict_incident_likelihood.assert_awaited_once()
        call_args_pred = self.mock_analytics_service.predict_incident_likelihood.call_args[1] # kwargs
        self.assertIsInstance(call_args_pred['location'], LocationModel)
        self.assertEqual(call_args_pred['location'].latitude, mock_loc_summary_end['latitude'])
        self.assertEqual(call_args_pred['location'].longitude, mock_loc_summary_end['longitude'])
        self.assertEqual(call_args_pred['prediction_time'], fixed_future_time)

        self.mock_analytics_service.send_user_specific_alert.assert_awaited_once()
        args, kwargs = self.mock_analytics_service.send_user_specific_alert.call_args
        self.assertEqual(kwargs['user_id'], sample_user_id)
        payload: UserSpecificConditionAlert = kwargs['notification_model']
        self.assertEqual(payload.alert_type, "predicted_disruption_on_common_route")
        self.assertEqual(payload.severity, "warning")
        self.assertIn("75%", payload.message)
        self.assertIn(mock_loc_summary_end["name"], payload.message)
        self.assertIsNotNone(payload.route_context)
        self.assertEqual(payload.route_context["pattern_id"], "p_predict")
        self.assertEqual(payload.route_context["likelihood_score_percent"], 75)

TestAgentCore.test_run_decision_cycle_sends_predictive_user_alert_high_likelihood = async_test(test_run_decision_cycle_sends_predictive_user_alert_high_likelihood)


@patch('app.core.agent_core.logger')
async def test_run_decision_cycle_no_predictive_user_alert_low_likelihood(self, mock_agent_logger): # Renamed
    sample_user_id = "user_low_congestion"
    common_patterns = [CommonTravelPattern(pattern_id="p1", user_id=sample_user_id, start_location_summary={"name":"H"}, end_location_summary={"name":"W"}, time_of_day_group="morning_weekday", days_of_week=[0,1,2,3,4], frequency_score=5.0)]
    self.mock_personalized_routing_service.get_user_common_travel_patterns.return_value = common_patterns

    low_congestion_kpis = {"overall_congestion_level": "LOW"} # Low congestion
    self.mock_analytics_service.get_current_system_kpis_summary.return_value = low_congestion_kpis

    with patch('app.core.agent_core.datetime') as mock_datetime: # Match time for pattern relevance
        mock_datetime.now.return_value = datetime(2023, 1, 2, 8, 0, 0)
        await self.agent_core.run_decision_cycle(sample_user_id=sample_user_id)

    self.mock_analytics_service.send_user_specific_alert.assert_not_awaited()

TestAgentCore.test_run_decision_cycle_no_user_specific_alert_if_low_congestion = async_test(test_run_decision_cycle_no_user_specific_alert_if_low_congestion)

@patch('app.core.agent_core.logger')
async def test_run_decision_cycle_no_user_specific_alert_if_no_common_patterns(self, mock_agent_logger):
    sample_user_id = "user_no_patterns"
    self.mock_personalized_routing_service.get_user_common_travel_patterns.return_value = [] # No patterns

    high_congestion_kpis = {"overall_congestion_level": "HIGH"} # High congestion
    self.mock_analytics_service.get_current_system_kpis_summary.return_value = high_congestion_kpis

    await self.agent_core.run_decision_cycle(sample_user_id=sample_user_id)

    self.mock_analytics_service.send_user_specific_alert.assert_not_awaited()

TestAgentCore.test_run_decision_cycle_no_user_specific_alert_if_no_common_patterns = async_test(test_run_decision_cycle_no_user_specific_alert_if_no_common_patterns)


if __name__ == '__main__':
    unittest.main()
