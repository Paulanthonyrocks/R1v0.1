import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler
import tensorflow as tf
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class TrafficPredictor:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.model = None
        self.scaler = StandardScaler()
        self.sequence_length = 10  # Number of time steps to use for prediction
        self._initialize_model()

    def _initialize_model(self):
        """Initialize the LSTM model for traffic prediction"""
        try:
            self.model = tf.keras.Sequential()
            self.model.add(tf.keras.layers.LSTM(64, return_sequences=True, input_shape=(self.sequence_length, 5)))
            self.model.add(tf.keras.layers.Dropout(0.2))
            self.model.add(tf.keras.layers.LSTM(32))
            self.model.add(tf.keras.layers.Dropout(0.2))
            self.model.add(tf.keras.layers.Dense(16, activation='relu'))
            self.model.add(tf.keras.layers.Dense(1, activation='sigmoid'))  # Predict incident likelihood (0-1)
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
                                  prediction_time: datetime) -> Dict[str, Any]:
        """Predict the likelihood of traffic incidents"""
        try:
            # For now, use a rule-based approach until we have enough data to train the ML model
            current_hour = prediction_time.hour
            
            # Base factors
            base_likelihood = 0.1
            
            # Time-based factors
            if 7 <= current_hour <= 9:  # Morning rush
                base_likelihood += 0.3
            elif 16 <= current_hour <= 18:  # Evening rush
                base_likelihood += 0.3
            
            # Recent traffic patterns
            if recent_traffic_data:
                recent_speeds = [d.get('average_speed', 0) for d in recent_traffic_data[-5:]]
                recent_counts = [d.get('vehicle_count', 0) for d in recent_traffic_data[-5:]]
                
                avg_speed = np.mean(recent_speeds) if recent_speeds else 0
                avg_count = np.mean(recent_counts) if recent_counts else 0
                
                # Adjust likelihood based on current conditions
                if avg_speed < 20:  # Slow traffic
                    base_likelihood += 0.2
                if avg_count > 50:  # High vehicle density
                    base_likelihood += 0.2
            
            # Weather factor (placeholder for future weather API integration)
            weather_factor = 0.0
            
            final_likelihood = min(0.95, base_likelihood + weather_factor)
            
            factors = []
            if 7 <= current_hour <= 9 or 16 <= current_hour <= 18:
                factors.append("peak_hour")
            if recent_traffic_data:
                if avg_speed < 20:
                    factors.append("slow_traffic")
                if avg_count > 50:
                    factors.append("high_density")
            
            return {
                "location": location,
                "prediction_time": prediction_time.isoformat(),
                "incident_likelihood": round(final_likelihood, 3),
                "confidence_score": 0.7,  # Can be adjusted based on data quality
                "contributing_factors": factors,
                "recommendations": self._generate_recommendations(final_likelihood, factors)
            }
            
        except Exception as e:
            logger.error(f"Error predicting incident likelihood: {e}")
            return {
                "incident_likelihood": 0.0,
                "error": str(e)
            }

    def _generate_recommendations(self, likelihood: float, factors: List[str]) -> List[str]:
        """Generate recommendations based on prediction factors"""
        recommendations = []
        
        if likelihood > 0.7:
            recommendations.append("Consider deploying traffic management personnel")
            recommendations.append("Activate dynamic routing suggestions for users")
            
        if "peak_hour" in factors:
            recommendations.append("Suggest alternative routes to users")
            recommendations.append("Optimize signal timing for peak traffic flow")
            
        if "slow_traffic" in factors:
            recommendations.append("Check for and clear any road obstructions")
            recommendations.append("Adjust signal timing to improve flow")
            
        if "high_density" in factors:
            recommendations.append("Monitor for potential congestion buildup")
            recommendations.append("Consider temporary lane adjustments if applicable")
            
        return recommendations
