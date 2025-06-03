import asyncio
import unittest
from unittest.mock import MagicMock, patch, call, ANY # Added ANY
import json # For checking log messages with JSON

from app.core.agent_core import AgentCore
from app.tasks.prediction_scheduler import PredictionScheduler
from app.services.personalized_routing_service import PersonalizedRoutingService
from app.services.analytics_service import AnalyticsService # Import AnalyticsService
from app.models.traffic import LocationModel

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
        self.mock_analytics_service.get_critical_alert_summary = AsyncMock(return_value=self.mock_alert_summary_default) # Make this AsyncMock
        self.mock_analytics_service.broadcast_operational_alert = AsyncMock() # Make this AsyncMock


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
            message_text=ANY, # Or construct the exact expected message
            severity="warning"
        )
        mock_agent_logger.info.assert_any_call("AgentCore action: Issued OPERATIONAL ALERT. Title: 'High System Congestion', Severity: warning")
        self.mock_prediction_scheduler.set_priority_locations.assert_awaited_once() # Still called

    @patch('app.core.agent_core.logger')
    async def test_run_decision_cycle_triggers_critical_alerts_alert(self, mock_agent_logger):
        # Scenario: Low congestion, multiple critical alerts
        low_congestion_kpis = {"overall_congestion_level": "LOW", "average_speed_kmh": 60, "total_vehicle_flow_estimate": 1000, "active_monitored_locations": 3}
        many_critical_alerts = {"critical_unack_alert_count": 3, "recent_critical_types": ["typeA", "typeB"]}
        self.mock_analytics_service.get_current_system_kpis_summary.return_value = low_congestion_kpis
        self.mock_analytics_service.get_critical_alert_summary.return_value = many_critical_alerts

        await self.agent_core.run_decision_cycle()

        self.mock_analytics_service.broadcast_operational_alert.assert_awaited_once_with(
            title="Critical Alerts Active",
            message_text=ANY,
            severity="warning"
        )
        mock_agent_logger.info.assert_any_call("AgentCore action: Issued OPERATIONAL ALERT. Title: 'Critical Alerts Active', Severity: warning")

    @patch('app.core.agent_core.logger')
    async def test_run_decision_cycle_triggers_high_congestion_and_critical_alerts(self, mock_agent_logger):
        # Scenario: High congestion AND critical alerts
        high_congestion_kpis = {"overall_congestion_level": "HIGH", "average_speed_kmh": 25, "total_vehicle_flow_estimate": 2500, "active_monitored_locations": 6}
        some_critical_alerts = {"critical_unack_alert_count": 1, "recent_critical_types": ["typeC"]} # CRITICAL_ALERT_COUNT_THRESHOLD_FOR_HIGH_CONGESTION is 0
        self.mock_analytics_service.get_current_system_kpis_summary.return_value = high_congestion_kpis
        self.mock_analytics_service.get_critical_alert_summary.return_value = some_critical_alerts

        await self.agent_core.run_decision_cycle()

        self.mock_analytics_service.broadcast_operational_alert.assert_awaited_once_with(
            title="High System Load & Critical Alerts",
            message_text=ANY,
            severity="error"
        )
        mock_agent_logger.info.assert_any_call("AgentCore action: Issued OPERATIONAL ALERT. Title: 'High System Load & Critical Alerts', Severity: error")


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

if __name__ == '__main__':
    unittest.main()
