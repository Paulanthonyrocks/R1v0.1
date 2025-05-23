import unittest
from unittest.mock import patch, MagicMock, ANY
from datetime import datetime
import numpy as np

# Modules to be tested or mocked
from backend.app.ml.traffic_predictor import TrafficPredictor
from backend.app.ml.llm_adapter import LLMAdapter # Imported for type hinting and potentially direct patching

class TestPredictIncidentLikelihood_Success(unittest.TestCase):
    def setUp(self):
        self.config = {
            "llm_predictor_config": {
                "model_name_or_path": "test_model",
                "temperature": 0.5,
                "max_tokens": 100
            },
            "slow_traffic_threshold": 20,
            "high_density_threshold": 50
        }
        # The TrafficPredictor will create its own LLMAdapter instance.
        # We will patch the get_prediction_and_recommendations method on that instance.
        self.predictor = TrafficPredictor(config=self.config)

        # Sample data for the call
        self.recent_traffic_data = [
            {'timestamp': datetime(2023, 1, 1, 9, 30, 0), 'average_speed': 25, 'vehicle_count': 60},
            {'timestamp': datetime(2023, 1, 1, 9, 35, 0), 'average_speed': 22, 'vehicle_count': 65}
        ]
        self.location = {"lat": 34.0, "lon": -118.0}
        self.prediction_time = datetime(2023, 1, 1, 10, 0, 0) # Sunday, 10 AM

    @patch.object(LLMAdapter, 'get_prediction_and_recommendations')
    def test_predict_incident_likelihood_success(self, mock_get_llm_recommendations):
        """Test successful prediction with valid LLM response."""
        
        # Configure the mock LLM response
        mock_llm_response = {
            "prediction": "LLM insight: Expect moderate congestion due to typical Sunday morning patterns.",
            "recommendations": ["Suggest alternative routes.", "Deploy traffic monitoring units."],
            "confidence_score": 0.88, # LLM's confidence
            # "incident_likelihood" is not directly in LLM response in traffic_predictor's design
            # "contributing_factors" are also not directly from LLM in traffic_predictor's design
        }
        mock_get_llm_recommendations.return_value = mock_llm_response

        result = self.predictor.predict_incident_likelihood(
            recent_traffic_data=self.recent_traffic_data,
            location=self.location,
            prediction_time=self.prediction_time
        )

        # 1. Assert that llm_adapter.get_prediction_and_recommendations was called
        mock_get_llm_recommendations.assert_called_once()
        call_args = mock_get_llm_recommendations.call_args[1] # Get keyword arguments
        
        # Check aspects of the user_query passed to LLM
        self.assertIn("user_query", call_args)
        self.assertIn(f"location {self.location}", call_args["user_query"])
        self.assertIn(self.prediction_time.strftime('%Y-%m-%d %H:%M:%S'), call_args["user_query"])
        # Rule-based likelihood and factors are part of the query
        # For Sunday 10 AM, no rush hour, so rule_based_likelihood = 0.1 initially
        # avg_speed = (25+22)/2 = 23.5 (not <20)
        # avg_count = (60+65)/2 = 62.5 ( >50, so high_density) -> rule_based_likelihood += 0.2 -> 0.3
        self.assertIn("Rule-based incident likelihood is 0.30", call_args["user_query"]) # 0.1 (base) + 0.2 (high_density)
        self.assertIn("Key factors identified: high_density", call_args["user_query"])
        
        # Check user_profile (default in traffic_predictor)
        self.assertIn("user_profile", call_args)
        self.assertEqual(call_args["user_profile"], {"role": "traffic_operator", "preferences": "safety, efficiency"})
        
        # 2. Assert the returned dictionary is correctly formatted
        self.assertEqual(result["location"], self.location)
        self.assertEqual(result["prediction_time"], self.prediction_time.isoformat())
        
        # Rule-based likelihood:
        # Base: 0.1. Hour 10 is not peak.
        # Avg speed = 23.5 (threshold 20). Avg count = 62.5 (threshold 50).
        # So, 0.1 (base) + 0.2 (high_density) = 0.3
        self.assertEqual(result["incident_likelihood"], 0.3) # Rule-based
        
        # Confidence score should be average of rule-based (0.5) and LLM's (0.88)
        expected_confidence = (0.5 + 0.88) / 2
        self.assertAlmostEqual(result["confidence_score"], expected_confidence, places=3)
        
        self.assertIn("high_density", result["contributing_factors"]) # From rule-based part
        
        self.assertEqual(result["recommendations"], mock_llm_response["recommendations"])
        self.assertEqual(result["llm_insights"], mock_llm_response["prediction"])
        self.assertNotIn("error", result)
        self.assertNotIn("error_llm", result)


