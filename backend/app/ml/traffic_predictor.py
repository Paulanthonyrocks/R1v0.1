import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler # Assuming StandardScaler is used for feature scaling
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)

# Attempt to import tensorflow gracefully
try:
    import tensorflow as tf
except Exception as e:
    tf = None
    logger.error(f"Failed to import tensorflow. ML features will be disabled. Error: {e}")


class TrafficPredictor:
    def __init__(self, config: Dict[str, Any], model_object=None):
        self.config = config
        self.model = model_object
        # Initialize scaler. In a production scenario, the scaler should be fitted
        # on the training data and saved/loaded along with the model to ensure consistency.
        self.scaler = StandardScaler() 
        self.sequence_length = 10  # Number of time steps to use for prediction

        # Load pre-trained model if path is provided and no model object is given
        if self.model is None:
            # Use get with default None to avoid KeyError if 'model_path' is missing
            model_path = self.config.get("traffic_predictor_model_path", None) 
            if model_path:
                self.load_model(model_path) # Attempt to load the model from the specified path
            else:
                logger.warning(
                    "No 'traffic_predictor_model_path' provided in config or model_object provided. Model will not be loaded."
                )
                # Optionally initialize a dummy model or set self.model to None explicitly
                # If you intend to train the model later, you should initialize it here
                # self._initialize_model() # Initialize a model even if path is not given, allows training later
                pass # Do nothing if no model path or object is provided, self.model remains None

    def _initialize_model(self):
        """Initialize the LSTM model for traffic prediction"""
        if tf is None:
            logger.error("TensorFlow is not available. Cannot initialize model.")
            self.model = None
            return

        try:
            # Check if model is already initialized to prevent re-initialization
            # This method is typically called when training data is available and no model is loaded
            if self.model is not None:
                logger.info("Model already initialized.")
                return

            self.model = tf.keras.Sequential()
            # Input shape: (sequence_length, number_of_features)
            # Based on prepare_features, we have 14 features
            self.model.add(
                tf.keras.layers.LSTM(
                    64, return_sequences=True, input_shape=(self.sequence_length, 14)
                )
            )
            self.model.add(tf.keras.layers.Dropout(0.2))
            self.model.add(tf.keras.layers.LSTM(32))
            self.model.add(tf.keras.layers.Dropout(0.2))
            self.model.add(tf.keras.layers.Dense(16, activation="relu"))
            self.model.add(
                tf.keras.layers.Dense(1, activation="sigmoid")
            )  # Predict incident likelihood (0-1)
            self.model.compile(
                optimizer="adam", loss="binary_crossentropy", metrics=["accuracy"]
            )
            logger.info("Traffic prediction model initialized successfully")
        except Exception as e:
            logger.error(f"Error initializing traffic prediction model: {e}")
            self.model = None

    def prepare_features(self, traffic_data: pd.DataFrame) -> pd.DataFrame:
        """Prepare features for the model from raw traffic data"""
        if traffic_data.empty:
            logger.warning("No data provided to prepare_features.")
            # Return a DataFrame with the expected columns and 0 values for shape consistency
            expected_columns = [
                "hour_sin", "hour_cos", "day_of_week_sin", "day_of_week_cos", "is_weekend",
                "vehicle_count", "average_speed", "congestion_score",
                "weather_conditions_temperature", "weather_conditions_precipitation",
                "truck_percentage", "road_type_major_artery", "road_type_highway",
                "road_type_local_road"
            ]
            return pd.DataFrame(columns=expected_columns)

        # Ensure timestamp is datetime and handle potential errors
        try:
            traffic_data["timestamp"] = pd.to_datetime(traffic_data["timestamp"], errors='coerce')
            traffic_data.dropna(subset=["timestamp"], inplace=True) # Drop rows where timestamp parsing failed
        except Exception as e:
            logger.error(f"Error converting timestamp in prepare_features: {e}")
            # Return empty DataFrame or handle based on requirements
            expected_columns = [
                "hour_sin", "hour_cos", "day_of_week_sin", "day_of_week_cos", "is_weekend",
                "vehicle_count", "average_speed", "congestion_score",
                "weather_conditions_temperature", "weather_conditions_precipitation",
                "truck_percentage", "road_type_major_artery", "road_type_highway",
                "road_type_local_road"
            ]
            return pd.DataFrame(columns=expected_columns)

        # Time-based features
        traffic_data["hour_sin"] = np.sin(
            2 * np.pi * traffic_data["timestamp"].dt.hour / 24
        )
        traffic_data["hour_cos"] = np.cos(
            2 * np.pi * traffic_data["timestamp"].dt.hour / 24
        )
        traffic_data["day_of_week_sin"] = np.sin(
            2 * np.pi * traffic_data["timestamp"].dt.dayofweek / 7
        )
        traffic_data["day_of_week_cos"] = np.cos(
            2 * np.pi * traffic_data["timestamp"].dt.dayofweek / 7
        )
        # Ensure 'is_weekend' is calculated correctly if not present
        if 'is_weekend' not in traffic_data.columns:
             traffic_data["is_weekend"] = (traffic_data["timestamp"].dt.dayofweek >= 5).astype(int)
        else:
             traffic_data["is_weekend"] = traffic_data["is_weekend"].astype(int)

        # One-hot encode road_type, handling potential missing column
        if "road_type" in traffic_data.columns:
            traffic_data = pd.get_dummies(
                traffic_data, columns=["road_type"], prefix="road_type"
            )
        else:
             logger.warning("Missing 'road_type' column in traffic data for prepare_features.")
             # Add default road type columns with 0 if missing
             for rt in ["major_artery", "highway", "local_road"]:
                 traffic_data[f"road_type_{rt}"] = 0

        # Select features for the model
        # Ensure all expected road_type columns are present, fill with 0 if not
        expected_road_types = ["major_artery", "highway", "local_road"]
        for rt in expected_road_types:
            col_name = f"road_type_{rt}"
            if col_name not in traffic_data.columns:
                traffic_data[col_name] = 0

        # List of expected feature columns. Add defaults if they are missing.
        expected_features = [
            "hour_sin", "hour_cos", "day_of_week_sin", "day_of_week_cos", "is_weekend",
            "vehicle_count", "average_speed", "congestion_score",
            "weather_conditions_temperature", "weather_conditions_precipitation",
            "truck_percentage", "road_type_major_artery", "road_type_highway",
            "road_type_local_road"
        ]

        for col in expected_features:
            if col not in traffic_data.columns:
                 logger.warning(f"Missing feature column: {col}. Filling with 0.")
                 traffic_data[col] = 0 # Use 0 as a default for simplicity

        # Ensure the order of columns is consistent
        features = traffic_data[expected_features]

        return features

    # Changed from async to sync for simplicity in this initial ML implementation
    # Async might be needed if model inference is a slow, non-blocking operation
    def predict_incident_likelihood(
        self,
        recent_traffic_data: List[Dict[str, Any]],
        location: Dict[str, Any],
        prediction_time: datetime,
    ) -> Dict[str, Any]:
        """
        Predict the likelihood of traffic incidents using the trained model.
        This is a placeholder implementation.
        """
        # This is a placeholder implementation.
        # In a real-world scenario, this method would:
        # 1. Preprocess `recent_traffic_data` into the format expected by your model.
        # 2. Use the loaded ML model (`self.model`) to make a prediction.
        # 3. Incorporate `location` and `prediction_time` into features if needed.
        # 4. Return a prediction result.

        if self.model is None:
            logger.warning(
                "Model not loaded or trained. Returning rule-based prediction."
            )
            # In a real implementation, if the model is not available,
            # you might raise an error or log a critical issue, not just fall back.
            # Fallback to rule-based if model is not available
            return self._rule_based_prediction(
                 location, prediction_time, pd.DataFrame(recent_traffic_data) # Pass data for rule-based
            )

        try:
            # Convert list of dicts to DataFrame
            input_features_df = pd.DataFrame(recent_traffic_data)
            
            if input_features_df.empty:
                 logger.warning("No recent traffic data provided for prediction. Using default values.")
                 # Create a dummy row with default values for feature preparation
                 dummy_data = {
                     "timestamp": prediction_time,
                     "latitude": location.get("latitude", 0.0),
                     "longitude": location.get("longitude", 0.0),
                     "vehicle_count": 0,
                     "average_speed": 0.0,
                     "congestion_level": 0.0,
                     "congestion_score": 0.0,
                     "processing_timestamp": datetime.now(timezone.utc),
                     "status": "simulated",
                     "hour_of_day": prediction_time.hour,
                     "day_of_week": prediction_time.weekday(),
                     "is_weekend": prediction_time.weekday() >= 5,
                     "road_type": "major_artery",
                     "weather_conditions_temperature": 20.0,
                     "weather_conditions_precipitation": 0.0,
                     "truck_percentage": 0.0,
                     "is_outlier": False,
                     "incident_occurred": 0,
                 }
                 input_features_df = pd.DataFrame([dummy_data])

            # 1. Preprocess `recent_traffic_data` into the format expected by your model.
            processed_features = self.prepare_features(input_features_df.copy())
            
            # Ensure scaled_features has the correct shape for the scaler transform
            if processed_features.empty:
                 logger.warning("Processed features is empty after prepare_features.")
                 # Handle this case gracefully, possibly return a low likelihood or an error status
                 return {"incident_likelihood": 0.0, "error": "Empty processed features"}

            # Scale features
            # IMPORTANT: In a real production system, the scaler should be fitted
            # only ONCE during training on the training data, and then saved
            # and loaded along with the model. Fitting the scaler here on potentially
            # a small amount of recent data is incorrect for production use.
            # This block is a temporary fix for initial setup where the scaler might not be loaded.
            # You should load the fitted scaler in __init__ if the model is loaded.
            if not hasattr(self.scaler, 'scale_') or self.scaler.scale_ is None: # Check if scaler is fitted
                 logger.warning("Scaler not fitted. Fitting with dummy data for prediction.")
                 # Fit with dummy data or data from a known distribution if scaler is not loaded
                 self.scaler.fit(np.zeros((1, processed_features.shape[1])))
                 
            scaled_features = self.scaler.transform(processed_features)

            # Ensure we have enough data points for the sequence length
            if len(scaled_features) < self.sequence_length:
                #logger.debug(f"Not enough recent traffic data ({len(scaled_features)}) for sequence length ({self.sequence_length}). Padding with zeros.")
                # Pad with zeros at the beginning
                padding_needed = self.sequence_length - len(scaled_features)
                padding_array = np.zeros((padding_needed, scaled_features.shape[1]))
                scaled_features = np.vstack((padding_array, scaled_features))
            
            # Take the last 'sequence_length' data points
            scaled_features_sequence = scaled_features[-self.sequence_length:]
            
            # Reshape for LSTM: (samples, timesteps, features) - Batch size of 1
            scaled_features_reshaped = np.expand_dims(scaled_features_sequence, axis=0)

            # 2. Use the loaded ML model (`self.model`) to make a prediction.
            # Make prediction
            prediction = self.model.predict(scaled_features_reshaped)
            
            # Assuming the model outputs a single value for likelihood
            incident_likelihood = float(prediction[0][0])

            confidence_score = 0.8 # Placeholder, could be derived from model or other sources

            return {
                "location": location,
                # 3. Post-process the model's output to generate the prediction result dictionary.
                # Structure the output to match the expected format.
                "prediction_time": prediction_time.isoformat(),
                "incident_likelihood": round(incident_likelihood, 3),
                "confidence_score": confidence_score,
                "contributing_factors": self._get_contributing_factors(
                    input_features_df.iloc[-1] if not input_features_df.empty else pd.Series(), incident_likelihood
                ),
                "recommendations": self._generate_recommendations(
                    incident_likelihood,
                    self._get_contributing_factors(
                        input_features_df.iloc[-1] if not input_features_df.empty else pd.Series(), incident_likelihood
                    ),
                ),
            }

        except Exception as e:
            logger.error(f"Error predicting incident likelihood with ML model: {e}", exc_info=True)
            # Fallback to rule-based if ML prediction fails
            return self._rule_based_prediction(
                 location, prediction_time, pd.DataFrame(recent_traffic_data) # Pass data for rule-based
            )

    def _rule_based_prediction(
        self,
        location: Dict[str, Any],
        prediction_time: datetime,
        input_features: pd.DataFrame,
    ) -> Dict[str, Any]:
        """Fallback rule-based prediction if ML model is not available or prediction fails."""
        current_hour = prediction_time.hour
        # Safely get values, defaulting to 0.0/0 if input_features is empty or column is missing
        # Use iloc[-1] to get the latest data point if available
        latest_data = input_features.iloc[-1] if not input_features.empty else pd.Series()

        avg_speed = latest_data.get("average_speed", 0.0)
        vehicle_count = latest_data.get("vehicle_count", 0)
        congestion_score = latest_data.get("congestion_score", 0.0)
        is_weekend = latest_data.get("is_weekend", 0) # Assuming 0 or 1
        precipitation = latest_data.get("weather_conditions_precipitation", 0.0)

        base_likelihood = 0.05 # Start with a lower base

        # Factors increasing likelihood
        if 7 <= current_hour <= 10 or 16 <= current_hour <= 19: # Expanded peak hours slightly
            base_likelihood += 0.2
        if avg_speed > 0 and avg_speed < 20: # Check avg_speed > 0 to avoid division by zero or issues with default 0
            base_likelihood += 0.25
        if vehicle_count > 60:
            base_likelihood += 0.2
        if congestion_score > 50:
             base_likelihood += 0.25
        if is_weekend:
             base_likelihood += 0.1 # Slightly higher chance on weekends due to different patterns
        if precipitation > 0.2: # Check for significant precipitation
             base_likelihood += 0.15


        final_likelihood = min(0.95, base_likelihood) # Cap at 0.95
        final_likelihood = max(0.05, final_likelihood) # Ensure a minimum likelihood

        factors = []
        if 7 <= current_hour <= 10 or 16 <= current_hour <= 19:
            factors.append("peak_hour")
        if avg_speed > 0 and avg_speed < 20:
            factors.append("low_speed")
        if vehicle_count > 60:
            factors.append("high_density")
        if congestion_score > 50:
             factors.append("high_congestion")
        if is_weekend:
             factors.append("weekend")
        if precipitation > 0.2:
             factors.append("precipitation")

        return {
            "location": location, # Use passed location directly
            "prediction_time": prediction_time.isoformat(),
            "incident_likelihood": round(final_likelihood, 3),
            "confidence_score": 0.4, # Lower confidence for rule-based
            "contributing_factors": factors,
            "recommendations": self._generate_recommendations(
                final_likelihood, factors
            ),
        }

    def _get_contributing_factors(
        self, data_row: pd.Series, likelihood: float
    ) -> List[str]:
        """Identify contributing factors based on data and likelihood."""
        factors = []
        # Check if data_row is not empty before accessing elements
        if data_row.empty:
            return factors # Return empty list if no data

        if likelihood > 0.6:
            factors.append("high_predicted_likelihood") # Renamed for clarity
        # Use .get() with default values to handle missing columns gracefully
        if data_row.get("average_speed", 100.0) < 25: # Default to high speed if missing
            factors.append("low_speed")
        if data_row.get("vehicle_count", 0) > 70:
            factors.append("high_vehicle_count")
        if data_row.get("congestion_score", 0.0) > 60:
            factors.append("high_congestion_score")
        if data_row.get("is_weekend", 0): # Assuming 0 or 1
            factors.append("weekend")
        if data_row.get("weather_conditions_precipitation", 0.0) > 0.5:
            factors.append("precipitation")
        # Add other potential factors like time of day if not covered by peak_hour in rule-based
        # Example:
        # if 22 <= data_row.get("timestamp", datetime.min).hour or data_row.get("timestamp", datetime.min).hour < 5:
        #     factors.append("late_night/early_morning")

        return factors

    def _generate_recommendations(
        self, likelihood: float, factors: List[str]
    ) -> List[str]:
        """Generate recommendations based on likelihood and factors."""
        recommendations = []
        if likelihood > 0.7:
            recommendations.append("Consider deploying traffic management personnel.")
            recommendations.append("Activate dynamic routing suggestions for users.")
        if "peak_hour" in factors:
            recommendations.append("Suggest alternative routes to users.")
            recommendations.append("Optimize signal timing for peak traffic flow.")
        if "low_speed" in factors or "high_congestion_score" in factors:
            recommendations.append("Check for and clear any road obstructions.")
            recommendations.append("Adjust signal timing to improve flow.")
        if "high_vehicle_count" in factors:
            recommendations.append("Monitor for potential congestion buildup.")
            recommendations.append("Consider temporary lane adjustments if applicable.")
        if "precipitation" in factors:
            recommendations.append("Advise drivers to exercise caution due to weather.")
        # Add recommendations for other factors if needed
        if "late_night/early_morning" in factors:
             recommendations.append("Increased vigilance recommended due to low visibility/driver fatigue.")

        # Ensure recommendations are unique
        return list(set(recommendations))

    def train_model(self, filepath: str, epochs: int = 10, batch_size: int = 32):
        """Train the LSTM model with historical traffic data from a CSV file."""
        if tf is None:
            logger.error("TensorFlow is not available. Cannot train model.")
            return

        if self.model is None:
            logger.error("Model not initialized, cannot train.")
            # Initialize model if not already
            self._initialize_model()
            if self.model is None: # Check again if initialization failed
                 logger.error("Model initialization failed, cannot train.")
                 return

        try:
            logger.info(f"Loading training data from {filepath}")
            training_data = pd.read_csv(filepath)
            
            if training_data.empty:
                logger.error("Training data loaded is empty. Cannot train model.")
                return

            # Prepare features
            features = self.prepare_features(training_data.copy()) # Use .copy() to avoid SettingWithCopyWarning
            
            if features.empty or 'incident_occurred' not in training_data.columns:
                 logger.error("Prepared features or target 'incident_occurred' is missing. Cannot train.")
                 return

            target = training_data["incident_occurred"]

            # Scale features
            # Fit the scaler ONLY during training
            scaled_features = self.scaler.fit_transform(features)

            # Create sequences for LSTM: (samples, timesteps, features)
            X, y = self._create_sequences(scaled_features, target.values)
            
            if len(X) == 0:
                 logger.error("No sequences created from training data. Cannot train.")
                 return

            logger.info(f"Training model with {len(X)} sequences.")
            # Train the model
            history = self.model.fit(
                X,
                y,
                epochs=epochs,
                batch_size=batch_size,
                validation_split=0.2, # Use 20% of data for validation
                verbose=1,
            )
            logger.info("Model training completed successfully.")
            return history # Return training history

        except FileNotFoundError:
             logger.error(f"Training data file not found at {filepath}")
        except Exception as e:
            logger.error(f"Error during model training: {e}", exc_info=True)

    def _create_sequences(
        self, features: np.ndarray, target: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Create sequences for LSTM model"""
        X, y = [], []
        # Ensure there is enough data to create at least one sequence
        if len(features) < self.sequence_length:
            return np.array(X), np.array(y) # Return empty arrays

        for i in range(
            len(features) - self.sequence_length + 1
        ):
            # Each sequence includes 'sequence_length' timesteps
            X.append(features[i : i + self.sequence_length])
            # The target is the incident occurrence at the END of the sequence
            y.append(target[i + self.sequence_length - 1])
        return np.array(X), np.array(y)

    def save_model(self, path: str):
        """Save the trained model to a file"""
        if self.model:
            try:
                self.model.save(path)
                logger.info(f"Model saved to {path}")
            except Exception as e:
                logger.error(f"Error saving model to {path}: {e}")
        else:
            logger.warning("No model available to save.")

    def load_model(self, path: str):
        """Load a trained model from a file"""
        if tf is None:
            logger.error("TensorFlow is not available. Cannot load model.")
            return

        try:
            # Use tf.keras.models.load_model
            # If the scaler was saved separately, load it here as well.
            # Example: self.scaler = load_scaler(path + ".scaler")
            # Ensure the path exists before attempting to load.
            self.model = tf.keras.models.load_model(path)
            # Attempt to load the scaler as well if saved separately
            # This requires a separate saving/loading mechanism for the scaler
            # For simplicity here, we assume scaler will be fitted on dummy data if not loaded
            logger.info(f"Model loaded from {path}")
        except Exception as e:
            logger.error(f"Error loading model from {path}: {e}. Initializing a new model.", exc_info=True)
            self.model = None # Ensure model is None if loading fails
            # self._initialize_model() # Re-initialize a fresh model if loading fails (optional)
            # Decide whether to re-raise or continue with initialized model
            # For now, let's log and continue, allowing rule-based fallback


# Example usage block (only runs if the script is executed directly)
# Modified to use relative paths that should work from project root
if __name__ == "__main__":
    logger.setLevel(logging.INFO) # Ensure logger level is set for the predictor

    # Example usage:
    # Provide a minimal config with a potential model path
    config = {
        "traffic_predictor_model_path": "backend/models/traffic_predictor_model.h5",
         "logging": {"level": "INFO"} # Add logging config if needed
    }
    
    # Attempt to load existing model, if fails, a new one is initialized
    predictor = TrafficPredictor(config=config)

    # Train the model with dummy data if no model was loaded or if you want to retrain
    csv_filepath = "backend/data/traffic_data.csv" # Ensure this dummy data exists for the example
    # Add a check before training
    import os
    if os.path.exists(csv_filepath):
         logger.info(f"Training data found at {csv_filepath}. Starting training.")
         predictor.train_model(csv_filepath, epochs=50, batch_size=64)

         # Save the trained model
         model_save_path = "backend/models/traffic_predictor_model.h5"
         # Create the directory if it doesn't exist
         os.makedirs(os.path.dirname(model_save_path), exist_ok=True)
         predictor.save_model(model_save_path)
    else:
         logger.warning(f"Training data not found at {csv_filepath}. Skipping model training.")

    # Example prediction
    # Create some sample recent traffic data (list of dicts)
    sample_recent_data: List[Dict[str, Any]] = [
        {"timestamp": datetime.now(timezone.utc) - timedelta(minutes=i), 
         "latitude": 40.7128, "longitude": -74.0060, 
         "vehicle_count": 50 + i*5, "average_speed": 40 - i*2, 
         "congestion_score": 30 + i*3, "road_type": "major_artery",
         "is_weekend": datetime.now(timezone.utc).weekday() >= 5,
         "weather_conditions_temperature": 25.0, "weather_conditions_precipitation": 0.1,
         "truck_percentage": 5.0 + i,
         "incident_occurred": 0 # Assuming no incident in recent history
         } for i in range(predictor.sequence_length) # Create data for the sequence length
    ]
    # Reverse data to be in chronological order (oldest first)
    sample_recent_data.reverse()

    sample_location = {"latitude": 40.7128, "longitude": -74.0060}
    prediction_time = datetime.now(timezone.utc) + timedelta(minutes=15) # Predict 15 minutes into future

    logger.info(f"Attempting prediction for location {sample_location} at {prediction_time.isoformat()}")
    prediction_result = predictor.predict_incident_likelihood(sample_recent_data, sample_location, prediction_time)
    logger.info(f"Prediction Result: {prediction_result}")

