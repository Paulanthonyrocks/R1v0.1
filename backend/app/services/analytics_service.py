import logging
import asyncio
import json
import math
import uuid
import time
import pandas as pd
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone, timedelta
from collections import defaultdict

from sqlalchemy import select

logger = logging.getLogger("app.services.analytics_service")

try:
    from aiokafka import AIOKafkaConsumer
except ImportError:
    AIOKafkaConsumer = None
    logger.warning("aiokafka module not found. Kafka consumer features will be disabled.")

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
from app.ml.anomaly_detector import TrafficAnomalyDetector
from app.websocket.connection_manager import ConnectionManager
from app.services.traffic_signal_service import TrafficSignalService
from app.services.notification_service import NotificationService
from app.models.traffic import IncidentReport, IncidentTypeEnum, IncidentSeverityEnum, IncidentStatusEnum


class AnalyticsService:
    def __init__(
        self,
        config: Dict[str, Any],
        connection_manager: ConnectionManager,
        database_manager,
        traffic_predictor=None,
        traffic_signal_service: Optional[TrafficSignalService] = None,
        notification_service: Optional[NotificationService] = None,
    ):
        self.config = config
        self._connection_manager = connection_manager
        self._db_manager = database_manager
        self._traffic_signal_service = traffic_signal_service
        self._notification_service = notification_service
        self._data_cache = TrafficDataCache()
        self._traffic_predictor_instance = None
        self._anomaly_detector_instance = None
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
        self._prediction_verification_task: Optional[asyncio.Task] = None
        
        self._feed_manager = None
        self._active_incidents = {} # { "location_name": timestamp }
        self._kafka_consumer = None
        self._metrics_buffer = []
        self._metrics_buffer_lock = asyncio.Lock()
        self._flush_interval = 60 # Flush every 60 seconds
        self._last_flush_time = time.time()

        logger.info("AnalyticsService initialized.")

    @property
    def _traffic_predictor(self):
        if self._traffic_predictor_instance is None:
            logger.info("Lazy-loading TrafficPredictor...")
            self._traffic_predictor_instance = TrafficPredictor(config=self.config)
        return self._traffic_predictor_instance

    @property
    def _anomaly_detector(self):
        if self._anomaly_detector_instance is None:
            logger.info("Lazy-loading TrafficAnomalyDetector...")
            self._anomaly_detector_instance = TrafficAnomalyDetector(config=self.config)
        return self._anomaly_detector_instance

    def set_feed_manager(self, feed_manager):
        """Sets the feed manager to avoid circular imports."""
        self._feed_manager = feed_manager
        logger.info("FeedManager set in AnalyticsService.")

    async def predict_incident_likelihood(
        self, location: Dict[str, Any], prediction_time: datetime
    ) -> Dict[str, Any]:
        """
        Predicts the likelihood of an incident based on historical patterns and current state.
        """
        logger.info(
            f"Predicting incident likelihood for {location.get('name', 'N/A')} at {prediction_time}"
        )

        latitude = location.get("latitude")
        longitude = location.get("longitude")

        if latitude is None or longitude is None:
            logger.error("Location must contain latitude and longitude for prediction.")
            return {"incident_likelihood": 0.0, "error": "Missing location coordinates"}

        # Fetch recent data points for the specific location
        sequence_length = getattr(self._traffic_predictor, 'sequence_length', 10)
        recent_traffic_data = self._data_cache.get_recent_data(latitude, longitude, hours=int(sequence_length / 6))

        # 1. Use the trained TrafficPredictor if available
        if self._traffic_predictor and hasattr(self._traffic_predictor, 'model') and self._traffic_predictor.model is not None:
            try:
                prediction_result = self._traffic_predictor.predict_incident_likelihood(
                    recent_traffic_data=recent_traffic_data,
                    location=location,
                    prediction_time=prediction_time,
                )
                return prediction_result
            except Exception as e:
                logger.error(f"Traffic predictor model failed: {e}")

        # 2. Secondary: Use Anomaly Detector score as a likelihood proxy
        if self._anomaly_detector and recent_traffic_data:
            anomaly_result = self._anomaly_detector.detect_anomaly(recent_traffic_data)
            # Map MAE/Z-score to a 0-1 likelihood (heuristic)
            score = min(0.9, anomaly_result.get("score", 0) / 5.0) 
            if anomaly_result.get("is_anomaly"):
                return {
                    "location": location,
                    "prediction_time": prediction_time.isoformat(),
                    "incident_likelihood": score,
                    "confidence_score": 0.6,
                    "contributing_factors": [anomaly_result.get("reason", "anomaly")],
                    "recommendations": ["High pattern deviation detected. Monitor closely."]
                }

        # 3. Final Fallback: Statistical Rule-based prediction
        return self._traffic_predictor._rule_based_prediction(
            location, prediction_time, pd.DataFrame(recent_traffic_data)
        )

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

        # Handle Anomalies
        anomalies = metrics.get("anomalies", [])
        now = time.time()
        for anomaly in anomalies:
            # Trigger Incident/Alert based on anomaly
            severity_map = {
                "Critical": IncidentSeverityEnum.CRITICAL,
                "Warning": IncidentSeverityEnum.HIGH,
                "INFO": IncidentSeverityEnum.MEDIUM
            }
            
            # Debounce check
            inc_key = f"{feed_id}_{anomaly.get('details', 'unknown')}"
            last_time = self._active_incidents.get(inc_key, 0)
            
            # Create incident for significant anomalies if not recently reported
            if anomaly.get("severity") in ["Critical", "Warning"]:
                if now - last_time > 300: # 5 minute cooldown
                    self._active_incidents[inc_key] = now
                    asyncio.create_task(self._create_and_save_incident(
                        location={"latitude": latitude, "longitude": longitude},
                        incident_type=IncidentTypeEnum.OTHER, 
                        severity=severity_map.get(anomaly.get("severity"), IncidentSeverityEnum.MEDIUM),
                        description=f"Automated Alert: {anomaly.get('details')}",
                        source_feed_id=feed_id,
                        details=anomaly
                    ))
                else:
                    logger.debug(f"Debounced duplicate incident for {feed_id}: {anomaly.get('details')}")

        if latitude is not None and longitude is not None:
            self._data_cache.add_data_point(latitude, longitude, timestamp, metrics)
            
            # Buffer for TimescaleDB
            async with self._metrics_buffer_lock:
                self._metrics_buffer.append({
                    "id": self._data_cache._get_location_key(latitude, longitude),
                    "timestamp": timestamp,
                    "vehicle_count": metrics.get("total_vehicles", metrics.get("vehicle_count", 0)),
                    "average_speed": metrics.get("average_speed_kmh", metrics.get("average_speed", 0.0)),
                    "congestion_score": metrics.get("congestion_score", 0.0),
                    "latitude": latitude,
                    "longitude": longitude,
                    "extra_data": {
                        "lane_occupancy": metrics.get("lane_occupancy"),
                        "queue_lengths": metrics.get("queue_lengths")
                    }
                })
                
                # Check for flush
                if len(self._metrics_buffer) >= 100 or (time.time() - self._last_flush_time > self._flush_interval):
                    asyncio.create_task(self._flush_metrics_to_db())
        else:
            logger.warning(
                f"Metrics for feed {feed_id} missing latitude or longitude (Lat: {latitude}, Lon: {longitude}). Cannot add to TrafficDataCache."
            )

    async def _flush_metrics_to_db(self):
        """Flushes the metrics buffer to the database."""
        async with self._metrics_buffer_lock:
            if not self._metrics_buffer:
                return
            
            batch = self._metrics_buffer.copy()
            self._metrics_buffer.clear()
            self._last_flush_time = time.time()
            
        try:
            await self._db_manager.save_location_metrics_batch(batch)
            logger.info(f"Flushed {len(batch)} metrics to database.")
        except Exception as e:
            logger.error(f"Error flushing metrics to database: {e}")
            # Optional: Put back in buffer or discard

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
            # Construct dictionary matching the create_incident expectation
            now = datetime.now(timezone.utc)
            incident_data = {
                "id": str(uuid.uuid4()),
                "feed_id": source_feed_id,
                "type": incident_type.value,
                "severity": severity.value,
                "description": description,
                "status": IncidentStatusEnum.REPORTED.value,
                "timestamp": time.time(),
                "created_at": now,
                "updated_at": now,
                "latitude": location.get("latitude"),
                "longitude": location.get("longitude"),
                "snapshot_path": details.get("snapshot_path") if details else None
            }
            
            await self._db_manager.create_incident(incident_data)
            logger.info(f"Created and saved incident: {incident_data['id']}")

            # Broadcast new incident via WebSocket
            message = WebSocketMessage(
                type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION, 
                data={
                    "message_type": "new_incident",
                    "title": "New Incident Reported",
                    "message": description,
                    "severity": severity.value,
                    "incident_id": incident_data["id"]
                }
            )
            await self._connection_manager.broadcast_to_topic(
                message.model_dump_json(), topic="incidents"
            )

            # External Notifications (Slack/Discord)
            if self._notification_service:
                asyncio.create_task(self._notification_service.notify_incident(incident_data))

            # Trigger Snapshot if feed_id is provided
            if source_feed_id and self._feed_manager:
                try:
                    await self._feed_manager.request_snapshot(source_feed_id, incident_data["id"])
                except Exception as e:
                    logger.warning(f"Failed to request snapshot for incident {incident_data['id']}: {e}")

        except Exception as e:
            logger.error(f"Error creating or saving incident: {e}", exc_info=True)

    async def update_incident_snapshot(self, incident_id: str, snapshot_path: str):
        """Updates an incident with its snapshot path and broadcasts the update."""
        try:
            success = await self._db_manager.update_incident(incident_id, {"snapshot_path": snapshot_path})
            if success:
                logger.info(f"Updated incident {incident_id} with snapshot: {snapshot_path}")
                
                # Broadcast SNAPSHOT_READY
                message = WebSocketMessage(
                    type=WebSocketMessageTypeEnum.SNAPSHOT_READY,
                    data={
                        "incident_id": incident_id,
                        "snapshot_path": snapshot_path
                    }
                )
                await self._connection_manager.broadcast_to_topic(
                    message.model_dump_json(), topic="incidents"
                )
        except Exception as e:
            logger.error(f"Error updating incident snapshot: {e}")

    async def create_and_save_alert(self, alert: Alert):
        """
        Saves an alert to the database.
        """
        logger.info(f"Saving alert: Severity={alert.severity}, Message='{alert.message}'")
        log_id = await self._db_manager.save_alert(alert)
        logger.info(f"Alert saved successfully with log_id: {log_id}")

        # Auto-create incident for high severity alerts
        if alert.severity in [AlertSeverityEnum.CRITICAL, AlertSeverityEnum.ERROR]:
            await self._create_incident_from_alert(alert)

        return log_id

    async def _create_incident_from_alert(self, alert: Alert):
        """Helper to create an incident from a critical alert."""
        try:
            # Infer type from message or tags
            inc_type = IncidentTypeEnum.OTHER
            msg_lower = alert.message.lower()
            if "stopped" in msg_lower:
                inc_type = IncidentTypeEnum.STOPPED_VEHICLE
            elif "accident" in msg_lower or "crash" in msg_lower:
                inc_type = IncidentTypeEnum.ACCIDENT
            elif "congestion" in msg_lower:
                inc_type = IncidentTypeEnum.CONGESTION
            elif "wrong way" in msg_lower:
                inc_type = IncidentTypeEnum.OTHER # Or add WRONG_WAY to Enum if supported
            
            # Map AlertSeverity to IncidentSeverity
            inc_severity = IncidentSeverityEnum.MEDIUM
            if alert.severity == AlertSeverityEnum.CRITICAL:
                inc_severity = IncidentSeverityEnum.CRITICAL
            elif alert.severity == AlertSeverityEnum.ERROR:
                inc_severity = IncidentSeverityEnum.HIGH
                
            location = {
                "latitude": alert.latitude,
                "longitude": alert.longitude
            }
            
            await self._create_and_save_incident(
                location=location,
                incident_type=inc_type,
                severity=inc_severity,
                description=alert.message,
                source_feed_id=alert.feed_id,
                details=alert.details
            )
        except Exception as e:
            logger.error(f"Failed to auto-create incident from alert: {e}")

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

    async def get_forecast_vs_actual_data(
        self, 
        latitude: float, 
        longitude: float, 
        hours: int = 24
    ) -> List[Dict[str, Any]]:
        """
        Retrieves historical prediction vs actual data for a location.
        Returns a list of data points for charting.
        """
        try:
            start_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            
            # 1. Fetch prediction logs for this location
            # Note: Using SQLAlchemy's select
            stmt = select(PredictionLogModel).filter(
                PredictionLogModel.location_latitude == latitude,
                PredictionLogModel.location_longitude == longitude,
                PredictionLogModel.predicted_event_start_time >= start_time
            ).order_by(PredictionLogModel.predicted_event_start_time.asc())

            async with self._db_manager.get_session() as session:
                result = await session.execute(stmt)
                logs = result.scalars().all()

            # 2. Fetch actual metrics from TimescaleDB
            location_id = self._data_cache._get_location_key(latitude, longitude)
            actual_metrics = await self._db_manager.get_location_metrics(location_id, hours=hours)
            actual_map = {m["timestamp"].isoformat() if hasattr(m["timestamp"], "isoformat") else m["timestamp"]: m for m in actual_metrics}

            # 3. Format into a time-series list
            chart_data = []
            
            # Combine predictions and actuals
            # First, add all actuals to the chart
            for ts_iso, m in actual_map.items():
                chart_data.append({
                    "timestamp": ts_iso,
                    "forecasted": None,
                    "actual": m.get("congestion_score"),
                    "vehicle_count": m.get("vehicle_count"),
                    "average_speed": m.get("average_speed"),
                    "type": "actual"
                })

            # Next, overlay predictions
            for log in logs:
                # Extract predicted value (likelihood or congestion)
                pred_val = 0.0
                if isinstance(log.predicted_value, dict):
                    pred_val = log.predicted_value.get("incident_likelihood") or \
                               log.predicted_value.get("congestion_score") or 0.0
                
                log_ts_iso = log.predicted_event_start_time.isoformat()
                
                # Check if we have an actual for this exact timestamp (unlikely to match exactly)
                # In practice, we might want to find the nearest actual
                chart_data.append({
                    "timestamp": log_ts_iso,
                    "forecasted": pred_val,
                    "actual": None, # Actuals are already added separately
                    "type": log.prediction_type
                })

            # Sort by timestamp
            chart_data.sort(key=lambda x: x["timestamp"])

            return chart_data
        except Exception as e:
            logger.error(f"Error getting forecast vs actual data: {e}", exc_info=True)
            return []

    async def detect_traffic_anomalies(
        self, traffic_data_points: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Detects anomalies using ML and statistical methods.
        """
        anomalies = []
        now = time.time()
        
        # 1. Advanced Anomaly Detection (Pattern-based)
        # Use the last N points for sequence-based detection
        if self._anomaly_detector:
            result = self._anomaly_detector.detect_anomaly(traffic_data_points)
            if result.get("is_anomaly"):
                latest = traffic_data_points[-1]
                location_name = latest.get("location_description", "Unknown Location")
                
                # Cooldown check: 5 minutes (300 seconds)
                last_reported = self._active_incidents.get(location_name, 0)
                
                if (now - last_reported > 300):
                    self._active_incidents[location_name] = now
                    
                    anomalies.append({
                        "type": "pattern_anomaly",
                        "description": f"AI Detected Anomaly: {result.get('reason')} (Score: {result.get('score'):.2f})",
                        "location": location_name,
                        "timestamp": latest.get("timestamp", datetime.now(timezone.utc)).isoformat(),
                    })
                    
                    # Auto-generate incident
                    severity = IncidentSeverityEnum.HIGH if result.get("score", 0) > 0.8 else IncidentSeverityEnum.MEDIUM
                    location_data = {
                        "latitude": latest.get("latitude"),
                        "longitude": latest.get("longitude"),
                        "name": location_name
                    }
                    await self._create_and_save_incident(
                        location=location_data, 
                        incident_type=IncidentTypeEnum.OTHER, 
                        severity=severity, 
                        description=anomalies[-1]["description"], 
                        source_feed_id=latest.get("feed_id"), 
                        details={"anomaly_result": result, "data_point": latest}
                    )

        # 2. Hard Threshold Fallback (Rule-based)
        speed_threshold = 10.0  # km/h
        vehicle_count_threshold = 15

        for data_point in traffic_data_points[-1:]: # Only check the latest for rules to avoid double reporting
            speed = data_point.get("average_speed", float('inf'))
            vehicle_count = data_point.get("vehicle_count", 0)
            location_name = data_point.get("location_description", "Unknown Location")

            if speed < speed_threshold and vehicle_count > vehicle_count_threshold:
                # Check if we already flagged this point via AI or cooldown
                last_reported = self._active_incidents.get(location_name, 0)
                if (now - last_reported > 300) and not any(a["location"] == location_name for a in anomalies):
                    self._active_incidents[location_name] = now
                    anomalies.append({
                        "type": "traffic_anomaly",
                        "description": f"Rule-based detection: Low speed ({speed:.1f} km/h) with high vehicle count ({vehicle_count}) detected.",
                        "location": location_name,
                        "timestamp": data_point.get("timestamp", datetime.now(timezone.utc)).isoformat(),
                    })
                    # Create incident
                    location_data = {
                        "latitude": data_point.get("latitude"),
                        "longitude": data_point.get("longitude"),
                        "name": location_name
                    }
                    await self._create_and_save_incident(
                        location=location_data, 
                        incident_type=IncidentTypeEnum.CONGESTION, 
                        severity=IncidentSeverityEnum.MEDIUM, 
                        description=anomalies[-1]["description"], 
                        source_feed_id=data_point.get("feed_id"), 
                        details=data_point
                    )
        
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
        data = self._data_cache.get_all_location_summaries()
        logger.info(f"Retrieved {len(data)} node congestion summaries from cache.")
        return data

    def get_data_cache(self) -> TrafficDataCache:
        """Returns the internal data cache instance."""
        return self._data_cache

    def get_traffic_predictor(self) -> TrafficPredictor:
        """Returns the internal traffic predictor instance."""
        return self._traffic_predictor

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
        
        # Start prediction verification task
        if self._prediction_verification_task is None or self._prediction_verification_task.done():
            self._prediction_verification_task = asyncio.create_task(self._verify_predictions_loop())
            logger.info("Prediction verification task started.")

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

    async def _verify_predictions_loop(self):
        """Periodically checks past predictions and verifies them against actual data."""
        while True:
            try:
                # 1. Fetch unverified predictions that should have happened by now
                now = datetime.now(timezone.utc)
                stmt = select(PredictionLogModel).filter(
                    PredictionLogModel.outcome_verified == False,
                    PredictionLogModel.predicted_event_end_time <= now
                )
                
                async with self._db_manager.get_session() as session:
                    result = await session.execute(stmt)
                    pending = result.scalars().all()
                
                if pending:
                    logger.info(f"Found {len(pending)} predictions pending verification.")
                    for log in pending:
                        # 2. Get actual data from cache for that location/time
                        actual_data = self._data_cache.get_recent_data(
                            log.location_latitude, 
                            log.location_longitude,
                            hours=1 # check the window
                        )
                        
                        if actual_data:
                            # Calculate actual metrics (e.g. max congestion score seen in window)
                            # This is a simplification
                            max_congestion = max([d.get("congestion_score", 0) for d in actual_data])
                            avg_speed = sum([d.get("average_speed", 0) for d in actual_data]) / len(actual_data)
                            
                            # 3. Update the log
                            await self._db_manager.update_prediction_log(log.id, {
                                "outcome_verified": True,
                                "actual_outcome_type": "congestion_verified",
                                "actual_outcome_details": {
                                    "congestion_score": max_congestion,
                                    "average_speed": avg_speed
                                },
                                "verified_at": now
                            })
                
            except Exception as e:
                logger.error(f"Error in prediction verification loop: {e}")
            
            await asyncio.sleep(300) # Run every 5 minutes

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
        if AIOKafkaConsumer is None:
            logger.warning("AIOKafkaConsumer not available. Skipping Kafka consumer loop.")
            return

        logger.info("Starting AIOKafka consumer loop for processed traffic data.")
        
        consumer = AIOKafkaConsumer(
            self.config["kafka"]["processed_topic"],
            bootstrap_servers=self.config["kafka"]["brokers"],
            group_id=self.config["kafka"]["group_id"],
            auto_offset_reset='earliest'
        )
        
        try:
            await consumer.start()
            async for message in consumer:
                try:
                    val_bytes = message.value
                    if val_bytes:
                        data = json.loads(val_bytes.decode('utf-8'))
                        latitude = data.get("latitude")
                        longitude = data.get("longitude")
                        timestamp_str = data.get("timestamp")
                        
                        if latitude is not None and longitude is not None and timestamp_str:
                            timestamp = datetime.fromisoformat(timestamp_str)
                            self._data_cache.add_data_point(latitude, longitude, timestamp, data)
                        else:
                            logger.warning(f"Skipping message due to missing data: {data}")
                        
                        # Yield control to the event loop
                        await asyncio.sleep(0)
                        
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode message: {message.value}")
                except Exception as e:
                    logger.error(f"Error processing Kafka message: {e}", exc_info=True)
                    
        except asyncio.CancelledError:
            logger.info("Kafka consumer loop task cancelled.")
        except Exception as e:
            logger.error(f"Kafka consumer error: {e}")
        finally:
            await consumer.stop()
            logger.info("AIOKafka consumer stopped.")

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
                message.model_dump_json(), topic="node_congestion"
            )
            logger.debug(
                f"Broadcasted {len(nodes_for_broadcast)} node congestion updates."
            )
        else:
            logger.debug("No node congestion data to broadcast.")
