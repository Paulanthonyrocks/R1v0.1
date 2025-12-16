import logging
import asyncio
import json
import math
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
from collections import defaultdict

from sqlalchemy import select
from kafka import KafkaConsumer
from kafka.errors import KafkaError

from app.models.websocket import (
    WebSocketMessage,
    WebSocketMessageTypeEnum,
    GeneralNotification,
    NodeCongestionUpdatePayload,
    NodeCongestionUpdateData,
)
from app.models.alerts import Alert, AlertSeverityEnum
from app.models.analytics import (
    LocationModel,
    PredictionLogModel,
)
from app.ml.data_cache import TrafficDataCache
from app.ml.traffic_predictor import TrafficPredictor
from app.websocket.connection_manager import ConnectionManager
from app.services.traffic_signal_service import TrafficSignalService
from app.models.traffic import IncidentReport, IncidentTypeEnum, IncidentSeverityEnum

logger = logging.getLogger("app.services.analytics_service")


class AnalyticsService:
    def __init__(
        self,
        config: Dict[str, Any],
        connection_manager: ConnectionManager,
        database_manager,
        traffic_predictor=None,
        traffic_signal_service: Optional[TrafficSignalService] = None,
    ):
        self.config = config
        self._connection_manager = connection_manager
        self._db_manager = database_manager
        self._traffic_signal_service = traffic_signal_service
        self._data_cache = TrafficDataCache()
        self._traffic_predictor = TrafficPredictor(config=config)
        self._prediction_log_table_initialized = False

        self._node_congestion_task: Optional[asyncio.Task] = None
        self._kafka_consumer_task: Optional[asyncio.Task] = None
        self._node_congestion_broadcast_interval = self.config.get("node_congestion_broadcast", {}).get(
            "interval", 5
        )
        self._data_cleanup_task: Optional[asyncio.Task] = None
        self._data_cleanup_interval = self.config.get(
            "data_cleanup_interval", 3600
        ) # Default to 1 hour
        
        self._kafka_consumer = None
        if self.config.get("kafka", {}).get("enabled", False):
            try:
                self._kafka_consumer = KafkaConsumer(
                    self.config["kafka"]["processed_topic"],
                    bootstrap_servers=self.config["kafka"]["brokers"],
                    group_id=self.config["kafka"]["group_id"],
                    auto_offset_reset='earliest',
                    value_deserializer=lambda x: json.loads(x.decode('utf-8')),
                )
            except KafkaError as e:
                logger.error(f"Failed to initialize Kafka consumer: {e}")


        logger.info("AnalyticsService initialized.")

    async def predict_incident_likelihood(
        self, location: Dict[str, Any], prediction_time: datetime
    ) -> Dict[str, Any]:
        """
        Predicts the likelihood of an incident at a given location and time.
        This is a placeholder implementation.
        """
        logger.info(
            f"Predicting incident likelihood for {location.get('name', 'N/A')} at {prediction_time}"
        )

        # Retrieve recent traffic data for the location from the data cache
        # Assuming location has 'latitude' and 'longitude' keys
        latitude = location.get("latitude")
        longitude = location.get("longitude")

        if latitude is None or longitude is None:
            logger.error("Location must contain latitude and longitude for prediction.")
            return {"incident_likelihood": 0.0, "error": "Missing location coordinates"}

        # Fetch recent data points for the specific location
        # The number of data points to fetch should correspond to the model's sequence_length
        # This assumes TrafficPredictor has a 'sequence_length' attribute
        sequence_length = getattr(self._traffic_predictor, 'sequence_length', 10) # Default to 10 if not found
        recent_traffic_data = self._data_cache.get_recent_data(latitude, longitude, hours=int(sequence_length / 6)) # Assuming 6 data points per hour

        # Check if traffic prediction is enabled in config
        if not self.config.get("traffic_prediction", {}).get("enabled", True):
            logger.info("Traffic prediction is disabled in config. Returning dummy prediction.")
            return {
                "location": location,
                "prediction_time": prediction_time.isoformat(),
                "incident_likelihood": 0.5,  # Dummy value
                "confidence_score": 0.5,  # Dummy value
                "contributing_factors": ["traffic_prediction_disabled"],
                "recommendations": ["enable_traffic_prediction_in_config"],
                "likelihood_score_percent": 50.0,
                "message": "Traffic prediction is currently disabled by configuration.",
                "severity": "info",
                "suggested_actions": [],
            }

        if not self._traffic_predictor or not hasattr(self._traffic_predictor, 'predict_incident_likelihood'):
            logger.warning("Traffic predictor not initialized or missing prediction method. Returning dummy prediction.")
            return {
                "location": location,
                "prediction_time": prediction_time.isoformat(),
                "incident_likelihood": 0.5,  # Dummy value
                "confidence_score": 0.5,  # Dummy value
                "contributing_factors": ["predictor_unavailable"],
                "recommendations": ["check_predictor_setup"],
                "likelihood_score_percent": 50.0,
                "message": "Predictor unavailable.",
                "severity": "info",
                "suggested_actions": [],
            }

        try:
            prediction_result = self._traffic_predictor.predict_incident_likelihood(
                recent_traffic_data=recent_traffic_data,
                location=location,
                prediction_time=prediction_time,
            )

            return prediction_result

            # Check if prediction indicates a high likelihood of an incident
            incident_likelihood_threshold = self.config.get("traffic_prediction", {}).get("incident_likelihood_threshold", 0.7)
            if prediction_result.get("incident_likelihood", 0.0) > incident_likelihood_threshold:
                 # Create an incident report based on the high likelihood prediction
                 await self._create_and_save_incident(
                    location=location,
                    incident_type=IncidentTypeEnum.PREDICTION,
                    severity=IncidentSeverityEnum.HIGH if prediction_result.get("incident_likelihood", 0.0) > 0.9 else IncidentSeverityEnum.MEDIUM, # Simple severity mapping
                    description=f"High predicted incident likelihood ({prediction_result.get('incident_likelihood', 0.0):.2f}) at {location.get('name', 'N/A')}",
                    details={"prediction_result": prediction_result})

            # If the incident is severe, suggest a signal adjustment
            if prediction_result.get("severity") in [IncidentSeverityEnum.HIGH, IncidentSeverityEnum.CRITICAL] and self._traffic_signal_service:
                 logger.info("High likelihood prediction incident. Suggesting signal adjustment.")
                 await self._traffic_signal_service.suggest_signal_adjustment(
                     incident_location=location, # Use the location dict directly
                     incident_severity=IncidentSeverityEnum[prediction_result.get("severity", "MEDIUM").upper()]) # Ensure severity is enum
        except Exception as e:
            logger.error(f"Error during traffic prediction: {e}", exc_info=True)
            return {"incident_likelihood": 0.0, "error": str(e)}

    async def initialize_prediction_log_table(self):
        if not self._prediction_log_table_initialized:
            try:
                async with self._db_manager.async_engine.begin() as conn:
                    await conn.run_sync(PredictionLogModel.metadata.create_all)
                self._prediction_log_table_initialized = True
                logger.info("PredictionLog table initialized/checked successfully.")
            except Exception as e:
                logger.error(
                    f"Failed to initialize PredictionLog table: {e}", exc_info=True
                )
                raise

    async def process_feed_metrics(self, feed_id: str, metrics: Dict[str, Any]):
        # Placeholder for processing feed metrics
        logger.debug(f"Processing metrics for feed {feed_id}: {metrics}")
        # Extract latitude, longitude, and timestamp from metrics
        latitude = metrics.get("latitude")
        longitude = metrics.get("longitude")
        timestamp = metrics.get(
            "timestamp", datetime.now(timezone.utc)
        )  # Use current UTC time if not provided

        if latitude is not None and longitude is not None and (latitude != 0.0 or longitude != 0.0):
            self._data_cache.add_data_point(latitude, longitude, timestamp, metrics)
        else:
            logger.warning(
                f"Metrics for feed {feed_id} missing or invalid latitude/longitude (0.0, 0.0). Cannot add to TrafficDataCache."
            )

    async def save_vehicle_data(self, vehicle_data: Dict[str, Any]):
        """Saves vehicle data to the database."""
        await self._db_manager.save_vehicle_data(vehicle_data)

    async def record_prediction_log(self, log_data: Dict[str, Any]) -> Optional[str]:
        """Records prediction log data to the database."""
        log_id = await self._db_manager.record_prediction_log(log_data)
        return log_id

    async def _create_and_save_incident(
        self,
        location: Dict[str, Any],
        incident_type: IncidentTypeEnum,
        severity: IncidentSeverityEnum,
        description: str,
        source_feed_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        """Creates and saves a new incident report to the database."""
        try:
            # Ensure location is a LocationModel
            location_model = LocationModel(**location) if isinstance(location, dict) else location

            incident_report = IncidentReport(
                location=location_model,
                type=incident_type,
                severity=severity,
                description=description,
                source_feed_id=source_feed_id,
                details=details or {},
            )
            await self._db_manager.save_incident(incident_report)
            logger.info(f"Created and saved incident: {incident_report.incident_id}")

            # TODO: Broadcast new incident via WebSocket

        except Exception as e:
            logger.error(f"Error creating or saving incident: {e}", exc_info=True)

    async def create_and_save_alert(self, alert: Alert):
        """
        Saves an alert to the database.
        """
        logger.info(f"Saving alert: Severity={alert.severity}, Message='{alert.message}'")
        log_id = await self._db_manager.save_alert(alert)
        logger.info(f"Alert saved successfully with log_id: {log_id}")

        return log_id

    async def get_critical_alert_summary(self) -> Dict[str, Any]:
        """
        Retrieves a summary of critical and unacknowledged alerts.
        """
        try:
            # Define filters for critical and unacknowledged alerts
            filters = {
                "severity_in": [
                    AlertSeverityEnum.CRITICAL.value,
                    AlertSeverityEnum.ERROR.value,
                ],
                "acknowledged": False,
            }

            # Get count of critical unacknowledged alerts
            critical_unack_alert_count = await self._db_manager.count_alerts_filtered(
                filters=filters
            )

            # Get recent critical alerts for type analysis
            recent_critical_alerts = await self._db_manager.get_alerts_filtered(
                filters=filters, limit=3, offset=0
            )

            recent_critical_types = []
            for alert in recent_critical_alerts:
                details = alert.get("details")
                if isinstance(details, str):
                    try:
                        details = json.loads(details)
                    except json.JSONDecodeError:
                        details = {}  # Fallback to empty dict if JSON is invalid

                incident_type = details.get("incident_type", "Unknown")
                recent_critical_types.append(
                    f"{incident_type}: {alert.get('message', 'No message')}"
                )

            return {
                "critical_unack_alert_count": critical_unack_alert_count,
                "recent_critical_types": recent_critical_types,
            }
        except Exception as e:
            logger.error(f"Error getting critical alert summary: {e}", exc_info=True)
            return {
                "recent_critical_types": [],
                "error": str(e),
            }

    async def get_prediction_outcome_summary(
        self,
        source_of_prediction: Optional[str] = None,
        location_latitude: Optional[float] = None,
        location_longitude: Optional[float] = None,
        location_radius_km: float = 1.0,
        time_since: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        Retrieves a summary of prediction outcomes, optionally filtered by source or location.
        """
        try:
            stmt = select(PredictionLogModel).filter(
                PredictionLogModel.outcome_verified
            )

            if source_of_prediction:
                stmt = stmt.filter(
                    PredictionLogModel.source_of_prediction == source_of_prediction
                )

            if location_latitude is not None and location_longitude is not None:
                # This is a simplified proximity check. For real-world, consider geospatial queries.
                # For now, check if the logged location is within a simple square bounding box.
                min_lat = location_latitude - (
                    location_radius_km / 111.0
                )  # Approx 111 km per degree latitude
                max_lat = location_latitude + (location_radius_km / 111.0)
                min_lon = location_longitude - (
                    location_radius_km
                    / (111.0 * math.cos(math.radians(location_latitude)))
                )
                max_lon = location_longitude + (
                    location_radius_km
                    / (111.0 * math.cos(math.radians(location_latitude)))
                )

                stmt = stmt.filter(
                    PredictionLogModel.location_latitude.between(min_lat, max_lat),
                    PredictionLogModel.location_longitude.between(min_lon, max_lon),
                )

            if time_since:
                stmt = stmt.filter(
                    PredictionLogModel.predicted_event_start_time >= time_since
                )

            async with self._db_manager.get_session() as session:
                result = await session.execute(stmt)
                verified_predictions = result.scalars().all()

            total_verified_predictions = len(verified_predictions)
            outcome_counts = defaultdict(int)
            incident_hit_count = 0

            for pred in verified_predictions:
                outcome_counts[pred.actual_outcome_type] += 1
                if pred.actual_outcome_type == "incident_occurred":
                    incident_hit_count += 1

            incident_hit_rate = (
                (incident_hit_count / total_verified_predictions)
                if total_verified_predictions > 0
                else 0.0
            )

            return {
                "total_verified_predictions": total_verified_predictions,
                "outcomes": dict(outcome_counts),
                "accuracy_metrics": {"incident_hit_rate": round(incident_hit_rate, 3)},
            }
        except Exception as e:
            logger.error(
                f"Error getting prediction outcome summary: {e}", exc_info=True
            )
            return {
                "total_verified_predictions": 0,
                "outcomes": {},
                "accuracy_metrics": {"incident_hit_rate": 0.0},
                "error": str(e),
            }

    async def detect_traffic_anomalies(
        self, traffic_data_points: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Simplified anomaly detection: identifies data points with low speed and high vehicle count.
        A real anomaly detection would use more sophisticated statistical or ML models.
        """
        anomalies = []
        speed_threshold = 10.0  # km/h
        vehicle_count_threshold = 5

        logger.info(f"Performing simplified anomaly detection on {len(traffic_data_points)} data points.")

        for data_point in traffic_data_points:
            speed = data_point.get("average_speed", float('inf'))
            vehicle_count = data_point.get("vehicle_count", 0)
            location_name = data_point.get("location_description", "Unknown Location")

            if speed < speed_threshold and vehicle_count > vehicle_count_threshold:
                anomalies.append({
                    "type": "traffic_anomaly",
                    "description": f"Low speed ({speed:.1f} km/h) with high vehicle count ({vehicle_count}) detected.",
                    "location": location_name,
                    "timestamp": data_point.get("timestamp", datetime.now(timezone.utc)).isoformat(),
                })
                # Create an incident report for the detected anomaly
                severity = IncidentSeverityEnum.HIGH if speed < 5.0 and vehicle_count > 10 else IncidentSeverityEnum.MEDIUM # More specific severity
                location_data = {
                    "latitude": data_point.get("latitude"),
                    "longitude": data_point.get("longitude"),
                    "name": location_name # Use extracted location name
                }
                # Filter out None values from location_data
                location_data = {k: v for k, v in location_data.items() if v is not None}
                await self._create_and_save_incident(location=location_data, incident_type=IncidentTypeEnum.CONGESTION, severity=severity, description=anomalies[-1]["description"], source_feed_id=None, details=data_point)
        return anomalies

    async def generate_trend_summary(
        self, region_id: str, start_date: datetime, end_date: datetime
    ) -> Dict[str, Any]:
        """
        Simplified trend summary generation: calculates average speed and total vehicle count
        from data in the TrafficDataCache within the specified time range.
        A real trend summary would involve more complex data aggregation and potentially DB queries.
        """
        logger.info(
            f"Generating trend summary for {region_id} from {start_date} to {end_date}"
        )

        # Assuming TrafficDataCache has a method to get data within a time range,
        # and that the region_id can be used as a filter if the cache supports it.
        # For this simplified implementation, we'll just get all data in the time range
        # and ignore region_id for now, assuming the cache is for the overall system.
        historical_data = self._data_cache.get_data_in_range(start_date, end_date)

        total_speed = 0
        total_vehicles = 0
        data_point_count = 0

        for data_point in historical_data:
            total_speed += data_point.get("average_speed", 0.0)
            total_vehicles += data_point.get("vehicle_count", 0)
            data_point_count += 1

        average_speed = total_speed / data_point_count if data_point_count > 0 else 0.0

        return {"region_id": region_id, "average_speed_kmh": round(average_speed, 1), "total_vehicles_sum": total_vehicles}

    async def broadcast_operational_alert(
        self, title: str, message_text: str, severity: str
    ):
        logger.info(f"Broadcasting operational alert: {title} - {message_text}")
        # Simplified implementation broadcasting to a general operational alerts topic.
        # A real implementation might target specific user roles or clients.
        notification = GeneralNotification(
            message_type="operational_alert",
            title=title,
            message=message_text,
            severity=severity,
        )
        message = WebSocketMessage(
            type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION, data=notification.model_dump()
        )
        await self._connection_manager.broadcast_to_topic(
            message.model_dump_json(), topic="operational_alerts"
        )

    async def send_user_specific_alert(self, user_id: str, notification_model: Any):
        # Placeholder for sending user-specific alerts
        # This would typically involve looking up the user's active WebSocket connections
        # and sending the message via connection_manager.send_personal_message_model
        # For now, just log.
        logger.info(f"Intending to send user-specific alert to {user_id}: {notification_model.title}")

    def get_current_system_kpis_summary(self) -> Dict[str, Any]:
        """
        Simplified system KPIs summary: aggregates data from the TrafficDataCache
        to provide estimated overall congestion, speed, vehicle flow,
        and active monitored locations.
        A real system KPI summary would be more comprehensive and potentially involve
        aggregations over longer periods or different data sources.
        """
        logger.debug("Getting current system KPIs summary.")

        # Get the latest summary data from the cache
        latest_summaries = self._data_cache.get_all_location_summaries()

        total_vehicles_sum = 0
        total_speed_sum = 0
        active_locations_count = len(latest_summaries)
        congestion_scores_sum = 0

        for summary in latest_summaries:
            total_vehicles_sum += summary.get("vehicle_count", 0)
            total_speed_sum += summary.get("average_speed", 0.0)
            congestion_scores_sum += summary.get("congestion_score", 0.0)

        average_speed_kmh = total_speed_sum / active_locations_count if active_locations_count > 0 else 0.0
        average_congestion_score = congestion_scores_sum / active_locations_count if active_locations_count > 0 else 0.0

        # Simple mapping of average congestion score to a level
        if average_congestion_score > 0.7:
            overall_congestion_level = "HIGH"
        elif average_congestion_score > 0.3:
            overall_congestion_level = "MEDIUM"
        else:
            overall_congestion_level = "LOW" if active_locations_count > 0 else "UNKNOWN"

        return {
            "overall_congestion_level": overall_congestion_level,
            "average_speed_kmh": round(average_speed_kmh, 1),
            "total_vehicle_flow_estimate": total_vehicles_sum, # Simple sum as estimate
            "active_monitored_locations": active_locations_count,
            "system_stability_indicator": "NO_DATA",
        }

    async def get_all_location_congestion_data(self) -> List[Dict[str, Any]]:
        logger.info("Fetching all location congestion data summaries from cache.")
        # This method should ideally fetch from self._data_cache
        # For now, return mock data or data from db_manager if it has a direct method
        data = self._data_cache.get_all_location_summaries()
        logger.info(f"Retrieved {len(data)} node congestion summaries from cache.")
        
        # If no data in cache, return mock data for demonstration
        if not data:
            logger.info("No data in cache, returning mock data for demonstration.")
            from datetime import datetime, timezone
            mock_data = [
                {
                    "id": "34.0522,-118.2437",
                    "name": "Downtown LA Intersection",
                    "latitude": 34.0522,
                    "longitude": -118.2437,
                    "timestamp": datetime.now(timezone.utc),
                    "vehicle_count": 45,
                    "average_speed": 35.2,
                    "congestion_score": 65.5,
                },
                {
                    "id": "34.0736,-118.4004",
                    "name": "Santa Monica Boulevard",
                    "latitude": 34.0736,
                    "longitude": -118.4004,
                    "timestamp": datetime.now(timezone.utc),
                    "vehicle_count": 78,
                    "average_speed": 28.7,
                    "congestion_score": 72.3,
                },
                {
                    "id": "34.0195,-118.4912",
                    "name": "Venice Beach Area",
                    "latitude": 34.0195,
                    "longitude": -118.4912,
                    "timestamp": datetime.now(timezone.utc),
                    "vehicle_count": 32,
                    "average_speed": 42.1,
                    "congestion_score": 48.9,
                }
            ]
            return mock_data
        
        return data

    async def start_background_tasks(self):
        if self.config.get("node_congestion_broadcast", {}).get("enabled", True):
            if self._node_congestion_task is None or self._node_congestion_task.done():
                self._node_congestion_task = asyncio.create_task(
                    self._broadcast_node_congestion_updates_loop()
                )
                logger.info("Node congestion broadcast task started.")
        if self._data_cleanup_task is None or self._data_cleanup_task.done():
            self._data_cleanup_task = asyncio.create_task(
                self._cleanup_data_cache_loop()
            )
            logger.info("Data cache cleanup task started.")
        if self._kafka_consumer and (self._kafka_consumer_task is None or self._kafka_consumer_task.done()):
            self._kafka_consumer_task = asyncio.create_task(
                self._consume_processed_traffic_data_loop()
            )
            logger.info("Kafka consumer task started.")

    async def stop_background_tasks(self):
        if self._node_congestion_task and not self._node_congestion_task.done():
            self._node_congestion_task.cancel()
            try:
                await self._node_congestion_task
            except asyncio.CancelledError:
                logger.info("Node congestion broadcast task cancelled.")
            self._node_congestion_task = None
        if self._data_cleanup_task and not self._data_cleanup_task.done():
            self._data_cleanup_task.cancel()
            try:
                await self._data_cleanup_task
            except asyncio.CancelledError:
                logger.info("Data cache cleanup task cancelled.")
            self._data_cleanup_task = None
        if self._kafka_consumer_task and not self._kafka_consumer_task.done():
            self._kafka_consumer_task.cancel()
            try:
                await self._kafka_consumer_task
            except asyncio.CancelledError:
                logger.info("Kafka consumer task cancelled.")
            self._kafka_consumer_task = None

    async def _cleanup_data_cache_loop(self):
        while True:
            try:
                await asyncio.sleep(self._data_cleanup_interval)
                self._data_cache.clean_all_locations()
            except asyncio.CancelledError:
                logger.info("Data cache cleanup loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in data cache cleanup loop: {e}", exc_info=True)

    async def _consume_processed_traffic_data_loop(self):
        logger.info("Starting Kafka consumer loop for processed traffic data.")
        try:
            for message in self._kafka_consumer:
                try:
                    data = message.value
                    latitude = data.get("latitude")
                    longitude = data.get("longitude")
                    timestamp_str = data.get("timestamp")
                    if latitude is not None and longitude is not None and timestamp_str:
                        timestamp = datetime.fromisoformat(timestamp_str)
                        self._data_cache.add_data_point(latitude, longitude, timestamp, data)
                    else:
                        logger.warning(f"Skipping message due to missing data: {data}")
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode message: {message.value}")
                except Exception as e:
                    logger.error(f"Error processing message: {e}", exc_info=True)
        except asyncio.CancelledError:
            logger.info("Kafka consumer loop cancelled.")
        finally:
            if self._kafka_consumer:
                self._kafka_consumer.close()
                logger.info("Kafka consumer closed.")

    async def _broadcast_node_congestion_updates_loop(self):
        while True:
            try:
                await self._broadcast_node_congestion_updates()
                await asyncio.sleep(self._node_congestion_broadcast_interval)
            except asyncio.CancelledError:
                logger.info("Node congestion broadcast loop cancelled.")
                break
            except Exception as e:
                logger.error(
                    f"Error in node congestion broadcast loop: {e}", exc_info=True
                )
                await asyncio.sleep(
                    self._node_congestion_broadcast_interval
                )  # Wait before retrying

    async def _broadcast_node_congestion_updates(self):
        node_data_list = await self.get_all_location_congestion_data()
        if node_data_list:
            # Convert dicts to Pydantic models for broadcast
            nodes_for_broadcast = [
                NodeCongestionUpdateData(
                    id=node.get("id"),
                    name=node.get("name"),
                    latitude=node.get("latitude"),
                    longitude=node.get("longitude"),
                    congestion_score=node.get("congestion_score"),
                    vehicle_count=node.get("vehicle_count"),
                    average_speed=node.get("average_speed"),
                    timestamp=node.get(
                        "timestamp", datetime.now(timezone.utc)
                    ),  # Ensure timestamp is present
                )
                for node in node_data_list
            ]
            payload = NodeCongestionUpdatePayload(nodes=nodes_for_broadcast)
            message = WebSocketMessage(
                type=WebSocketMessageTypeEnum.NODE_CONGESTION_UPDATE, data=payload
            )
            await self._connection_manager.broadcast_to_topic(
                message, topic="node_congestion"
            )
            logger.debug(
                f"Broadcasted {len(nodes_for_broadcast)} node congestion updates."
            )
        else:
            logger.debug("No node congestion data to broadcast.")
