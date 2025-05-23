import logging
import json
from typing import Dict, List, Any

# Attempt to import the real LLMAdapter; fallback to a mock for standalone testing
try:
    from backend.app.ml.llm_adapter import LLMAdapter
except ImportError:
    # This will be used if the main LLMAdapter path is not found, e.g. during standalone testing
    logging.warning("LLMAdapter not found from backend.app.ml.llm_adapter. Using MockLLMAdapter for llm_signal_advisor.py.")
    class LLMAdapter:  # Mock LLMAdapter if the real one isn't available
        def __init__(self, model_name_or_path: str, config: Dict = None):
            self.model_name = model_name_or_path
            self.config = config if config else {}
            logging.info(f"MockLLMAdapter initialized with model: {self.model_name} and config: {self.config}")

        def get_prediction_and_recommendations(self, user_query: str, user_profile: Dict, interaction_history: List) -> Dict[str, Any]:
            logging.info(f"MockLLMAdapter.get_prediction_and_recommendations called with query: {user_query[:100]}...")
            # Simulate a response structure similar to the real LLMAdapter
            return {
                "prediction": "Mocked signal adjustment advice based on query.",
                "recommendations": [
                    "Extend green light on Main Street by 10 seconds.",
                    "Reduce red light on Cross Avenue by 5 seconds.",
                    "Monitor traffic flow for the next 15 minutes to observe impact."
                ],
                "confidence_score": 0.90,
                "details": "This is a mock response. In a real scenario, this would contain detailed reasoning."
            }

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class LLMSignalAdvisor:
    """
    Generates traffic signal adjustment advice using a Large Language Model (LLM).

    This class leverages an LLMAdapter to communicate with an LLM. It constructs
    prompts specific to traffic signal optimization based on current conditions and
    recent incidents at an intersection, then processes the LLM's response to
    provide actionable advice.
    """
    def __init__(self, llm_adapter: LLMAdapter, default_user_profile: Dict = None):
        """
        Initializes the LLMSignalAdvisor.

        Args:
            llm_adapter (LLMAdapter): An instance of the LLMAdapter which will be used
                                      to interact with the underlying language model.
            default_user_profile (Dict, optional): A dictionary representing the default
                                                   user profile to be used when querying the LLM.
                                                   This can help tailor the LLM's responses.
                                                   If None, a predefined profile for a
                                                   "traffic_signal_optimizer" is used.
        """
        self.llm_adapter = llm_adapter # Store the provided LLMAdapter instance
        self.default_user_profile = default_user_profile or { # Set up default user profile
            "role": "traffic_signal_optimizer", # Defines the persona for LLM interaction
            "expertise_level": "expert",
            "goals": ["reduce congestion", "improve safety", "minimize travel time"] # Objectives for the LLM
        }
        logging.info("LLMSignalAdvisor initialized.")

    def _construct_signal_advice_prompt(self, intersection_id: str, current_conditions: Dict, recent_incidents: List[Dict]) -> str:
        """
        Constructs a detailed prompt for the LLM to solicit signal adjustment advice.

        The prompt includes the intersection ID, current traffic conditions (serialized as JSON),
        a summary of recent incidents (if any), and a specific request for actionable advice
        on signal timing adjustments, considering objectives like minimizing wait times and
        improving flow.

        Args:
            intersection_id (str): A unique identifier for the traffic intersection.
            current_conditions (Dict): A dictionary describing the current state of traffic
                                       at the intersection (e.g., vehicle volume, queue lengths,
                                       current signal phase timings).
            recent_incidents (List[Dict]): A list of dictionaries, where each dictionary
                                           details a recent incident near the intersection
                                           (e.g., type, severity, description). Limited to the
                                           last 3 incidents for prompt conciseness.

        Returns:
            str: A formatted, multi-line string prompt ready to be sent to the LLM.
        """
        prompt = f"Intersection ID: {intersection_id}\n"
        prompt += f"Current Traffic Conditions: {json.dumps(current_conditions)}\n" # Serialize conditions to JSON string
        prompt += "Recent Incidents:\n"
        if recent_incidents:
            # Include up to the first 3 recent incidents in the prompt for brevity
            for i, incident in enumerate(recent_incidents[:3]): 
                prompt += f"  - Incident {i+1}: Type: {incident.get('type', 'N/A')}, Severity: {incident.get('severity', 'N/A')}, Description: {incident.get('description', 'N/A')[:100]}...\n" # Truncate long descriptions
        else:
            prompt += "  - No significant recent incidents.\n" # Indicate if no incidents are reported
        
        # Define the core request to the LLM
        prompt += "\nRequest: Based on the current traffic conditions and recent incidents at this intersection, "
        prompt += "provide specific, actionable advice for traffic signal timing adjustments. "
        prompt += "Consider objectives such as minimizing wait times, reducing queue lengths, and improving overall flow. "
        prompt += "If applicable, suggest changes to cycle lengths, phase splits, or coordination with nearby signals. "
        prompt += "Provide a primary recommendation and any secondary considerations or monitoring advice."

        logging.info(f"Constructed signal advice prompt for {intersection_id}: {prompt[:250]}...")
        return prompt

    def get_signal_adjustment_advice(self, 
                                     intersection_id: str, 
                                     current_conditions: Dict, 
                                     recent_incidents: List[Dict] = None, 
                                     interaction_history: List[Dict] = None
                                     ) -> Dict[str, Any]:
        """
        Retrieves traffic signal adjustment advice from the LLM.

        This method constructs a prompt using the provided intersection data,
        queries the LLM via the LLMAdapter, and then formats the LLM's response
        into a structured advice dictionary.

        Args:
            intersection_id (str): Identifier for the intersection requiring advice.
            current_conditions (Dict): Current traffic conditions at the intersection.
            recent_incidents (List[Dict], optional): A list of recent incidents that might
                                                     affect signal timing. Defaults to an empty list.
            interaction_history (List[Dict], optional): A list of previous interactions
                                                       (queries and responses) to provide context
                                                       to the LLM. Defaults to an empty list.

        Returns:
            Dict[str, Any]: A dictionary containing the structured advice.
                On success, includes keys like "intersection_id", "primary_advice",
                "detailed_recommendations", "confidence", and "additional_info".
                On failure (e.g., LLMAdapter returns an error), includes "intersection_id",
                "error", and "raw_response" (if available).
        """
        # Ensure lists are initialized if not provided, to prevent errors in prompt construction or LLM call
        if recent_incidents is None:
            recent_incidents = []
        if interaction_history is None:
            interaction_history = []

        logging.info(f"Requesting signal adjustment advice for intersection: {intersection_id}")

        # Step 1: Construct the prompt for the LLM
        prompt = self._construct_signal_advice_prompt(intersection_id, current_conditions, recent_incidents)
        
        # Step 2: Call the LLMAdapter to get a response
        # The LLMAdapter is expected to return a dictionary (already parsed from JSON if applicable within adapter)
        llm_response = self.llm_adapter.get_prediction_and_recommendations(
            user_query=prompt,
            user_profile=self.default_user_profile, # Use the default or user-set profile
            interaction_history=interaction_history
        )

        # Step 3: Process the LLM's response
        if llm_response and "error" in llm_response: # Check if the LLMAdapter itself reported an error
            logging.error(f"LLMAdapter returned an error for signal advice: {llm_response['error']}")
            return {
                "intersection_id": intersection_id,
                "error": llm_response.get("error", "Unknown error from LLM."), # Propagate error
                "raw_response": llm_response.get("raw_response") # Include raw response if available
            }
        elif not llm_response: # Handle cases where LLMAdapter might return None or empty
             logging.warning(f"LLMAdapter returned a null or empty response for {intersection_id}.")
             return {
                "intersection_id": intersection_id,
                "error": "LLMAdapter returned no response or an empty response.",
                "primary_advice": "No specific advice provided due to empty LLM response.",
                "detailed_recommendations": [],
                "confidence": 0.0,
                "additional_info": "LLM response was null or empty."
            }

        # Step 4: Format the successful LLM response into the advice structure
        advice = {
            "intersection_id": intersection_id,
            "primary_advice": llm_response.get("prediction", "No specific advice provided."), # Main advice from 'prediction' field
            "detailed_recommendations": llm_response.get("recommendations", []), # Supporting recommendations
            "confidence": llm_response.get("confidence_score", 0.0), # Confidence from LLM
            "additional_info": llm_response.get("details", "") # Any other details provided by LLM
        }
        logging.info(f"Received signal adjustment advice for {intersection_id}: {advice['primary_advice']}")
        return advice

