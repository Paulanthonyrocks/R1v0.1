import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import random

from app.models.traffic import TrafficData, AggregatedTrafficTrend, IncidentReport, IncidentTypeEnum, IncidentSeverityEnum, LocationModel
from app.models.alerts import Alert, AlertSeverityEnum
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, NewAlertNotification, GeneralNotification
from app.websocket.connection_manager import ConnectionManager

logger = logging.getLogger(__name__)

class AnalyticsService:
    def __init__(self, config: Dict[str, Any], connection_manager: ConnectionManager):
        self.config = config.get("analytics_service", {})
        self.historical_data_provider = None
        self._connection_manager = connection_manager
        logger.info("AnalyticsService initialized.")

    async def _broadcast_alert_from_incident(self, incident: IncidentReport):
        if not self._connection_manager:
            return

        alert_severity_map = {
            IncidentSeverityEnum.LOW: AlertSeverityEnum.INFO,
            IncidentSeverityEnum.MEDIUM: AlertSeverityEnum.WARNING,
            IncidentSeverityEnum.HIGH: AlertSeverityEnum.ERROR,
            IncidentSeverityEnum.CRITICAL: AlertSeverityEnum.CRITICAL,
        }
        alert_severity = alert_severity_map.get(incident.severity, AlertSeverityEnum.INFO)

        alert_model = Alert(
            timestamp=incident.timestamp,
            severity=alert_severity,
            feed_id=incident.source_feed_id,
            message=f"Incident Detected: {incident.type.value} - {incident.description}",
            details={
                "incident_type": incident.type.value,
                "location": incident.location.model_dump() if incident.location else None,
                "original_severity": incident.severity.value,
                "status": incident.status.value if incident.status else None
            }
        )
        
        ws_payload = NewAlertNotification(alert_data=alert_model)
        message = WebSocketMessage(
            event_type=WebSocketMessageTypeEnum.NEW_ALERT_NOTIFICATION,
            payload=ws_payload
        )
        await self._connection_manager.broadcast_message_model(message, specific_topic="alerts")
        if incident.source_feed_id:
            await self._connection_manager.broadcast_message_model(message, specific_topic=f"feed_alerts:{incident.source_feed_id}")
        logger.info(f"Broadcasted alert from incident: {alert_model.message}")

    async def detect_traffic_anomalies(
        self, 
        current_traffic_data: List[TrafficData],
    ) -> List[IncidentReport]:
        logger.info(f"Detecting anomalies in {len(current_traffic_data)} data points.")
        incidents: List[IncidentReport] = []
        if not current_traffic_data:
            return incidents

        for data_point in current_traffic_data:
            is_anomaly = False
            description = ""
            severity = IncidentSeverityEnum.MEDIUM
            incident_type = IncidentTypeEnum.UNKNOWN

            if data_point.vehicle_count is not None and data_point.vehicle_count > self.config.get("anomaly_vehicle_count_threshold", 100):
                is_anomaly = True
                description = f"Unusually high vehicle count ({data_point.vehicle_count}) detected at sensor {data_point.sensor_id}."
                severity = IncidentSeverityEnum.HIGH
                incident_type = IncidentTypeEnum.CONGESTION
            
            if data_point.speed is not None and data_point.speed < self.config.get("anomaly_speed_threshold_kmh", 10):
                if is_anomaly: 
                    description += f" Additionally, very low speed ({data_point.speed} km/h) observed."
                else:
                    description = f"Unusually low speed ({data_point.speed} km/h) detected at sensor {data_point.sensor_id}."
                is_anomaly = True
                severity = IncidentSeverityEnum.HIGH if data_point.speed < 5 else IncidentSeverityEnum.MEDIUM
                incident_type = IncidentTypeEnum.CONGESTION

            if is_anomaly:
                incident = IncidentReport(
                    location=data_point.location,
                    type=incident_type,
                    severity=severity,
                    description=description,
                    source_feed_id=data_point.sensor_id,
                    timestamp=data_point.timestamp 
                )
                incidents.append(incident)
                logger.warning(f"Anomaly detected, creating incident: {description}")
                await self._broadcast_alert_from_incident(incident)
        
        return incidents

    async def predict_incident_likelihood(
        self, 
        location: LocationModel, 
        prediction_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        if prediction_time is None:
            prediction_time = datetime.now(timezone.utc)
        
        logger.info(f"Predicting incident likelihood for location {location.model_dump()} at {prediction_time.isoformat()}")
        
        likelihood_score = 0.1 
        if 7 <= prediction_time.hour <= 9 or 16 <= prediction_time.hour <= 18:
            likelihood_score = 0.65
        
        prediction_result = {
            "location": location.model_dump(),
            "prediction_time": prediction_time.isoformat(),
            "predicted_incident_type": IncidentTypeEnum.CONGESTION.value,
            "likelihood_score_percent": round(likelihood_score * 100, 1),
            "confidence": "low",
            "factors_considered": ["time_of_day_pattern"]
        }

        if self._connection_manager:
            ws_payload = GeneralNotification(
                message="Incident Likelihood Prediction",
                details=prediction_result
            )
            message = WebSocketMessage(
                event_type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                payload=ws_payload
            )
            location_hash = abs(hash((location.latitude, location.longitude)))
            topic = f"analytics:prediction:{location_hash}"
            await self._connection_manager.broadcast_message_model(message, specific_topic=topic)
            logger.debug(f"Broadcasted incident likelihood prediction to topic {topic}")

        return prediction_result

    async def generate_trend_summary(
        self, 
        region_id: str, 
        start_time: datetime, 
        end_time: datetime
    ) -> Optional[AggregatedTrafficTrend]:
        logger.info(f"Generating trend summary for region {region_id} from {start_time.isoformat()} to {end_time.isoformat()}")
        
        trend_summary: Optional[AggregatedTrafficTrend] = None
        if region_id == "downtown_sector_1":
            trend_summary = AggregatedTrafficTrend(
                region_id=region_id,
                start_time=start_time,
                end_time=end_time,
                average_congestion_score=random.uniform(30, 70),
                contributing_sensors_count=random.randint(5,15),
                total_vehicle_detections=random.randint(1000,5000),
                peak_hour=f"{random.randint(7,9):02d}:00 or {random.randint(16,18):02d}:00",
                average_speed_kmh=random.uniform(15, 45),
                dominant_vehicle_types=["car", "bus"],
            )
        
        if trend_summary and self._connection_manager:
            ws_payload = GeneralNotification(
                message=f"Traffic Trend Summary for {region_id}",
                details=trend_summary.model_dump()
            )
            message = WebSocketMessage(
                event_type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                payload=ws_payload
            )
            topic = f"analytics:summary:{region_id}"
            await self._connection_manager.broadcast_message_model(message, specific_topic=topic)
            logger.debug(f"Broadcasted trend summary to topic {topic}")
            
        return trend_summary

# It would also be good to have methods to:
# - get_historical_data_for_sensor(sensor_id, start_time, end_time)
# - get_current_alerts_for_region(region_id) 