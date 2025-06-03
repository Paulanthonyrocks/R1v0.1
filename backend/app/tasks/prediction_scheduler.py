import asyncio
import logging
import random # Added random for dynamic location selection
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import json

from app.services.analytics_service import AnalyticsService
from app.models.traffic import LocationModel
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, GeneralNotification

logger = logging.getLogger(__name__)

class PredictionScheduler:
    def __init__(self, analytics_service: AnalyticsService, prediction_interval_minutes: int = 15):
        self.analytics_service = analytics_service
        self.prediction_interval = timedelta(minutes=prediction_interval_minutes)
        self.is_running = False
        self.monitored_locations = []
        self.task = None
        self._load_monitored_locations()

    def _load_monitored_locations(self):
        """Load monitored locations from configuration"""
        # TODO: Load from database or config
        # For now, using sample locations and selecting a random subset
        all_locations = [
            LocationModel(latitude=34.0522, longitude=-118.2437),  # Los Angeles
            LocationModel(latitude=40.7128, longitude=-74.0060),   # New York
            LocationModel(latitude=41.8781, longitude=-87.6298),   # Chicago
            LocationModel(latitude=37.7749, longitude=-122.4194), # San Francisco
            LocationModel(latitude=33.7490, longitude=-84.3880)   # Atlanta
        ]
        if not all_locations: # Should not happen with hardcoded list but good practice
            self.monitored_locations = []
            return

        # Select a random number of locations to monitor, at least 1
        num_to_select = random.randint(1, len(all_locations))
        self.monitored_locations = random.sample(all_locations, num_to_select)
        logger.info(f"Dynamically selected {len(self.monitored_locations)} locations to monitor: {[f'({loc.latitude},{loc.longitude})' for loc in self.monitored_locations]}")


    def determine_autonomous_actions(self, prediction: Dict[str, Any], location: LocationModel) -> str:
        """
        Determines autonomous actions based on the prediction.
        For now, this method logs the prediction and returns a placeholder action.
        """
        action_details = (
            f"Log: High incident likelihood of {prediction['likelihood_score_percent']}% "
            f"at location ({location.latitude}, {location.longitude}). "
            "Placeholder action: Consider adjusting traffic signals and dispatching resources."
        )
        logger.info(f"Determined autonomous action: {action_details}")
        return action_details


    async def _predict_and_notify(self, location: LocationModel):
        """Make prediction for a location and notify if high likelihood of incidents"""
        try:
            # Get prediction for next hour
            prediction_time = datetime.now() + timedelta(hours=1)
            prediction = await self.analytics_service.predict_incident_likelihood(
                location=location,
                prediction_time=prediction_time
            )

            # If high likelihood, determine actions and notify
            if prediction["likelihood_score_percent"] > 70: # Threshold for high likelihood
                action_taken = self.determine_autonomous_actions(prediction, location)

                notification_details = {
                    "location": location.model_dump(),
                    "prediction_time": prediction_time.isoformat(),
                    "likelihood_percent": prediction["likelihood_score_percent"],
                    "recommendations": prediction.get("recommendations", []),
                    "autonomous_action": action_taken
                }

                notification = GeneralNotification(
                    message=f"High likelihood of traffic incident predicted. Action initiated.",
                    details=notification_details
                )
                
                message = WebSocketMessage(
                    event_type=WebSocketMessageTypeEnum.PREDICTION_ALERT,
                    payload=notification
                )

                # Use analytics service's connection manager to broadcast
                if self.analytics_service._connection_manager:
                    # Creating a unique topic per location for targeted messages
                    location_hash = abs(hash((location.latitude, location.longitude)))
                    await self.analytics_service._connection_manager.broadcast_message_model(
                        message,
                        specific_topic=f"predictions:{location_hash}"
                    )
                    logger.info(f"Sent high likelihood notification for location {location.latitude},{location.longitude} with action: {action_taken}")

        except Exception as e:
            logger.error(f"Error making prediction for location {location}: {e}")

    async def run(self):
        """Run the prediction scheduler"""
        self.is_running = True
        while self.is_running:
            try:
                # Make predictions for all monitored locations
                for location in self.monitored_locations:
                    await self._predict_and_notify(location)
                
                # Wait for next interval
                await asyncio.sleep(self.prediction_interval.total_seconds())
            except Exception as e:
                logger.error(f"Error in prediction scheduler: {e}")
                await asyncio.sleep(60)  # Wait a minute before retrying

    async def start(self):
        """Start the prediction scheduler"""
        if self.task is None:
            self.task = asyncio.create_task(self.run())
            logger.info("Prediction scheduler started")
        else:
            logger.warning("Prediction scheduler already running")

    async def stop(self):
        """Stop the prediction scheduler"""
        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
            logger.info("Prediction scheduler stopped")
