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
        self.monitored_locations: List[LocationModel] = [] # Type hint
        self.task = None
        self._priority_locations: List[LocationModel] = []
        self._priority_lock = asyncio.Lock()
        self.logger = logger # Assign logger to instance for use in methods
        # self._load_monitored_locations() # Remove sync call from __init__

    async def set_priority_locations(self, locations: List[LocationModel]):
        """
        Allows AgentCore to set a list of priority locations for the next prediction cycle.
        """
        async with self._priority_lock:
            self._priority_locations = locations.copy()
            priority_info = [f"{loc.name} ({loc.latitude},{loc.longitude})" if loc.name else f"({loc.latitude},{loc.longitude})"
                             for loc in self._priority_locations]
            self.logger.info(f"PredictionScheduler received priority locations: {priority_info}")

    async def _load_monitored_locations(self):
        """
        Load monitored locations. Prioritizes locations set by AgentCore,
        otherwise falls back to default/random selection.
        This method is now async.
        """
        current_monitored_locations: List[LocationModel] = []
        async with self._priority_lock:
            if self._priority_locations:
                current_monitored_locations = self._priority_locations.copy()
                self._priority_locations = []  # Clear after use for this cycle
                priority_names = [loc.name for loc in current_monitored_locations if loc.name]
                self.logger.info(f"Using {len(current_monitored_locations)} priority locations for prediction: {priority_names if priority_names else [f'({loc.latitude},{loc.longitude})' for loc in current_monitored_locations]}")
            else:
                self.logger.info("No priority locations set, using default/random locations for prediction.")
                # Existing logic for default/random selection
                all_locations = [
                    LocationModel(latitude=34.0522, longitude=-118.2437, name="Los Angeles Downtown"),
                    LocationModel(latitude=40.7128, longitude=-74.0060, name="New York Times Square"),
                    LocationModel(latitude=41.8781, longitude=-87.6298, name="Chicago The Loop"),
                    LocationModel(latitude=37.7749, longitude=-122.4194, name="San Francisco Embarcadero"),
                    LocationModel(latitude=33.7490, longitude=-84.3880, name="Atlanta Centennial Park")
                ]
                if not all_locations:
                    self.monitored_locations = [] # Should be current_monitored_locations
                    return # Exiting early if no locations defined

                # Select a random number of locations to monitor, at least 1
                num_to_select = random.randint(1, len(all_locations))
                current_monitored_locations = random.sample(all_locations, num_to_select)
                default_loc_info = [f"{loc.name if loc.name else ''} ({loc.latitude},{loc.longitude})" for loc in current_monitored_locations]
                self.logger.info(f"Dynamically selected {len(current_monitored_locations)} default locations: {default_loc_info}")

        self.monitored_locations = current_monitored_locations


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
                await self._load_monitored_locations() # Load locations at the start of each cycle

                if not self.monitored_locations:
                    self.logger.warning("No locations loaded for prediction cycle. Sleeping before retry.")
                else:
                    # Make predictions for all monitored locations
                    prediction_tasks = []
                    for location in self.monitored_locations:
                        prediction_tasks.append(self._predict_and_notify(location))
                    await asyncio.gather(*prediction_tasks) # Run predictions concurrently
                    self.logger.info(f"Completed prediction cycle for {len(self.monitored_locations)} locations.")
                
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
