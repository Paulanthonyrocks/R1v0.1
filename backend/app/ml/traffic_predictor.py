import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
import tensorflow as tf
from datetime import datetime
from typing import List, Dict, Any
import logging

logger = logging.getLogger(__name__)


class TrafficPredictor:
    def __init__(self, config: Dict[str, Any], model_object=None):
        self.config = config
        self.model = model_object
        self.scaler = StandardScaler()
        self.sequence_length = 10  # Number of time steps to use for prediction

        if self.model is None:
            model_path = self.config.get("model_path")
            if model_path:
                self.load_model(model_path)
            else:
                logger.warning(
                    "No model_path or model_object provided in config for TrafficPredictor. Model will not be loaded."
                )

    def _initialize_model(self):
        """Initialize the LSTM model for traffic prediction"""
        try:
            self.model = tf.keras.Sequential()
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
        # Ensure timestamp is datetime
        traffic_data["timestamp"] = pd.to_datetime(traffic_data["timestamp"])

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
        traffic_data["is_weekend"] = traffic_data["is_weekend"].astype(int)

        # One-hot encode road_type
        traffic_data = pd.get_dummies(
            traffic_data, columns=["road_type"], prefix="road_type"
        )

        # Select features for the model
        # Ensure all expected road_type columns are present, fill with 0 if not
        expected_road_types = ["major_artery", "highway", "local_road"]
        for rt in expected_road_types:
            col_name = f"road_type_{rt}"
            if col_name not in traffic_data.columns:
                traffic_data[col_name] = 0

        features = traffic_data[
            [
                "hour_sin",
                "hour_cos",
                "day_of_week_sin",
                "day_of_week_cos",
                "is_weekend",
                "vehicle_count",
                "average_speed",
                "congestion_score",
                "weather_conditions_temperature",
                "weather_conditions_precipitation",
                "truck_percentage",
                "road_type_major_artery",
                "road_type_highway",
                "road_type_local_road",
            ]
        ]
        return features

    def predict_incident_likelihood(
        self,
        recent_traffic_data: List[Dict[str, Any]],
        location: Dict[str, Any],
        prediction_time: datetime,
    ) -> Dict[str, Any]:
        """Predict the likelihood of traffic incidents using the trained model."""
        # Construct input_features DataFrame from the provided data
        # This is a simplified approach; in a real system, you'd align columns carefully
        # and handle missing data more robustly.

        # If recent_traffic_data is empty, create a dummy row based on location and prediction_time
        if not recent_traffic_data:
            input_data = {
                "timestamp": prediction_time,
                "latitude": location.get("latitude", 0.0),
                "longitude": location.get("longitude", 0.0),
                "vehicle_count": 0,  # Default or average values
                "average_speed": 0.0,
                "congestion_level": 0.0,
                "congestion_score": 0.0,
                "processing_timestamp": datetime.now(),
                "status": "simulated",
                "hour_of_day": prediction_time.hour,
                "day_of_week": prediction_time.weekday(),
                "is_weekend": prediction_time.weekday() >= 5,
                "road_type": "major_artery",  # Default
                "weather_conditions_temperature": 20.0,  # Default
                "weather_conditions_precipitation": 0.0,  # Default
                "truck_percentage": 0.0,  # Default
                "is_outlier": False,
                "incident_occurred": 0,  # Default
            }
            input_features = pd.DataFrame([input_data])
        else:
            # Convert list of dicts to DataFrame
            input_features = pd.DataFrame(recent_traffic_data)
            # Ensure timestamp is datetime
            input_features["timestamp"] = pd.to_datetime(input_features["timestamp"])
            # Add missing columns with default values if they are not present in recent_traffic_data
            # This is crucial for prepare_features to work correctly
            for col in [
                "vehicle_count",
                "average_speed",
                "congestion_level",
                "congestion_score",
                "processing_timestamp",
                "status",
                "hour_of_day",
                "day_of_week",
                "is_weekend",
                "road_type",
                "weather_conditions_temperature",
                "weather_conditions_precipitation",
                "truck_percentage",
                "is_outlier",
                "incident_occurred",
            ]:
                if col not in input_features.columns:
                    if col in ["vehicle_count", "incident_occurred"]:
                        input_features[col] = 0
                    elif col in [
                        "average_speed",
                        "congestion_level",
                        "congestion_score",
                        "truck_percentage",
                        "weather_conditions_temperature",
                        "weather_conditions_precipitation",
                    ]:
                        input_features[col] = 0.0
                    elif col == "processing_timestamp":
                        input_features[col] = datetime.now()
                    elif col == "status":
                        input_features[col] = "unknown"
                    elif col == "hour_of_day":
                        input_features[col] = input_features["timestamp"].dt.hour
                    elif col == "day_of_week":
                        input_features[col] = input_features["timestamp"].dt.dayofweek
                    elif col == "is_weekend":
                        input_features[col] = (
                            input_features["timestamp"].dt.dayofweek >= 5
                        ).astype(int)
                    elif col == "road_type":
                        input_features[col] = "major_artery"  # Default road type

        if self.model is None:
            logger.warning(
                "Model not loaded or trained. Returning rule-based prediction."
            )
            # Fallback to rule-based if model is not available
            return self._rule_based_prediction(
                location, prediction_time, input_features
            )

        try:
            processed_features = self.prepare_features(input_features.copy())

            # Scale features
            scaled_features = self.scaler.transform(processed_features)

            # Ensure we have enough data points for the sequence length
            if len(scaled_features) < self.sequence_length:
                logger.warning(f"Not enough recent traffic data ({len(scaled_features)}) for sequence length ({self.sequence_length}). Padding with zeros.")
                # Pad with zeros or a more sophisticated padding strategy
                padding_needed = self.sequence_length - len(scaled_features)
                padding_array = np.zeros((padding_needed, scaled_features.shape[1]))
                scaled_features = np.vstack((padding_array, scaled_features))
            
            # Take the last 'sequence_length' data points
            scaled_features_sequence = scaled_features[-self.sequence_length:]
            
            # Reshape for LSTM: (samples, timesteps, features)
            scaled_features_reshaped = np.expand_dims(scaled_features_sequence, axis=0)

            prediction = self.model.predict(scaled_features_reshaped)[0][
                0
            ]  # Get scalar prediction

            incident_likelihood = float(prediction)
            confidence_score = (
                0.8  # Placeholder, could be derived from model output or ensemble
            )

            return {
                "location": location,
                "prediction_time": prediction_time.isoformat(),
                "incident_likelihood": round(incident_likelihood, 3),
                "confidence_score": confidence_score,
                "contributing_factors": self._get_contributing_factors(
                    input_features.iloc[0], incident_likelihood
                ),
                "recommendations": self._generate_recommendations(
                    incident_likelihood,
                    self._get_contributing_factors(
                        input_features.iloc[0], incident_likelihood
                    ),
                ),
            }

        except Exception as e:
            logger.error(f"Error predicting incident likelihood: {e}", exc_info=True)
            return {"incident_likelihood": 0.0, "error": str(e)}

    def _rule_based_prediction(
        self,
        location: Dict[str, Any],
        prediction_time: datetime,
        input_features: pd.DataFrame,
    ) -> Dict[str, Any]:
        """Fallback rule-based prediction if ML model is not available."""
        current_hour = prediction_time.hour
        # Safely get values, defaulting to 0.0 if input_features is empty or column is missing
        avg_speed = (
            input_features["average_speed"].iloc[0]
            if not input_features.empty and "average_speed" in input_features.columns
            else 0.0
        )
        vehicle_count = (
            input_features["vehicle_count"].iloc[0]
            if not input_features.empty and "vehicle_count" in input_features.columns
            else 0
        )

        base_likelihood = 0.1
        if 7 <= current_hour <= 9 or 16 <= current_hour <= 18:
            base_likelihood += 0.3
        if avg_speed < 20:
            base_likelihood += 0.2
        if vehicle_count > 50:
            base_likelihood += 0.2

        final_likelihood = min(0.95, base_likelihood)

        factors = []
        if 7 <= current_hour <= 9 or 16 <= current_hour <= 18:
            factors.append("peak_hour")
        if avg_speed < 20:
            factors.append("slow_traffic")
        if vehicle_count > 50:
            factors.append("high_density")

        return {
            "location": {
                "latitude": input_features["latitude"].iloc[0],
                "longitude": input_features["longitude"].iloc[0],
            },
            "prediction_time": input_features["timestamp"].iloc[0].isoformat(),
            "incident_likelihood": round(final_likelihood, 3),
            "confidence_score": 0.5,
            "contributing_factors": factors,
            "recommendations": self._generate_recommendations(
                final_likelihood, factors
            ),
        }

    def _get_contributing_factors(
        self, data_row: pd.Series, likelihood: float
    ) -> List[str]:
        factors = []
        if likelihood > 0.6:
            factors.append("high_likelihood")
        if data_row["average_speed"] < 25:
            factors.append("low_speed")
        if data_row["vehicle_count"] > 70:
            factors.append("high_vehicle_count")
        if data_row["congestion_score"] > 60:
            factors.append("high_congestion_score")
        if data_row["is_weekend"]:
            factors.append("weekend")
        if data_row["weather_conditions_precipitation"] > 0.5:
            factors.append("precipitation")
        return factors

    def _generate_recommendations(
        self, likelihood: float, factors: List[str]
    ) -> List[str]:
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
        return recommendations

    def train_model(self, filepath: str, epochs: int = 10, batch_size: int = 32):
        """Train the LSTM model with historical traffic data from a CSV file."""
        if self.model is None:
            logger.error("Model not initialized, cannot train.")
            return

        try:
            training_data = pd.read_csv(filepath)

            # Prepare features
            features = self.prepare_features(
                training_data.copy()
            )  # Use .copy() to avoid SettingWithCopyWarning
            target = training_data["incident_occurred"]

            # Scale features
            scaled_features = self.scaler.fit_transform(features)

            # Reshape for LSTM: (samples, timesteps, features)
            # Assuming each row is a single timestep for now.
            # If sequence_length > 1, this needs to be adjusted to create sequences.
            X, y = self._create_sequences(scaled_features, target.values)

            # Train the model
            self.model.fit(
                X,
                y,
                epochs=epochs,
                batch_size=batch_size,
                validation_split=0.2,
                verbose=1,
            )
            logger.info("Model training completed successfully.")

        except Exception as e:
            logger.error(f"Error during model training: {e}", exc_info=True)

    def _create_sequences(
        self, features: np.ndarray, target: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Create sequences for LSTM model"""
        X, y = [], []
        for i in range(
            len(features) - self.sequence_length + 1
        ):  # Adjusted loop for sequence creation
            X.append(features[i : i + self.sequence_length])
            y.append(
                target[i + self.sequence_length - 1]
            )  # Target corresponds to the last element in the sequence
        return np.array(X), np.array(y)

    def save_model(self, path: str):
        """Save the trained model to a file"""
        if self.model:
            self.model.save(path)
            logger.info(f"Model saved to {path}")

    def load_model(self, path: str):
        """Load a trained model from a file"""
        try:
            self.model = tf.keras.models.load_model(path)
            logger.info(f"Model loaded from {path}")
        except Exception as e:
            logger.error(f"Error loading model from {path}: {e}")
            self._initialize_model()  # Re-initialize a fresh model if loading fails


if __name__ == "__main__":
    # Example usage:
    predictor = TrafficPredictor(config={})
    # Train the model with dummy data
    csv_filepath = "backend/data/traffic_data.csv"
    predictor.train_model(csv_filepath, epochs=50, batch_size=64)

    # Save the trained model
    model_path = "backend/models/traffic_predictor_model.h5"
    predictor.save_model(model_path)

    # Example prediction (using a sample from the training data)
    # In a real scenario, you would get new data for prediction
    sample_data = pd.read_csv(csv_filepath).sample(1)
    prediction_result = predictor.predict_incident_likelihood(sample_data)
    logger.info(f"Prediction for sample data: {prediction_result}")
