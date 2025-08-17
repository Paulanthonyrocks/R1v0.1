import asyncio
import logging
import random  # Added random for dynamic location selection
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

from app.models.traffic import LocationModel
from app.services.analytics_service import AnalyticsService
from app.models.websocket import (
    WebSocketMessage,
    WebSocketMessageTypeEnum,
    GeneralNotification,
)


logger = logging.getLogger("app.tasks.prediction_scheduler")


class PredictionScheduler:
    def __init__(self, analytics_service: AnalyticsService, config: Dict[str, Any]):
        self.config = config.get(
            "prediction_scheduler", {}
        )  # Get prediction_scheduler specific config
        self.analytics_service = analytics_service
        self.prediction_interval = timedelta(
            minutes=self.config.get("prediction_interval_minutes", 15)
        )
        logger.info(f"PredictionScheduler initialized. Enabled status from config: {self.config.get('enabled', True)}") # Added log

        self.is_running = False
        self.monitored_locations: List[LocationModel] = []  # Type hint
        self.task = None
        self._priority_locations: List[LocationModel] = []
        self._priority_lock = asyncio.Lock()
        self.logger = logger  # Assign logger to instance for use in methods

        # For accuracy-based location selection
        self._location_accuracy_cache: Dict[
            str, Dict[str, Any]
        ] = {}  # Stores summary from AnalyticsService
        self._accuracy_cache_ttl = timedelta(
            minutes=self.config.get("accuracy_cache_ttl_minutes", 60)
        )  # How long to trust cached accuracy data
        self._last_accuracy_cache_refresh: Optional[datetime] = None

    async def set_priority_locations(self, locations: List[LocationModel]):
        """Sets a list of priority locations for the next prediction cycle."""
        async with self._priority_lock:
            self._priority_locations = locations
            self.logger.info(f"Set {len(locations)} priority locations for next cycle.")

    def _get_location_key(self, location: LocationModel) -> str:
        """Helper to create a consistent dictionary key for a location."""
        return f"{location.latitude:.4f}_{location.longitude:.4f}"

    def _get_all_configured_locations(self) -> List[LocationModel]:
        """Loads all predefined monitored locations from config.yaml."""
        locations_data = self.config.get("monitored_locations", [])
        return [LocationModel(**loc) for loc in locations_data]

    async def _refresh_accuracy_cache_if_stale(
        self, all_locations: List[LocationModel]
    ):
        """Refreshes the prediction accuracy cache if it's stale."""
        now = datetime.now()
        if self._last_accuracy_cache_refresh is None or (
            now - self._last_accuracy_cache_refresh > self._accuracy_cache_ttl
        ):
            self.logger.info(
                "Prediction accuracy cache is stale or empty. Refreshing..."
            )
            temp_cache = {}
            for loc in all_locations:
                try:
                    summary = (
                        await self.analytics_service.get_prediction_outcome_summary(
                            location_latitude=loc.latitude,
                            location_longitude=loc.longitude,
                            time_since=now - timedelta(days=7),
                        )
                    )
                    if summary and not summary.get("error"):
                        temp_cache[self._get_location_key(loc)] = summary
                        self.logger.debug(
                            f"Fetched accuracy for {loc.name or self._get_location_key(loc)}: {summary.get('accuracy_metrics', {}).get('incident_hit_rate', 'N/A')}"
                        )
                    else:
                        self.logger.warning(
                            f"Could not fetch valid accuracy summary for {loc.name or self._get_location_key(loc)}. Error: {summary.get('error')}"
                        )
                except Exception as e_fetch:
                    self.logger.error(
                        f"Exception fetching accuracy for {loc.name or self._get_location_key(loc)}: {e_fetch}",
                        exc_info=True,
                    )

            # Validate data before caching
            validated_cache = {}
            for key, summary_data in temp_cache.items():
                if (
                    isinstance(summary_data, dict)
                    and isinstance(summary_data.get("accuracy_metrics"), dict)
                    and isinstance(summary_data["accuracy_metrics"].get("incident_hit_rate"), (int, float))
                    and isinstance(summary_data.get("total_verified_predictions"), int)
                ):
                    validated_cache[key] = summary_data
            self._location_accuracy_cache = validated_cache
            self._last_accuracy_cache_refresh = now
            self.logger.info(
                f"Prediction accuracy cache refreshed. Total entries: {len(self._location_accuracy_cache)}"
            )

    def _select_locations_by_accuracy_weight(
        self, all_locations: List[LocationModel]
    ) -> List[LocationModel]:
        """Selects locations based on prediction accuracy weights."""
        weights = []
        min_verified = self.config.get("min_verified_predictions_for_accuracy", 5)
        default_low_data_weight = self.config.get("default_low_data_weight", 0.75)
        default_no_accuracy_weight = self.config.get("default_no_accuracy_weight", 0.8)

        for loc in all_locations:
            loc_key = self._get_location_key(loc)
            accuracy_data = self._location_accuracy_cache.get(loc_key)
            weight = 0.5  # Default neutral weight

            if accuracy_data:
                hit_rate = accuracy_data.get("accuracy_metrics", {}).get(
                    "incident_hit_rate", 0.5
                )
                total_verified = accuracy_data.get("total_verified_predictions", 0)

                if total_verified < min_verified:
                    weight = default_low_data_weight
                else:
                    weight = (hit_rate * hit_rate) if hit_rate > 0.05 else 0.1
            else:
                weight = default_no_accuracy_weight

            weights.append(weight)
            self.logger.debug(
                f"Location: {loc.name or loc_key}, HitRate: {accuracy_data.get('accuracy_metrics', {}).get('incident_hit_rate', 'N/A') if accuracy_data else 'N/A'}, Weight: {weight:.2f}"
            )

        if not any(w > 0 for w in weights):
            self.logger.warning(
                "All location weights are zero. Falling back to uniform random sampling."
            )
            weights = [1.0] * len(all_locations)

        max_locations_factor = self.config.get("max_locations_to_select_factor", 0.5)
        num_to_select = random.randint(
            1, max(1, int(len(all_locations) * max_locations_factor))
        )

        current_monitored_locations = random.choices(
            all_locations, weights=weights, k=num_to_select
        )

        selected_loc_info = [
            f"{loc.name if loc.name else self._get_location_key(loc)} (W:{weights[all_locations.index(loc)]:.2f})"
            for loc in current_monitored_locations
        ]
        self.logger.info(
            f"Dynamically selected {len(current_monitored_locations)} locations based on accuracy weights: {selected_loc_info}"
        )
        return current_monitored_locations

    async def _load_monitored_locations(self):
        """
        Load monitored locations. Prioritizes locations set by AgentCore,
        otherwise falls back to dynamic selection based on prediction accuracy.
        """
        current_monitored_locations: List[LocationModel] = []
        async with self._priority_lock:
            if self._priority_locations:
                current_monitored_locations = self._priority_locations.copy()
                self._priority_locations = []  # Clear after use for this cycle
                priority_names = [
                    loc.name for loc in current_monitored_locations if loc.name
                ]
                self.logger.info(
                    f"Using {len(current_monitored_locations)} priority locations for prediction: {priority_names if priority_names else [f'({loc.latitude},{loc.longitude})' for loc in current_monitored_locations]}"
                )
            else:
                self.logger.info(
                    "No priority locations set. Attempting to select locations based on prediction accuracy."
                )
                all_locations = self._get_all_configured_locations()
                if not all_locations:
                    self.monitored_locations = []
                    return

                await self._refresh_accuracy_cache_if_stale(all_locations)
                current_monitored_locations = self._select_locations_by_accuracy_weight(
                    all_locations
                )

        self.monitored_locations = current_monitored_locations

    def determine_autonomous_actions(
        self, prediction: Dict[str, Any], location: LocationModel
    ) -> str:
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
            prediction_time = datetime.now(timezone.utc) + timedelta(hours=1)
            prediction = await self.analytics_service.predict_incident_likelihood(
                location=location.model_dump(),  # Convert LocationModel to dictionary
                prediction_time=prediction_time,
            )

            # If high likelihood, determine actions and notify
            # Also log this significant prediction
            likelihood_threshold = self.config.get(
                "likelihood_threshold", 70
            )  # Configurable threshold
            if prediction.get("likelihood_score_percent", 0) > likelihood_threshold:
                action_taken = self.determine_autonomous_actions(prediction, location)

                # Log the prediction
                log_data = {
                    "location_name": location.name,
                    "location_latitude": location.latitude,
                    "location_longitude": location.longitude,
                    "predicted_event_start_time": prediction_time,  # This is when the predicted event window starts
                    "predicted_event_end_time": prediction_time
                    + timedelta(hours=1),  # Assuming a 1-hour prediction window
                    "prediction_type": "incident_likelihood",
                    "predicted_value": prediction,  # Store the full prediction dictionary
                    "source_of_prediction": "PredictionScheduler_HighLikelihood",
                    "kpi_snapshot_at_prediction": None,  # Placeholder for now
                    # outcome_verified and related fields will be updated later by a different process
                }
                try:
                    log_id = await self.analytics_service.record_prediction_log(
                        log_data
                    )
                    if log_id:
                        self.logger.info(
                            f"Successfully recorded high-likelihood prediction (ID: {log_id}) for location {location.name or (location.latitude, location.longitude)}."
                        )
                    else:
                        self.logger.warning(
                            f"Failed to record high-likelihood prediction for location {location.name or (location.latitude, location.longitude)}."
                        )
                except Exception as e_log:
                    self.logger.error(
                        f"Error calling record_prediction_log: {e_log}", exc_info=True
                    )

                notification = GeneralNotification(
                    message_type="traffic_prediction",  # Add required message_type
                    message=f"Traffic Prediction for {location.name}: {prediction.get('message', '')}",
                    severity=prediction.get("severity", "info"),
                    suggested_actions=prediction.get("suggested_actions", []),
                    title=f"Traffic Prediction - {location.name}",
                )

                message = WebSocketMessage(
                    type=WebSocketMessageTypeEnum.PREDICTION_ALERT, data=notification
                )

                # Use analytics service's connection manager to broadcast
                if self.analytics_service._connection_manager:
                    # Creating a unique topic per location for targeted messages
                    location_hash = abs(hash((location.latitude, location.longitude)))
                    await self.analytics_service._connection_manager.broadcast_to_topic(
                        message, topic=f"predictions:{location_hash}"
                    )
                    logger.info(
                        f"Sent high likelihood notification for location {location.latitude},{location.longitude} with action: {action_taken}"
                    )

        except Exception as e:
            logger.error(
                f"Error making prediction for location latitude={location.latitude} longitude={location.longitude} name='{location.name}': {e}"
            )

    async def run(self):
        """Run the prediction scheduler"""
        self.is_running = True
        while self.is_running:
            try:
                await (
                    self._load_monitored_locations()
                )  # Load locations at the start of each cycle

                if not self.monitored_locations:
                    self.logger.warning(
                        "No locations loaded for prediction cycle. Sleeping before retry."
                    )
                else:
                    # Make predictions for all monitored locations
                    prediction_tasks = []
                    for location in self.monitored_locations:
                        prediction_tasks.append(self._predict_and_notify(location))
                    await asyncio.gather(
                        *prediction_tasks
                    )  # Run predictions concurrently
                    self.logger.info(
                        f"Completed prediction cycle for {len(self.monitored_locations)} locations."
                    )

                # Wait for next interval
                await asyncio.sleep(self.prediction_interval.total_seconds())
            except Exception as e:
                logger.error(f"Error in prediction scheduler: {e}")
                await asyncio.sleep(60)  # Wait a minute before retrying

    async def start(self):
        """Start the prediction scheduler"""
        if not self.config.get("enabled", True):
            logger.info("Prediction scheduler is disabled in config. Skipping internal startup.")
            return

        if self.task is None:
            self.task = asyncio.create_task(self.run())
            logger.info("Prediction scheduler started (task created).")
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
