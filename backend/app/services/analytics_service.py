import logging
import asyncio
import json
import math
from typing import Dict, Any, Optional, List
from datetime import datetime
from collections import defaultdict

from sqlalchemy import select

from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, GeneralNotification, NodeCongestionUpdatePayload, NodeCongestionUpdateData
from app.models.alerts import AlertSeverityEnum
from app.models.analytics import PredictionLogBase, PredictionLogModel # Assuming these models exist
from app.ml.data_cache import TrafficDataCache # Assuming this class exists
from app.websocket.connection_manager import ConnectionManager # Assuming this class exists
from unittest.mock import MagicMock # For placeholder for traffic_predictor

logger = logging.getLogger("app.services.analytics_service")

class AnalyticsService:
    def __init__(self, config: Dict[str, Any], connection_manager: ConnectionManager, database_manager):
        self.config = config
        self._connection_manager = connection_manager
        self._db_manager = database_manager
        self._data_cache = TrafficDataCache() # Initialize data cache
        self._traffic_predictor = MagicMock() # Placeholder for traffic predictor
        print("AnalyticsService: Before _prediction_log_table_initialized = False")
        self._prediction_log_table_initialized = False
        print("AnalyticsService: After _prediction_log_table_initialized = False")

        self._node_congestion_task: Optional[asyncio.Task] = None
        self._node_congestion_broadcast_interval = self.config.get("node_congestion_broadcast_interval", 5) # seconds

        logger.info("AnalyticsService initialized.")

    async def predict_incident_likelihood(self, location: Dict[str, Any], prediction_time: datetime) -> Dict[str, Any]:
        """
        Predicts the likelihood of an incident at a given location and time.
        This is a placeholder implementation.
        """
        logger.info(f"Predicting incident likelihood for {location.get('name', 'N/A')} at {prediction_time}")
        # In a real scenario, this would involve feeding data to a trained ML model
        # For now, return a dummy prediction
        return {
            "location": location,
            "prediction_time": prediction_time.isoformat(),
            "incident_likelihood": 0.75, # Dummy value
            "confidence_score": 0.8, # Dummy value
            "contributing_factors": ["high_traffic_density", "recent_accidents"],
            "recommendations": ["suggest_alternative_routes", "monitor_area_closely"],
            "likelihood_score_percent": 75.0, # Add this for prediction_scheduler
            "message": "High likelihood of minor incident due to traffic patterns.",
            "severity": "warning",
            "suggested_actions": ["Adjust signal timing", "Deploy traffic control"]
        }

    async def initialize_prediction_log_table(self):
        if not self._prediction_log_table_initialized:
            try:
                async with self._db_manager.async_engine.begin() as conn:
                    await conn.run_sync(PredictionLogBase.metadata.create_all)
                self._prediction_log_table_initialized = True
                logger.info("PredictionLog table initialized/checked successfully.")
            except Exception as e:
                logger.error(f"Failed to initialize PredictionLog table: {e}", exc_info=True)
                raise

    async def process_feed_metrics(self, feed_id: str, metrics: Dict[str, Any]):
        # Placeholder for processing feed metrics
        logger.debug(f"Processing metrics for feed {feed_id}: {metrics}")
        # Extract latitude, longitude, and timestamp from metrics
        latitude = metrics.get("latitude")
        longitude = metrics.get("longitude")
        timestamp = metrics.get("timestamp", datetime.now(timezone.utc)) # Use current UTC time if not provided

        if latitude is not None and longitude is not None:
            self._data_cache.add_data_point(latitude, longitude, timestamp, metrics)
        else:
            logger.warning(f"Metrics for feed {feed_id} missing latitude or longitude. Cannot add to TrafficDataCache.")

    async def save_vehicle_data(self, vehicle_data: Dict[str, Any]):
        # Placeholder for saving vehicle data to DB
        logger.debug(f"Saving vehicle data: {vehicle_data}")
        # Example: await self._db_manager.save_vehicle_data(vehicle_data)

    async def record_prediction_log(self, log_data: Dict[str, Any]) -> Optional[str]:
        # Placeholder for recording prediction logs
        logger.info(f"Recording prediction log: {log_data}")
        # Example: await self._db_manager.record_prediction_log(log_data)
        return "mock_log_id"

    async def get_critical_alert_summary(self) -> Dict[str, Any]:
        """
        Retrieves a summary of critical and unacknowledged alerts.
        """
        try:
            # Define filters for critical and unacknowledged alerts
            filters = {
                "severity_in": [AlertSeverityEnum.CRITICAL.value, AlertSeverityEnum.ERROR.value],
                "acknowledged": False
            }
            
            # Get count of critical unacknowledged alerts
            critical_unack_alert_count = await self._db_manager.count_alerts_filtered(filters=filters)

            # Get recent critical alerts for type analysis
            recent_critical_alerts = await self._db_manager.get_alerts_filtered(filters=filters, limit=3, offset=0)
            
            recent_critical_types = []
            for alert in recent_critical_alerts:
                details = alert.get("details")
                if isinstance(details, str):
                    try:
                        details = json.loads(details)
                    except json.JSONDecodeError:
                        details = {} # Fallback to empty dict if JSON is invalid
                
                incident_type = details.get("incident_type", "Unknown")
                recent_critical_types.append(f"{incident_type}: {alert.get("message", "No message")}")

            return {
                "critical_unack_alert_count": critical_unack_alert_count,
                "recent_critical_types": recent_critical_types
            }
        except Exception as e:
            logger.error(f"Error getting critical alert summary: {e}", exc_info=True)
            return {
                "critical_unack_alert_count": 0,
                "recent_critical_types": [],
                "error": str(e)
            }

    async def get_prediction_outcome_summary(self, source_of_prediction: Optional[str] = None, location_latitude: Optional[float] = None, location_longitude: Optional[float] = None, location_radius_km: float = 1.0, time_since: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Retrieves a summary of prediction outcomes, optionally filtered by source or location.
        """
        try:
            stmt = select(PredictionLogModel).filter(PredictionLogModel.outcome_verified)

            if source_of_prediction:
                stmt = stmt.filter(PredictionLogModel.source_of_prediction == source_of_prediction)

            if location_latitude is not None and location_longitude is not None:
                # This is a simplified proximity check. For real-world, consider geospatial queries.
                # For now, check if the logged location is within a simple square bounding box.
                min_lat = location_latitude - (location_radius_km / 111.0) # Approx 111 km per degree latitude
                max_lat = location_latitude + (location_radius_km / 111.0)
                min_lon = location_longitude - (location_radius_km / (111.0 * math.cos(math.radians(location_latitude))))
                max_lon = location_longitude + (location_radius_km / (111.0 * math.cos(math.radians(location_latitude))))

                stmt = stmt.filter(
                    PredictionLogModel.location_latitude.between(min_lat, max_lat),
                    PredictionLogModel.location_longitude.between(min_lon, max_lon)
                )

            if time_since:
                stmt = stmt.filter(PredictionLogModel.predicted_event_start_time >= time_since)

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

            incident_hit_rate = (incident_hit_count / total_verified_predictions) if total_verified_predictions > 0 else 0.0

            return {
                "total_verified_predictions": total_verified_predictions,
                "outcomes": dict(outcome_counts),
                "accuracy_metrics": {"incident_hit_rate": round(incident_hit_rate, 3)}
            }
        except Exception as e:
            logger.error(f"Error getting prediction outcome summary: {e}", exc_info=True)
            return {
                "total_verified_predictions": 0,
                "outcomes": {},
                "accuracy_metrics": {"incident_hit_rate": 0.0},
                "error": str(e)
            }

    async def detect_traffic_anomalies(self, traffic_data_points: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # Placeholder for anomaly detection
        logger.info(f"Detecting anomalies in {len(traffic_data_points)} data points.")
        return []

    async def generate_trend_summary(self, region_id: str, start_date: datetime, end_date: datetime) -> Dict[str, Any]:
        # Placeholder for trend summary generation
        logger.info(f"Generating trend summary for {region_id} from {start_date} to {end_date}")
        return {"region_id": region_id, "average_speed": 50.0, "total_vehicles": 1000}

    async def broadcast_operational_alert(self, title: str, message_text: str, severity: str):
        # Placeholder for broadcasting operational alerts
        logger.info(f"Broadcasting operational alert: {title} - {message_text}")
        notification = GeneralNotification(
            message_type="operational_alert",
            title=title,
            message=message_text,
            severity=severity
        )
        message = WebSocketMessage(
            type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
            data=notification
        )
        await self._connection_manager.broadcast_message_model(message, specific_topic="operational_alerts")

    async def send_user_specific_alert(self, user_id: str, notification_model: Any):
        # Placeholder for sending user-specific alerts
        logger.info(f"Sending user-specific alert to {user_id}: {notification_model.title}")
        # This would typically involve looking up the user's active WebSocket connections
        # and sending the message via connection_manager.send_personal_message_model
        # For now, just log.

    def get_current_system_kpis_summary(self) -> Dict[str, Any]:
        # Placeholder for KPI summary
        logger.debug("Getting current system KPIs summary.")
        return {
            "overall_congestion_level": "UNKNOWN",
            "average_speed_kmh": 0.0,
            "total_vehicle_flow_estimate": 0,
            "active_monitored_locations": 0,
            "system_stability_indicator": "NO_DATA"
        }

    async def get_all_location_congestion_data(self) -> List[Dict[str, Any]]:
        logger.info("Fetching all location congestion data summaries from cache.")
        # This method should ideally fetch from self._data_cache
        # For now, return mock data or data from db_manager if it has a direct method
        data = self._data_cache.get_all_location_summaries()
        logger.info(f"Retrieved {len(data)} node congestion summaries from cache.")
        return data

    async def start_background_tasks(self):
        if self._node_congestion_task is None or self._node_congestion_task.done():
            self._node_congestion_task = asyncio.create_task(self._broadcast_node_congestion_updates_loop())
            logger.info("AnalyticsService background tasks started.")

    async def stop_background_tasks(self):
        if self._node_congestion_task and not self._node_congestion_task.done():
            self._node_congestion_task.cancel()
            try:
                await self._node_congestion_task
            except asyncio.CancelledError:
                logger.info("AnalyticsService background tasks cancelled.")
            self._node_congestion_task = None

    async def _broadcast_node_congestion_updates_loop(self):
        while True:
            try:
                await self._broadcast_node_congestion_updates()
                await asyncio.sleep(self._node_congestion_broadcast_interval)
            except asyncio.CancelledError:
                logger.info("Node congestion broadcast loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in node congestion broadcast loop: {e}", exc_info=True)
                await asyncio.sleep(self._node_congestion_broadcast_interval) # Wait before retrying

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
                    timestamp=node.get("timestamp", datetime.utcnow()) # Ensure timestamp is present
                )
                for node in node_data_list
            ]
            payload = NodeCongestionUpdatePayload(nodes=nodes_for_broadcast)
            message = WebSocketMessage(
                type=WebSocketMessageTypeEnum.NODE_CONGESTION_UPDATE,
                data=payload
            )
            await self._connection_manager.broadcast_message_model(message, specific_topic="node_congestion")
            logger.debug(f"Broadcasted {len(nodes_for_broadcast)} node congestion updates.")
        else:
            logger.debug("No node congestion data to broadcast.")


# This function is likely deprecated or should be part of the AnalyticsService class
def get_all_location_congestion_data_summaries():
    logger.info("Fetching all location congestion data summaries from cache.")
    # This function should probably not exist outside the class, or should call a class method.
    # For now, it's a placeholder that might be called by old code.
    # This function needs to be updated to use the AnalyticsService instance's _data_cache
    # For now, returning empty list to avoid immediate errors.
    logger.warning("Deprecated function get_all_location_congestion_data_summaries called. Use AnalyticsService.get_all_location_congestion_data instead.")
    return []