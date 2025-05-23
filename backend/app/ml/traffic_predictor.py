import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
import tensorflow as tf
from tensorflow import keras
from keras.models import Sequential
from keras.layers import LSTM, Dense, Dropout
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import logging
from .llm_adapter import LLMAdapter

logger = logging.getLogger(__name__)

class TrafficPredictor:
    """
    Predicts traffic conditions and incident likelihood.

    This class can use a traditional machine learning model (LSTM-based) for numerical
    predictions and is enhanced with a Large Language Model (LLM) via an
    LLMAdapter for more nuanced insights, recommendations, and qualitative predictions.
    It combines rule-based assessments, (optional) LSTM model predictions, and LLM-generated
    text to provide a comprehensive traffic forecast.
    """
    def __init__(self, config: Dict[str, Any]):
        """
        Initializes the TrafficPredictor.

        Args:
            config (Dict[str, Any]): A configuration dictionary. Expected to contain
                                     keys like "llm_predictor_config" for LLM settings
                                     (e.g., "model_name_or_path", "temperature", "max_tokens"),
                                     "lstm_sequence_length" for LSTM model, and data processing
                                     thresholds (e.g., "slow_traffic_threshold").
        """
        self.config = config
        self.model = None # Placeholder for the LSTM model, initialized in _initialize_model
        self.scaler = StandardScaler() # For scaling features for the LSTM model
        self.sequence_length = config.get("lstm_sequence_length", 10)  # Number of time steps for LSTM prediction
        
        # Initialize the LLM adapter
        llm_specific_config = config.get("llm_predictor_config", {})
        self.llm_model_name_or_path = llm_specific_config.get("model_name_or_path", "mock/default_llm_model")
        
        # Note: The LLMAdapter class (from Subtask 1) was defined with __init__(self, model_name, temperature, max_tokens).
        # The instantiation here as LLMAdapter(model_name_or_path=..., config=...) implies that
        # LLMAdapter's __init__ was (or will be) updated to handle these params, perhaps by
        # extracting temperature and max_tokens from the config dict within its own __init__.
        # For this docstring, we assume the passed llm_specific_config is structured correctly
        # for the LLMAdapter being used.
        self.llm_adapter = LLMAdapter(model_name_or_path=self.llm_model_name_or_path, config=llm_specific_config)
        
        self._initialize_model() # Initialize the LSTM model

    def _initialize_model(self):
        """Initialize the LSTM model for traffic prediction"""
        try:
            self.model = Sequential([
                LSTM(64, return_sequences=True, input_shape=(self.sequence_length, 5)),
                Dropout(0.2),
                LSTM(32),
                Dropout(0.2),
                Dense(16, activation='relu'),
                Dense(1, activation='sigmoid')  # Predict incident likelihood (0-1)
            ])
            self.model.compile(optimizer='adam', loss='binary_crossentropy', metrics=['accuracy'])
            logger.info("Traffic prediction model initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing traffic prediction model: {e}")
            self.model = None

    def prepare_features(self, traffic_data: List[Dict[str, Any]]) -> np.ndarray:
        """Prepare features for the model from raw traffic data"""
        features = []
        for data in traffic_data:
            time_features = self._extract_time_features(data['timestamp'])
            traffic_features = [
                data.get('vehicle_count', 0),
                data.get('average_speed', 0),
                data.get('congestion_score', 0)
            ]
            features.append(time_features + traffic_features)
        return np.array(features)

    def _extract_time_features(self, timestamp: datetime) -> List[float]:
        """Extract time-based features from timestamp"""
        hour_sin = np.sin(2 * np.pi * timestamp.hour / 24)
        hour_cos = np.cos(2 * np.pi * timestamp.hour / 24)
        return [hour_sin, hour_cos]

    def predict_incident_likelihood(self,
                                  recent_traffic_data: List[Dict[str, Any]],
                                  location: Dict[str, float],
                                  prediction_time: datetime,
                                  user_profile: Optional[Dict[str, Any]] = None,
                                  interaction_history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """
        Predicts the likelihood of traffic incidents and generates related insights.

        This method combines a rule-based assessment, (optional) predictions from a
        time-series model like LSTM (currently a placeholder), and insights from an
        LLM via LLMAdapter to provide a comprehensive prediction dictionary.

        Args:
            recent_traffic_data (List[Dict[str, Any]]): A list of recent traffic data points.
                Each dictionary in the list should ideally contain keys like 'timestamp',
                'average_speed', 'vehicle_count', 'congestion_score'.
            location (Dict[str, float]): A dictionary specifying the location for the prediction,
                typically with 'lat' (latitude) and 'lon' (longitude) keys.
                Example: `{"lat": 34.0522, "lon": -118.2437}`.
            prediction_time (datetime): The specific `datetime` object for which the
                prediction is being made. This is used for time-based rules and for
                informing the LLM.
            user_profile (Optional[Dict[str, Any]], optional): User profile information to
                potentially tailor LLM prompts. Defaults to a generic traffic operator profile
                if not provided.
            interaction_history (Optional[List[Dict[str, str]]], optional): Past interaction
                history (e.g., previous queries and LLM responses) to provide context to the
                LLM. Defaults to an empty list.

        Returns:
            Dict[str, Any]: A dictionary containing the prediction results, including:
                - "location": The input location dictionary.
                - "prediction_time": ISO 8601 format string of the prediction_time.
                - "incident_likelihood": A float (0.0 to 1.0) representing the predicted
                  likelihood of a traffic incident, primarily from rule-based assessment
                  in the current version.
                - "confidence_score": A float representing the confidence in the overall
                  prediction. This is initialized to a base value and can be updated
                  by averaging with the LLM's confidence if an LLM response is obtained.
                - "contributing_factors": A list of strings describing factors identified
                  by the rule-based system (e.g., "peak_hour", "slow_traffic").
                - "recommendations": A list of strings with actionable recommendations.
                  These primarily come from the LLM if available; otherwise, a default
                  message is provided.
                - "llm_insights": A string containing textual insights or predictions from
                  the LLM. Defaults to an "unavailable" message if LLM interaction fails.
                - "error_lstm" (optional): A string describing an error if the LSTM model
                  prediction (currently placeholder) encounters an issue.
                - "error_llm" (optional): A string describing an error if the LLMAdapter
                  interaction fails or the adapter returns an error.
        """
        logger.info(f"Starting incident likelihood prediction for location: {location} at {prediction_time}")

        # Initialize the output structure with default values.
        # These values will be updated based on rule-based logic, LSTM (future), and LLM insights.
        output = {
            "location": location,
            "prediction_time": prediction_time.isoformat(),
            "incident_likelihood": 0.0, # Default, to be updated by rule-based logic
            "confidence_score": 0.0,    # Default, to be updated
            "contributing_factors": [], # List of identified factors
            "recommendations": ["No specific recommendations at this time."], # Default recommendations
            "llm_insights": "LLM insights not available." # Default LLM insights
        }

        # 1. Use existing numerical model (e.g., LSTM) for prediction (currently a placeholder)
        # This section is intended for future integration of a trained LSTM model.
        # For now, it initializes placeholders for numerical prediction outputs.
        numerical_prediction = 0.0 # Placeholder for LSTM-derived likelihood
        numerical_confidence = 0.0 # Placeholder for LSTM's confidence in its prediction
        contributing_factors = [] # Factors identified by rules or numerical models (populated below)

        if self.model: # Check if the LSTM model (self.model) has been initialized
            try:
                # Note: The current LSTM model structure (`_initialize_model`) and
                # feature preparation (`prepare_features`, `_extract_time_features`)
                # are placeholders and would need significant development to be effectively
                # used for generating a single point-in-time incident likelihood.
                # This section outlines where such logic would conceptually reside.
                
                # Example of conceptual LSTM usage (currently commented out):
                # features = self.prepare_features(recent_traffic_data) # Prepare features for LSTM
                # if features.shape[0] >= self.sequence_length: # Check if enough data for a sequence
                #     sequence = features[-self.sequence_length:].reshape(1, self.sequence_length, features.shape[1])
                #     # Scaler would need to be fit during a training phase
                #     # scaled_sequence = self.scaler.transform(sequence.reshape(-1, features.shape[1])).reshape(1, self.sequence_length, features.shape[1])
                #     # numerical_prediction = self.model.predict(scaled_sequence)[0][0]
                #     # numerical_confidence = 0.6 # Example confidence from a hypothetical LSTM model
                #     logger.info(f"LSTM model (conceptual) predicted likelihood: {numerical_prediction}")
                # else:
                #     logger.warning("Not enough recent data for LSTM sequence prediction. Relying on rule-based/LLM.")
                pass # Current implementation keeps the LSTM part as a non-operational placeholder.
            except Exception as e:
                logger.error(f"Error during LSTM model prediction (conceptual block): {e}")
                output["error_lstm"] = str(e) # Capture any LSTM-specific errors if this block were active.

        # 2. Rule-based assessment: Provides a baseline likelihood and identifies contributing factors.
        # This uses heuristics based on time of day and recent traffic conditions.
        current_hour = prediction_time.hour
        rule_based_likelihood = 0.1 # Start with a base likelihood

        # Factor in time-based heuristics (e.g., rush hours)
        if 7 <= current_hour <= 9 or 16 <= current_hour <= 18: # Morning/Evening rush hours
            rule_based_likelihood += 0.3
            contributing_factors.append("peak_hour")
        
        if recent_traffic_data:
            recent_speeds = [d.get('average_speed', 100) for d in recent_traffic_data[-5:]]
            recent_counts = [d.get('vehicle_count', 0) for d in recent_traffic_data[-5:]]
            avg_speed = np.mean(recent_speeds) if recent_speeds else 100
            avg_count = np.mean(recent_counts) if recent_counts else 0

            if avg_speed < self.config.get("slow_traffic_threshold", 20):
                rule_based_likelihood += 0.2
                contributing_factors.append("slow_traffic")
            if avg_count > self.config.get("high_density_threshold", 50):
                rule_based_likelihood += 0.2
                contributing_factors.append("high_density")
        
        # Combine numerical (LSTM) and rule-based likelihoods.
        # As the LSTM part is currently a placeholder, the rule-based likelihood is the primary driver here.
        final_likelihood = min(0.95, rule_based_likelihood) # Cap likelihood at 0.95 to avoid overconfidence from rules alone
        output["incident_likelihood"] = round(final_likelihood, 3)
        output["confidence_score"] = 0.5 # Assign a base confidence for the rule-based assessment. This will be averaged with LLM's confidence later.
        output["contributing_factors"] = list(set(contributing_factors)) # Store unique factors identified.

        # 3. Construct user query for the LLM based on available data (rule-based assessment, traffic conditions).
        # This query provides context to the LLM for generating relevant insights and recommendations.
        user_query = (
            f"Predict traffic incident likelihood and provide actionable recommendations "
            f"for location {location} at {prediction_time.strftime('%Y-%m-%d %H:%M:%S')}. "
            f"Current traffic conditions: {len(recent_traffic_data)} recent data points, "
            f"average speed around {avg_speed if 'avg_speed' in locals() else 'N/A'} km/h, "
            f"vehicle count around {avg_count if 'avg_count' in locals() else 'N/A'}. "
            f"Rule-based incident likelihood is {final_likelihood:.2f}. "
            f"Key factors identified: {', '.join(output['contributing_factors'])}. "
            f"Provide specific, actionable advice for traffic management authorities and general commuters."
        )

        # Set default user profile and interaction history if not provided by the caller,
        # ensuring the LLMAdapter receives appropriate, structured input.
        user_profile = user_profile or {"role": "traffic_operator", "preferences": "safety, efficiency"}
        interaction_history = interaction_history or []

        # 4. Query the LLMAdapter for qualitative insights and enhanced recommendations.
        # This involves sending the constructed query and context to the LLM via the adapter.
        try:
            logger.info("Querying LLMAdapter for insights and recommendations.")
            llm_response = self.llm_adapter.get_prediction_and_recommendations(
                user_query=user_query,
                user_profile=user_profile,
                interaction_history=interaction_history
            )
            
            # Process the LLM's response
            if llm_response and "error" in llm_response:
                # Case: LLMAdapter indicated an error (e.g., parsing its own LLM call failed).
                logger.error(f"LLMAdapter returned an error: {llm_response['error']}")
                output["error_llm"] = llm_response['error'] # Store the specific LLM error message.
                # Use raw_response if available, otherwise the error message itself as insight.
                output["llm_insights"] = f"LLM error: {llm_response.get('raw_response', llm_response['error'])}"
            elif llm_response: 
                # Case: Successful response from LLMAdapter (already a parsed dictionary).
                output["recommendations"] = llm_response.get("recommendations", output["recommendations"]) # Use LLM recommendations or keep default.
                output["llm_insights"] = llm_response.get("prediction", output["llm_insights"]) # Use 'prediction' field from LLM as 'insights'.
                
                # Average the rule-based confidence with the LLM's confidence score, if provided by the LLM.
                if "confidence_score" in llm_response:
                    output["confidence_score"] = round((output["confidence_score"] + llm_response["confidence_score"]) / 2, 3)
                logger.info(f"LLMAdapter provided recommendations and insights. New confidence: {output['confidence_score']}")
            else:
                # Case: LLMAdapter returned None or an empty response not matching the error structure.
                logger.warning("LLMAdapter returned no response or an unexpected empty response. Using default insights/recommendations.")
                # Defaults for "llm_insights" and "recommendations" (set at the beginning) will be used.

        except Exception as e:
            # Case: An unexpected exception occurred during the call to LLMAdapter.
            logger.error(f"Error querying LLMAdapter: {e}", exc_info=True) # Log full exception info.
            output["error_llm"] = str(e) # Store the exception message.
            # Set insights to a generic error message; recommendations remain default.
            output["llm_insights"] = "Failed to retrieve insights from LLM due to an internal error."

        logger.info(f"Final prediction output: {output}") # Log the complete output before returning.
        return output
