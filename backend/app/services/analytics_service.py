import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import random

from app.models.traffic import TrafficData, AggregatedTrafficTrend, IncidentReport, IncidentTypeEnum, IncidentSeverityEnum, LocationModel
from app.models.alerts import Alert, AlertSeverityEnum
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum, NewAlertNotification, GeneralNotification
from app.websocket.connection_manager import ConnectionManager
from app.ml.traffic_predictor import TrafficPredictor
from app.ml.data_cache import TrafficDataCache

logger = logging.getLogger(__name__)

class AnalyticsService:
    def __init__(self, config: Dict[str, Any], connection_manager: ConnectionManager):
        self.config = config.get("analytics_service", {})
        self._connection_manager = connection_manager
        
        # Initialize ML components
        self._traffic_predictor = TrafficPredictor(self.config)
        self._data_cache = TrafficDataCache(
            max_history_hours=self.config.get("data_retention_hours", 24)
        )
        
        logger.info("AnalyticsService initialized with ML components.")
        
    def _update_traffic_data(self, data_point: TrafficData):
        """Update both recent data cache and trigger prediction updates"""
        try:
            self._data_cache.add_data_point(
                latitude=data_point.location.latitude,
                longitude=data_point.location.longitude,
                timestamp=data_point.timestamp,
                data={
                    'vehicle_count': data_point.vehicle_count,
                    'average_speed': data_point.speed,
                    'congestion_score': getattr(data_point, 'congestion_score', None)
                }
            )
        except Exception as e:
            logger.error(f"Error updating traffic data cache: {e}")

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
        incidents: List[IncidentReport] = []
        
        for data_point in current_traffic_data:
            # Update data cache for each point
            self._update_traffic_data(data_point)
            
            # Get historical statistics for context
            stats = self._data_cache.get_statistics(
                latitude=data_point.location.latitude,
                longitude=data_point.location.longitude,
                hours=1  # Look at last hour for immediate context
            )
            
            is_anomaly = False
            description = ""
            severity = IncidentSeverityEnum.MEDIUM
            incident_type = IncidentTypeEnum.UNKNOWN

            # Check for anomalies with historical context
            if data_point.vehicle_count is not None:
                avg_count = stats.get('avg_vehicle_count')
                if avg_count and data_point.vehicle_count > avg_count * 1.5:
                    is_anomaly = True
                    description = f"Unusually high vehicle count ({data_point.vehicle_count}, {round((data_point.vehicle_count/avg_count - 1) * 100)}% above average)"
                    severity = IncidentSeverityEnum.HIGH
                    incident_type = IncidentTypeEnum.CONGESTION
            
            if data_point.speed is not None:
                avg_speed = stats.get('avg_speed')
                if avg_speed and data_point.speed < avg_speed * 0.6:
                    description += f" Speed significantly below average ({data_point.speed} km/h, normal ~{round(avg_speed)} km/h)"
                    is_anomaly = True
                    severity = IncidentSeverityEnum.HIGH
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
                await self._broadcast_alert_from_incident(incident)

        return incidents

    async def predict_incident_likelihood(
        self, 
        location: LocationModel, 
        prediction_time: Optional[datetime] = None
    ) -> Dict[str, Any]:
        if prediction_time is None:
            prediction_time = datetime.now(timezone.utc)
            
        # Get recent data for prediction context
        recent_data = self._data_cache.get_recent_data(
            latitude=location.latitude,
            longitude=location.longitude,
            hours=3  # Use last 3 hours for prediction context
        )
        
        # Get prediction from ML model
        prediction = self._traffic_predictor.predict_incident_likelihood(
            recent_traffic_data=recent_data,
            location=location.model_dump(),
            prediction_time=prediction_time
        )
        
        # Get location statistics for additional context
        stats = self._data_cache.get_statistics(
            latitude=location.latitude,
            longitude=location.longitude,
            hours=24  # Use 24-hour history for pattern context
        )
        
        # Enhance prediction with historical context
        prediction.update({
            "historical_context": {
                "congestion_frequency": stats.get('congestion_frequency', 0),
                "typical_vehicle_count": stats.get('avg_vehicle_count'),
                "typical_speed": stats.get('avg_speed')
            }
        })
        
        if self._connection_manager:
            await self._broadcast_prediction(location, prediction)
            
        return prediction
        
    async def _broadcast_prediction(self, location: LocationModel, prediction: Dict[str, Any]):
        """Broadcast prediction results to websocket clients"""
        notification = GeneralNotification(
            message="Traffic Prediction Update",
            details=prediction
        )
        message = WebSocketMessage(
            event_type=WebSocketMessageTypeEnum.PREDICTION_ALERT,
            payload=notification
        )
        topic = f"predictions:{abs(hash((location.latitude, location.longitude)))}"
        await self._connection_manager.broadcast_message_model(message, specific_topic=topic)

    async def get_all_location_congestion_data(self) -> List[Dict[str, Any]]:
        """
        Retrieves the latest congestion data summary for all tracked locations/nodes.
        This data is intended for displaying node-based congestion on the frontend.
        """
        logger.info("Fetching all location congestion data summaries from cache.")

        # Data from TrafficDataCache.get_all_location_summaries() is expected to be a list of dicts,
        # each with 'id', 'name', 'latitude', 'longitude', 'timestamp',
        # 'vehicle_count', 'average_speed', 'congestion_score', etc.
        cached_summaries = self._data_cache.get_all_location_summaries()

        # The structure from get_all_location_summaries is already quite good.
        # We might perform additional transformations here if needed to perfectly match
        # a Pydantic response model, e.g., ensuring all required fields exist or
        # computing derived values. For now, assume it's largely compatible.

        # Example of ensuring required fields or adding defaults if not present in all summaries:
        processed_data = []
        for summary in cached_summaries:
            node_data = {
                'id': summary.get('id', f"{summary.get('latitude',0)},{summary.get('longitude',0)}"), # Fallback ID
                'name': summary.get('name', 'Unknown Node'),
                'latitude': summary.get('latitude'),
                'longitude': summary.get('longitude'),
                'congestion_score': summary.get('congestion_score'), # Might be None
                'vehicle_count': summary.get('vehicle_count'),       # Might be None
                'average_speed': summary.get('average_speed'),       # Might be None
                'timestamp': summary.get('timestamp', datetime.now(timezone.utc).isoformat()) # Ensure timestamp
                # Add other fields from summary if they are part of the defined NodeCongestionData model
            }
            # Filter out entries that couldn't get essential data like lat/lon
            if node_data['latitude'] is not None and node_data['longitude'] is not None:
                processed_data.append(node_data)
            else:
                logger.warning(f"Skipping node summary due to missing lat/lon: {summary.get('id')}")

        logger.info(f"Retrieved {len(processed_data)} node congestion summaries.")
        return processed_data