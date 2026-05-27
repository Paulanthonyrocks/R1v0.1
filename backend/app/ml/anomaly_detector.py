import numpy as np
import pandas as pd
import joblib
from sklearn.preprocessing import StandardScaler
from typing import List, Dict, Any, Optional
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class TrafficAnomalyDetector:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        anomaly_cfg = config.get("anomaly_detection", {})
        self.sequence_length = anomaly_cfg.get("sequence_length", 12) # e.g., last 12 intervals (1 hour if 5min intervals)
        self.threshold = anomaly_cfg.get("threshold", 0.5)
        
        self.scaler: Optional[StandardScaler] = None
        self.model: Any = None
        
        model_path = anomaly_cfg.get("model_path")
        scaler_path = anomaly_cfg.get("scaler_path")
        
        if model_path:
            self.load_model(model_path)
        
        if scaler_path:
            try:
                self.scaler = joblib.load(scaler_path)
                logger.info(f"Anomaly detector scaler loaded from {scaler_path}")
            except Exception as e:
                logger.error(f"Failed to load anomaly detector scaler from {scaler_path}: {e}")
        
        if self.model and not self.scaler:
            logger.warning("Anomaly detection model loaded but no scaler found. ML path will fallback to statistical detection.")

    def _initialize_model(self, feature_count: int):
        """Initialize an LSTM Autoencoder for anomaly detection."""
        try:
            import tensorflow as tf
        except Exception as e:
            logger.error(f"Failed to import tensorflow in anomaly_detector: {e}")
            return

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
        
        # Fallback to statistical detection if no ML model or scaler
        if self.model is None or self.scaler is None:
            return self._statistical_detection(df)

        try:
            # Scale and reshape
            scaled = self.scaler.transform(features)
            
            # Padding if needed - pad with the first valid value to avoid zero-distortion
            if len(scaled) < self.sequence_length:
                pad_val = scaled[0] if len(scaled) > 0 else np.zeros(scaled.shape[1])
                padding = np.tile(pad_val, (self.sequence_length - len(scaled), 1))
                scaled = np.vstack((padding, scaled))
            
            sequence = scaled[-self.sequence_length:].reshape(1, self.sequence_length, -1)
            
            # Predict (Reconstruct)
            reconstruction = self.model.predict(sequence, verbose=0)
            
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
        
        # Check for sudden vehicle count changes (spikes or drops)
        counts = df["vehicle_count"].values
        current_count = counts[-1]
        hist_avg_count = np.mean(counts[:-1])
        hist_std_count = np.std(counts[:-1]) + 0.1
        count_z_score = abs(current_count - hist_avg_count) / hist_std_count

        is_anomaly = z_score > 3.0 or count_z_score > 4.0
        
        reason = []
        if z_score > 3.0: reason.append("Sudden speed deviation")
        if count_z_score > 4.0: reason.append("Sudden vehicle count deviation")

        return {
            "is_anomaly": bool(is_anomaly),
            "score": float(max(z_score, count_z_score)),
            "method": "statistical_zscore",
            "reason": ", ".join(reason) if is_anomaly else "Normal"
        }

    def load_model(self, path: str):
        try:
            import tensorflow as tf
        except Exception as e:
            logger.error(f"Failed to import tensorflow in anomaly_detector: {e}")
            return

        try:
            self.model = tf.keras.models.load_model(path)
            logger.info(f"Anomaly detector model loaded from {path}")
        except Exception as e:
            logger.error(f"Failed to load anomaly detector: {e}")

    def save_model(self, path: str):
        if self.model:
            self.model.save(path)