# This block serves as an example usage or a simple test script when the file is executed directly.
if __name__ == '__main__':
    logging.info("Starting LLMSignalAdvisor test script.") # Script entry point log

    # This section demonstrates how to use the LLMSignalAdvisor with a mock adapter.
    # The try-except block for LLMAdapter import at the top of the file defines a
    # basic MockLLMAdapter if the actual one cannot be imported (e.g., due to missing dependencies
    # or path issues when running this script standalone).
    
    # Determine which LLMAdapter class to use for this test script:
    # - If the script is run where `backend.app.ml.llm_adapter` is not importable,
    #   the locally defined `LLMAdapter` (which is a mock) will be used.
    # - Otherwise, it attempts to use the imported `ActualLLMAdapter` via `MockLLMAdapterForMain`.
    if 'backend.app.ml.llm_adapter' not in globals().get('LLMAdapter', {}).__module__:
        # This condition means the 'LLMAdapter' in the global scope is the one defined locally as a mock
        # because the primary import `from backend.app.ml.llm_adapter import LLMAdapter` failed.
        MockAdapterForTest = LLMAdapter 
        logging.info("Using the local MockLLMAdapter (defined in llm_signal_advisor.py) for this test.")
    else:
        # This block executes if `from backend.app.ml.llm_adapter import LLMAdapter` was successful.
        # It defines a more specific mock that inherits from the actual LLMAdapter if needed,
        # or could simply instantiate the actual LLMAdapter if its default mock behavior is desired.
        # For this example, we create a new mock class `MockLLMAdapterForMain` that could wrap or
        # inherit from the real `LLMAdapter` to override behavior for testing.
        class MockLLMAdapterForMain(globals()['LLMAdapter']): # Inherit from the imported LLMAdapter
            def __init__(self, model_name_or_path: str, config: Dict = None):
                # Call the parent's __init__ if it's the real LLMAdapter and requires it.
                # Adjust this based on the actual LLMAdapter's __init__ signature.
                # super().__init__(model_name_or_path, config if config else {}) # Example if parent takes these
                super().__init__(model_name_or_path) # Assuming parent takes at least model_name
                logging.info(f"MockLLMAdapterForMain (wrapping actual LLMAdapter) initialized with model: {self.model_name}")

            def get_prediction_and_recommendations(self, user_query: str, user_profile: Dict, interaction_history: List) -> Dict[str, Any]:
                logging.info(f"MockLLMAdapterForMain.get_prediction_and_recommendations called: {user_query[:100]}...")
                # Provide a distinct mock response for this test path
                return {
                    "prediction": "Test advice from MockLLMAdapterForMain: Adjust Main St green phase by +5s.",
                    "recommendations": [
                        "Monitor queue length on Main St.",
                        "Check cycle time impact on Cross St."
                    ],
                    "confidence_score": 0.95,
                    "details": "This is a test response generated by MockLLMAdapterForMain in llm_signal_advisor.py."
                }
        MockAdapterForTest = MockLLMAdapterForMain
        logging.info("Using MockLLMAdapterForMain for this test (may wrap or use actual LLMAdapter structure).")

    # Instantiate the chosen mock adapter for the test
    mock_llm_adapter_instance = MockAdapterForTest(model_name_or_path="test_signal_model_via_main", config={"temp": 0.5})
    
    # Create an instance of LLMSignalAdvisor using the mock adapter
    signal_advisor = LLMSignalAdvisor(llm_adapter=mock_llm_adapter_instance)
    
    # Define sample data for the test
    sample_intersection_id = "INT-001_main_test"
    sample_current_conditions = { # Example current conditions for the intersection
        "main_street_volume": 500, # vehicles per hour
        "cross_street_volume": 200, # vehicles per hour
        "main_street_queue": 25, # number of vehicles
        "cross_street_queue": 10, # number of vehicles
        "current_cycle_length": 90, # seconds
        "main_street_green_time": 40, # seconds
        "cross_street_green_time": 25 # seconds
    }
    sample_recent_incidents = [ # Example list of recent incidents
        {"type": "minor_accident", "severity": "low", "description": "Fender bender on Main Street, east approach. Cleared.", "timestamp": "2023-10-26T10:00:00Z"},
        {"type": "congestion_alert", "severity": "medium", "description": "Unusual backup on Cross Street due to nearby event.", "timestamp": "2023-10-26T10:15:00Z"}
    ]
    sample_interaction_history = [ # Example interaction history
        {"query": "Previous advice query for INT-001_main_test", "response": "Previously advised to increase cycle length due to sustained high volume."}
    ]
    
    # Execute the main method of the advisor to get advice
    logging.info(f"Testing get_signal_adjustment_advice with sample data for {sample_intersection_id}.")
    advice_result = signal_advisor.get_signal_adjustment_advice(
        intersection_id=sample_intersection_id,
        current_conditions=sample_current_conditions,
        recent_incidents=sample_recent_incidents,
        interaction_history=sample_interaction_history
    )
    
    # Log the obtained advice for verification
    logging.info(f"Test script received advice result: {json.dumps(advice_result, indent=2)}")
    
    # Perform a basic check on the result structure
    if "error" in advice_result:
        logging.error(f"Test script encountered an error in LLMSignalAdvisor processing: {advice_result['error']}")
    elif advice_result.get("primary_advice") and advice_result.get("detailed_recommendations"):
        logging.info("Test script for LLMSignalAdvisor completed successfully. Advice received as expected.")
    else:
        logging.warning("Test script completed, but the advice result structure was not as expected.")
        
    logging.info("LLMSignalAdvisor test script finished.") # End of script log
