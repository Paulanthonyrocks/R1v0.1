import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone # Removed timedelta
import random

from app.models.traffic import TrafficData, AggregatedTrafficTrend, IncidentReport, IncidentTypeEnum, IncidentSeverityEnum, LocationModel
from app.models.alerts import Alert, AlertSeverityEnum
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, NewAlertNotification, GeneralNotification
from app.websocket.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)


class AnalyticsService:
    def __init__(self, config: Dict[str, Any], connection_manager: ConnectionManager):
        self.config = config.get("analytics_service", {})
        self.historical_data_provider = None # Placeholder for future DB integration
        self._connection_manager = connection_manager
        logger.info("AnalyticsService initialized.")

    async def _broadcast_alert_from_incident(self, incident: IncidentReport):
        if not self._connection_manager:
            return

        alert_severity_map = {
            IncidentSeverityEnum.LOW: AlertSeverityEnum.INFO,
            IncidentSeverityEnum.MEDIUM: AlertSeverityEnum.WARNING,
            IncidentSeverityEnum.HIGH: AlertSeverityEnum.CRITICAL, # Changed ERROR to CRITICAL
            IncidentSeverityEnum.CRITICAL: AlertSeverityEnum.CRITICAL,
        }
        alert_severity = alert_severity_map.get(incident.severity, AlertSeverityEnum.INFO)

        alert_model = Alert(
            timestamp=incident.timestamp,
            severity=alert_severity,
            feed_id=incident.source_feed_id,
            message=f"Incident: {incident.type.value} - {incident.description}",
            details={
                "incident_type": incident.type.value,
                "location": incident.location.model_dump() if incident.location else None,
                "original_severity": incident.severity.value,
                "status": incident.status.value if incident.status else "unknown"
            }
        )
        ws_payload = NewAlertNotification(alert_data=alert_model)
        message = WebSocketMessage(event_type=WebSocketMessageTypeEnum.NEW_ALERT, payload=ws_payload) # Corrected type
        await self._connection_manager.broadcast_message_model(message, specific_topic="alerts")
        if incident.source_feed_id:
            topic = f"feed_alerts:{incident.source_feed_id}"
            await self._connection_manager.broadcast_message_model(message, specific_topic=topic)
        logger.info(f"Broadcasted alert from incident: {alert_model.message[:100]}") # Shorten log

    async def detect_traffic_anomalies(
        self, current_traffic_data: List[TrafficData]
    ) -> List[IncidentReport]:
        logger.info(f"Detecting anomalies in {len(current_traffic_data)} data points.")
        incidents: List[IncidentReport] = []
        if not current_traffic_data: return incidents

        for data_point in current_traffic_data:
            is_anomaly = False
            description_parts = []
            severity = IncidentSeverityEnum.MEDIUM
            incident_type = IncidentTypeEnum.OTHER # Default to OTHER

            # High vehicle count
            count_threshold = self.config.get("anomaly_vehicle_count_threshold", 100)
            if data_point.vehicle_count is not None and data_point.vehicle_count > count_threshold:
                is_anomaly = True
                description_parts.append(f"High vehicle count ({data_point.vehicle_count}).")
                severity = IncidentSeverityEnum.HIGH
                incident_type = IncidentTypeEnum.CONGESTION

            # Low speed
            speed_threshold = self.config.get("anomaly_speed_threshold_kmh", 10)
            if data_point.speed is not None and data_point.speed < speed_threshold:
                description_parts.append(f"Low speed ({data_point.speed} km/h).")
                is_anomaly = True
                new_severity = IncidentSeverityEnum.HIGH if data_point.speed < (speed_threshold / 2) else IncidentSeverityEnum.MEDIUM
                severity = max(severity, new_severity, key=lambda s: list(IncidentSeverityEnum).index(s)) # Keep higher severity
                if incident_type == IncidentTypeEnum.OTHER: incident_type = IncidentTypeEnum.CONGESTION


            if is_anomaly:
                full_description = f"Sensor {data_point.sensor_id}: {' '.join(description_parts)}"
                incident = IncidentReport(
                    location=data_point.location, type=incident_type, severity=severity,
                    description=full_description, source_feed_id=data_point.sensor_id,
                    timestamp=data_point.timestamp
                )
                incidents.append(incident)
                logger.warning(f"Anomaly detected: {full_description}")
                await self._broadcast_alert_from_incident(incident)
        return incidents

    async def predict_incident_likelihood(
        self, location: LocationModel, prediction_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        pred_time = prediction_time or datetime.now(timezone.utc)
        logger.info(f"Predicting incident likelihood for {location.model_dump()} at {pred_time.isoformat()}")

        # Simplified likelihood logic
        likelihood_score = 0.1 # Base likelihood
        factors = ["base_model"]
        if 7 <= pred_time.hour <= 9 or 16 <= pred_time.hour <= 18: # Peak hours
            likelihood_score = 0.65
            factors.append("peak_hour_traffic")

        prediction_result = {
            "location": location.model_dump(),
            "prediction_time": pred_time.isoformat(),
            "predicted_incident_type": IncidentTypeEnum.CONGESTION.value,
            "likelihood_score_percent": round(likelihood_score * 100, 1),
            "confidence": "low", # Placeholder
            "factors_considered": factors
        }

        if self._connection_manager:
            ws_payload = GeneralNotification(message="Incident Likelihood Prediction", details=prediction_result)
            message = WebSocketMessage(event_type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION, payload=ws_payload)
            loc_hash = abs(hash((location.latitude, location.longitude)))
            topic = f"analytics:prediction:{loc_hash}"
            await self._connection_manager.broadcast_message_model(message, specific_topic=topic)
            logger.debug(f"Broadcasted incident likelihood prediction to topic {topic}")
        return prediction_result

    async def generate_trend_summary(
        self, region_id: str, start_time: datetime, end_time: datetime
    ) -> Optional[AggregatedTrafficTrend]:
        logger.info(f"Generating trend summary for {region_id} from {start_time.isoformat()} to {end_time.isoformat()}")

        # Mock data generation for now
        if region_id == "downtown_sector_1": # Example specific logic
            trend_summary = AggregatedTrafficTrend(
                region_id=region_id, start_time=start_time, end_time=end_time,
                average_congestion_score=random.uniform(30, 70),
                contributing_sensors_count=random.randint(5, 15),
                total_vehicle_detections=random.randint(1000, 5000),
                peak_hour=f"{random.choice([7,8,16,17]):02d}:00", # More realistic peak hour
                # average_speed_kmh=random.uniform(15, 45), # This field is not in AggregatedTrafficTrend
                # dominant_vehicle_types=["car", "bus"], # This field is not in AggregatedTrafficTrend
            )
        else: # Generic fallback
            trend_summary = AggregatedTrafficTrend(
                region_id=region_id, start_time=start_time, end_time=end_time,
                average_congestion_score=random.uniform(20, 80),
                contributing_sensors_count=random.randint(2, 10),
                total_vehicle_detections=random.randint(500, 3000),
                peak_hour=f"{random.choice([8,17]):02d}:00"
            )


        if trend_summary and self._connection_manager:
            ws_payload = GeneralNotification(message=f"Traffic Trend Summary for {region_id}",
                                             details=trend_summary.model_dump())
            message = WebSocketMessage(event_type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION, payload=ws_payload)
            topic = f"analytics:summary:{region_id}"
            await self._connection_manager.broadcast_message_model(message, specific_topic=topic)
            logger.debug(f"Broadcasted trend summary to topic {topic}")
        return trend_summary
