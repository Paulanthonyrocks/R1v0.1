import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
import tensorflow as tf
from typing import List, Dict, Any, Optional
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class TrafficAnomalyDetector:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.sequence_length = config.get("anomaly_detection", {}).get("sequence_length", 12) # e.g., last 12 intervals (1 hour if 5min intervals)
        self.threshold = config.get("anomaly_detection", {}).get("threshold", 0.5)
        self.scaler = StandardScaler()
        self.model: Optional[tf.keras.Model] = None
        
        model_path = config.get("anomaly_detection", {}).get("model_path")
        if model_path:
            self.load_model(model_path)

    def _initialize_model(self, feature_count: int):
        """Initialize an LSTM Autoencoder for anomaly detection."""
        try:
            self.model = tf.keras.Sequential([
                # Encoder
                tf.keras.layers.LSTM(32, activation='relu', input_shape=(self.sequence_length, feature_count), return_sequences=True),
                tf.keras.layers.LSTM(16, activation='relu', return_sequences=False),
                tf.keras.layers.RepeatVector(self.sequence_length),
                # Decoder
                tf.keras.layers.LSTM(16, activation='relu', return_sequences=True),
                tf.keras.layers.LSTM(32, activation='relu', return_sequences=True),
                tf.keras.layers.TimeDistributed(tf.keras.layers.Dense(feature_count))
            ])
            self.model.compile(optimizer='adam', loss='mae')
            logger.info("Anomaly detection Autoencoder initialized.")
        except Exception as e:
            logger.error(f"Error initializing anomaly detection model: {e}")

    def prepare_features(self, data: pd.DataFrame) -> np.ndarray:
        """Extract and scale features for the autoencoder."""
        # Focus on core flow metrics
        features = ["vehicle_count", "average_speed", "congestion_score"]
        for f in features:
            if f not in data.columns:
                data[f] = 0.0
        
        return data[features].values

    def detect_anomaly(self, recent_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Detects anomalies in the recent traffic data.
        Returns a dict with 'is_anomaly', 'score', and 'reason'.
        """
        if not recent_data or len(recent_data) < 5:
            return {"is_anomaly": False, "score": 0, "reason": "Insufficient data"}

        df = pd.DataFrame(recent_data)
        features = self.prepare_features(df)
        
        # Fallback to statistical detection if no ML model
        if self.model is None:
            return self._statistical_detection(df)

        try:
            # Scale and reshape
            scaled = self.scaler.transform(features)
            
            # Padding if needed
            if len(scaled) < self.sequence_length:
                padding = np.zeros((self.sequence_length - len(scaled), scaled.shape[1]))
                scaled = np.vstack((padding, scaled))
            
            sequence = scaled[-self.sequence_length:].reshape(1, self.sequence_length, -1)
            
            # Predict (Reconstruct)
            reconstruction = self.model.predict(sequence)
            
            # Calculate MAE as anomaly score
            loss = np.mean(np.abs(reconstruction - sequence))
            
            is_anomaly = loss > self.threshold
            
            return {
                "is_anomaly": bool(is_anomaly),
                "score": float(loss),
                "method": "ml_autoencoder",
                "reason": "Unusual pattern detected by Autoencoder" if is_anomaly else "Normal"
            }
        except Exception as e:
            logger.error(f"ML Anomaly detection failed: {e}")
            return self._statistical_detection(df)

    def _statistical_detection(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        Heuristic/Statistical anomaly detection using Z-score logic.
        Useful when the ML model is not yet trained.
        """
        if len(df) < 5:
            return {"is_anomaly": False, "score": 0, "reason": "Insufficient data"}

        # Check for sudden drops in speed vs recent history
        speeds = df["average_speed"].values
        current_speed = speeds[-1]
        hist_avg_speed = np.mean(speeds[:-1])
        hist_std_speed = np.std(speeds[:-1]) + 0.1
        
        z_score = abs(current_speed - hist_avg_speed) / hist_std_speed
        
        # Check for sudden vehicle count spikes
        counts = df["vehicle_count"].values
        current_count = counts[-1]
        hist_avg_count = np.mean(counts[:-1])
        hist_std_count = np.std(counts[:-1]) + 0.1
        count_z_score = (current_count - hist_avg_count) / hist_std_count

        is_anomaly = z_score > 3.0 or count_z_score > 4.0
        
        reason = []
        if z_score > 3.0: reason.append("Sudden speed deviation")
        if count_z_score > 4.0: reason.append("Sudden vehicle spike")

        return {
            "is_anomaly": bool(is_anomaly),
            "score": float(max(z_score, count_z_score)),
            "method": "statistical_zscore",
            "reason": ", ".join(reason) if is_anomaly else "Normal"
        }

    def load_model(self, path: str):
        try:
            self.model = tf.keras.models.load_model(path)
            logger.info(f"Anomaly detector model loaded from {path}")
        except Exception as e:
            logger.error(f"Failed to load anomaly detector: {e}")

    def save_model(self, path: str):
        if self.model:
            self.model.save(path)