class TestPredictIncidentLikelihood_LLMReturnsIssue(unittest.TestCase):
    def setUp(self):
        self.config = {"llm_predictor_config": {"model_name_or_path": "test_model"}}
        self.predictor = TrafficPredictor(config=self.config)
        self.recent_traffic_data = []
        self.location = {"lat": 34.0, "lon": -118.0}
        self.prediction_time = datetime(2023, 1, 1, 10, 0, 0)

    @patch.object(LLMAdapter, 'get_prediction_and_recommendations')
    def test_predict_incident_likelihood_llm_returns_none(self, mock_get_llm_recommendations):
        """Test behavior when LLM adapter returns None (e.g. internal error in adapter)."""
        mock_get_llm_recommendations.return_value = None # LLMAdapter might return None or an error dict

        result = self.predictor.predict_incident_likelihood(
            recent_traffic_data=self.recent_traffic_data,
            location=self.location,
            prediction_time=self.prediction_time
        )
        mock_get_llm_recommendations.assert_called_once()
        
        self.assertEqual(result["llm_insights"], "LLM insights not available.") # Default
        self.assertEqual(result["recommendations"], ["No specific recommendations at this time."]) # Default
        # The "error_llm" key might not be set if LLMAdapter returns None without an error structure.
        # TrafficPredictor logs a warning and proceeds with defaults if llm_response is None or has no "error" key.
        # If LLMAdapter returns `{"error": "some error"}`, then "llm_insights" would contain that.
        # This test is for when the whole response is None.

    @patch.object(LLMAdapter, 'get_prediction_and_recommendations')
    def test_predict_incident_likelihood_llm_returns_error_dict(self, mock_get_llm_recommendations):
        """Test behavior when LLM adapter returns a dictionary with an 'error' key."""
        mock_get_llm_recommendations.return_value = {"error": "LLM processing failed", "raw_response": "details"}

        result = self.predictor.predict_incident_likelihood(
            recent_traffic_data=self.recent_traffic_data,
            location=self.location,
            prediction_time=self.prediction_time
        )
        mock_get_llm_recommendations.assert_called_once()
        
        self.assertEqual(result["llm_insights"], "LLM error: details")
        self.assertEqual(result["recommendations"], ["No specific recommendations at this time."]) # Default
        self.assertIn("error_llm", result) # Should be set by TrafficPredictor if LLM returns error dict
        self.assertEqual(result["error_llm"], "LLM processing failed")


class TestPredictIncidentLikelihood_LLMAdapterNotReady(unittest.TestCase):
    def setUp(self):
        self.config = {"llm_predictor_config": {"model_name_or_path": "test_model"}}
        # We want to simulate LLMAdapter not being ready *after* TrafficPredictor initializes it.
        self.predictor = TrafficPredictor(config=self.config)
        
        # To simulate LLMAdapter not ready, we can mock its 'get_prediction_and_recommendations'
        # to behave as if the LLM is not available or not initialized.
        # The subtask mentions setting `mock_llm = False` and `model = None`.
        # LLMAdapter from Subtask 1 doesn't have `mock_llm`. It has `self.llm`.
        # We can achieve a similar effect by making `get_prediction_and_recommendations`
        # raise an error or return an error state if `self.llm` is None.

        # For this test, let's directly mock `get_prediction_and_recommendations` on the instance.
        self.mock_llm_adapter_instance_gpar = MagicMock()
        self.predictor.llm_adapter.get_prediction_and_recommendations = self.mock_llm_adapter_instance_gpar
        
        # Simulate the adapter indicating it's not ready by how it responds
        # The `predict_incident_likelihood` method doesn't check `mock_llm` or `model` attributes directly.
        # It relies on the output of `get_prediction_and_recommendations`.
        # So, we make the mocked method return an error indicating non-readiness.
        self.mock_llm_adapter_instance_gpar.return_value = {"error": "LLM model not initialized or not ready."}


    def test_llm_adapter_not_ready_scenario(self):
        """
        Test behavior when the LLM adapter signals it's not ready
        (e.g., by returning an error from get_prediction_and_recommendations).
        """
        result = self.predictor.predict_incident_likelihood(
            recent_traffic_data=[],
            location={"lat": 10, "lon": 10},
            prediction_time=datetime.now()
        )
        
        self.mock_llm_adapter_instance_gpar.assert_called_once()
        self.assertIn("error_llm", result)
        self.assertEqual(result["error_llm"], "LLM model not initialized or not ready.")
        self.assertEqual(result["llm_insights"], "LLM error: LLM model not initialized or not ready.")
        self.assertEqual(result["recommendations"], ["No specific recommendations at this time."])


class TestPredictIncidentLikelihood_LLMAdapterRaisesException(unittest.TestCase):
    def setUp(self):
        self.config = {"llm_predictor_config": {"model_name_or_path": "test_model"}}
        self.predictor = TrafficPredictor(config=self.config)
        self.recent_traffic_data = []
        self.location = {"lat": 34.0, "lon": -118.0}
        self.prediction_time = datetime(2023, 1, 1, 10, 0, 0)

    @patch.object(LLMAdapter, 'get_prediction_and_recommendations')
    def test_predict_incident_likelihood_llm_raises_exception(self, mock_get_llm_recommendations):
        """Test behavior when llm_adapter.get_prediction_and_recommendations raises an exception."""
        exception_message = "Test LLM Exception"
        mock_get_llm_recommendations.side_effect = Exception(exception_message)

        result = self.predictor.predict_incident_likelihood(
            recent_traffic_data=self.recent_traffic_data,
            location=self.location,
            prediction_time=self.prediction_time
        )

        mock_get_llm_recommendations.assert_called_once()
        
        self.assertIn("error_llm", result)
        self.assertEqual(result["error_llm"], exception_message)
        self.assertEqual(result["llm_insights"], f"Failed to retrieve insights from LLM due to an internal error.")
        self.assertEqual(result["recommendations"], ["No specific recommendations at this time."])
        # Check that the rule-based likelihood is still computed
        self.assertIn("incident_likelihood", result)
        self.assertTrue(isinstance(result["incident_likelihood"], float))


if __name__ == '__main__':
    unittest.main(argv=['first-arg-is-ignored'], exit=False)
