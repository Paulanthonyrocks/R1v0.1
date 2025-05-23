import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import logging

# Assuming TrafficSignalService is in backend.app.services.traffic_signal_service
from backend.app.services.traffic_signal_service import TrafficSignalService
# Assuming LLMSignalAdvisor is in backend.app.services.llm_signal_advisor for type hinting/mocking
from backend.app.services.llm_signal_advisor import LLMSignalAdvisor
# Assuming ConnectionManager is in app.websocket.connection_manager
from backend.app.websocket.connection_manager import ConnectionManager

# Suppress most logging output during tests unless specifically testing for it
logging.disable(logging.CRITICAL)

# Sample data to be used across tests
sample_traffic_prediction_data = {
    "intersection_id": "intersection_A",
    "current_conditions": {"volume": 500, "speed": 30},
    "predicted_congestion": "high",
    "timestamp": "2023-10-27T10:00:00Z"
}

sample_signal_states_list = [
    {"signal_id": "s1", "current_phase": "green", "time_in_phase": 30},
    {"signal_id": "s2", "current_phase": "red", "time_in_phase": 60}
]

class TestProcessLLMSignalAdvice_Success(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_config = {"traffic_signal_controller": {}} # Basic mock config
        self.mock_connection_manager = AsyncMock(spec=ConnectionManager)
        self.mock_llm_signal_advisor = AsyncMock(spec=LLMSignalAdvisor)
        
        self.service = TrafficSignalService(
            config=self.mock_config,
            connection_manager=self.mock_connection_manager,
            llm_signal_advisor=self.mock_llm_signal_advisor
        )

        self.sample_advice = [{
            "signal_id": "s1", 
            "suggested_adjustment": "increase_green_time_main_st_by_10s",
            "reasoning": "High predicted congestion on Main St.",
            "priority": "high",
            "confidence": 0.9
        }]
        self.mock_llm_signal_advisor.get_signal_adjustment_advice.return_value = self.sample_advice[0] # Advisor returns one dict

    @patch('backend.app.services.traffic_signal_service.logger') # Patch the logger in the service module
    @patch.object(TrafficSignalService, 'get_all_signal_states', new_callable=AsyncMock)
    async def test_process_llm_signal_advice_success(self, mock_get_all_states, mock_logger_info):
        mock_get_all_states.return_value = sample_signal_states_list

        result = await self.service.process_llm_signal_advice(sample_traffic_prediction_data)

        mock_get_all_states.assert_awaited_once()
        
        self.mock_llm_signal_advisor.get_signal_adjustment_advice.assert_awaited_once_with(
            traffic_prediction=sample_traffic_prediction_data,
            signal_states=sample_signal_states_list
        )
        
        self.assertEqual(result, self.sample_advice) # Expecting a list containing the advice dict
        
        # Check for logger.info call with advice details
        # The actual log message in traffic_signal_service is:
        # logger.info(f"LLM Signal Advice received for {advice_intersection_id}: {advice_result}")
        # So we check if ANY call to info contains part of this.
        logged_info_str = ""
        for call_args in mock_logger_info.info.call_args_list:
            logged_info_str += str(call_args[0][0]) + " "
        
        self.assertIn("LLM Signal Advice received", logged_info_str)
        self.assertIn(str(self.sample_advice[0]), logged_info_str) # Check if advice dict is in log


class TestProcessLLMSignalAdvice_NoAdviceFromAdvisor(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_config = {}
        self.mock_connection_manager = AsyncMock(spec=ConnectionManager)
        self.mock_llm_signal_advisor = AsyncMock(spec=LLMSignalAdvisor)
        self.service = TrafficSignalService(self.mock_config, self.mock_connection_manager, self.mock_llm_signal_advisor)

        # Configure advisor to return an empty dict (simulating no error, but no actionable advice from LLM's perspective)
        # or a dict that would be processed by traffic_signal_service into an empty list (e.g. if it had an "error" key)
        # The code: `if advice_result and not advice_result.get("error"):`
        # If advisor returns `[]` or `None`, `advice_result` is falsy.
        # If advisor returns `{"error": ...}`, `advice_result.get("error")` is truthy.
        # If advisor returns `{}`, `advice_result` is falsy.
        # Subtask asks for advisor to return empty list `[]`. `get_signal_adjustment_advice` returns a dict or None.
        # Let's assume the "empty list" means the *final* result of process_llm_signal_advice should be empty.
        # This happens if advisor returns None, or a dict with "error", or an empty dict.
        # For this test, let's make it return an empty dict.
        self.mock_llm_signal_advisor.get_signal_adjustment_advice.return_value = {} 


    @patch('backend.app.services.traffic_signal_service.logger')
    @patch.object(TrafficSignalService, 'get_all_signal_states', new_callable=AsyncMock)
    async def test_no_advice_from_advisor(self, mock_get_all_states, mock_logger_warning):
        mock_get_all_states.return_value = sample_signal_states_list

        result = await self.service.process_llm_signal_advice(sample_traffic_prediction_data)

        self.assertEqual(result, [])
        self.mock_llm_signal_advisor.get_signal_adjustment_advice.assert_awaited_once()
        
        # Check logger.warning was called
        # Log message: f"No advice or error received from LLMSignalAdvisor for {intersection_id_log}. Response: {advice_result}. Returning empty list."
        logged_warning_str = ""
        for call_args in mock_logger_warning.warning.call_args_list:
            logged_warning_str += str(call_args[0][0]) + " "
            
        self.assertIn("No advice or error received from LLMSignalAdvisor", logged_warning_str)
        self.assertIn("Response: {}", logged_warning_str) # Empty dict was the response


class TestProcessLLMSignalAdvice_AdvisorReturnsNone(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_config = {}
        self.mock_connection_manager = AsyncMock(spec=ConnectionManager)
        self.mock_llm_signal_advisor = AsyncMock(spec=LLMSignalAdvisor)
        self.service = TrafficSignalService(self.mock_config, self.mock_connection_manager, self.mock_llm_signal_advisor)
        self.mock_llm_signal_advisor.get_signal_adjustment_advice.return_value = None

    @patch('backend.app.services.traffic_signal_service.logger')
    @patch.object(TrafficSignalService, 'get_all_signal_states', new_callable=AsyncMock)
    async def test_advisor_returns_none(self, mock_get_all_states, mock_logger_warning):
        mock_get_all_states.return_value = sample_signal_states_list

        result = await self.service.process_llm_signal_advice(sample_traffic_prediction_data)
        
        self.assertEqual(result, [])
        self.mock_llm_signal_advisor.get_signal_adjustment_advice.assert_awaited_once()
        # Check logger.warning was called
        # Log message: f"No advice or error received from LLMSignalAdvisor for {intersection_id_log}. Response: {advice_result}. Returning empty list."
        logged_warning_str = ""
        for call_args in mock_logger_warning.warning.call_args_list:
            logged_warning_str += str(call_args[0][0]) + " "
        self.assertIn("No advice or error received from LLMSignalAdvisor", logged_warning_str)
        self.assertIn("Response: None", logged_warning_str)


class TestProcessLLMSignalAdvice_AdvisorNotAvailable(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_config = {}
        self.mock_connection_manager = AsyncMock(spec=ConnectionManager)
        # Initialize TrafficSignalService with llm_signal_advisor=None
        self.service = TrafficSignalService(self.mock_config, self.mock_connection_manager, llm_signal_advisor=None)

    @patch('backend.app.services.traffic_signal_service.logger')
    # No need to patch get_all_signal_states as it won't be reached if advisor is None
    async def test_advisor_not_available(self, mock_logger_warning):
        result = await self.service.process_llm_signal_advice(sample_traffic_prediction_data)
        
        self.assertEqual(result, [])
        # Log message: "LLMSignalAdvisor not available. Cannot process signal advice request. Returning empty list."
        mock_logger_warning.warning.assert_called_once_with(
            "LLMSignalAdvisor not available. Cannot process signal advice request. Returning empty list."
        )


class TestProcessLLMSignalAdvice_AdvisorRaisesException(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_config = {}
        self.mock_connection_manager = AsyncMock(spec=ConnectionManager)
        self.mock_llm_signal_advisor = AsyncMock(spec=LLMSignalAdvisor)
        self.service = TrafficSignalService(self.mock_config, self.mock_connection_manager, self.mock_llm_signal_advisor)
        
        self.exception_message = "Test Advisor Exception"
        self.mock_llm_signal_advisor.get_signal_adjustment_advice.side_effect = Exception(self.exception_message)

    @patch('backend.app.services.traffic_signal_service.logger')
    @patch.object(TrafficSignalService, 'get_all_signal_states', new_callable=AsyncMock)
    async def test_advisor_raises_exception(self, mock_get_all_states, mock_logger_error):
        mock_get_all_states.return_value = sample_signal_states_list # This will be called before the exception

        result = await self.service.process_llm_signal_advice(sample_traffic_prediction_data)

        self.assertEqual(result, [])
        self.mock_llm_signal_advisor.get_signal_adjustment_advice.assert_awaited_once()
        # Log message: f"Error processing LLM signal advice for {intersection_id_log}: {e}"
        # We check that some error message containing the exception text was logged.
        logged_error_str = ""
        for call_args in mock_logger_error.error.call_args_list:
            logged_error_str += str(call_args[0][0]) + " "
        self.assertIn("Error processing LLM signal advice", logged_error_str)
        self.assertIn(self.exception_message, logged_error_str)


if __name__ == '__main__':
    logging.disable(logging.NOTSET) # Re-enable logging for direct runs
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
