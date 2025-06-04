import logging
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
import random
import asyncio
import numpy as np # For calculations in KPI summary
from typing import List, Optional, Dict, Any, Union # Added Union
from datetime import datetime, timedelta, timezone
import random

from app.models.traffic import TrafficData, AggregatedTrafficTrend, IncidentReport, IncidentTypeEnum, IncidentSeverityEnum, LocationModel
from app.models.alerts import Alert, AlertSeverityEnum # Keep AlertSeverityEnum
# Updated import for websocket models
from app.models.websocket import (
    WebSocketMessage,
    WebSocketMessageTypeEnum,
    NewAlertNotification,
    GeneralNotification,
    NodeCongestionUpdatePayload, # Added
    NodeCongestionUpdateData, # Added, though AnalyticsService returns dicts that Pydantic converts
    UserSpecificConditionAlert # Updated model name
)
from app.websocket.connection_manager import ConnectionManager
from app.ml.traffic_predictor import TrafficPredictor
from app.ml.data_cache import TrafficDataCache
from app.utils.utils import DatabaseManager # Added DatabaseManager import

# SQLAlchemy imports for PredictionLogModel
import uuid as uuid_pkg # Renamed to avoid conflict with column name
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, String, DateTime, JSON, Float, Boolean, Integer, func, select # Added func, select
from sqlalchemy.orm import Session # For type hinting if needed, though DatabaseManager might abstract it


logger = logging.getLogger(__name__)

# Define a Base for the PredictionLogModel.
# Ideally, this Base should be shared across the application or managed by DatabaseManager.
# For this subtask, we define it here.
PredictionLogBase = declarative_base()

class PredictionLogModel(PredictionLogBase):
    __tablename__ = 'prediction_log'

    id = Column(String, primary_key=True, default=lambda: str(uuid_pkg.uuid4()))
    prediction_made_at = Column(DateTime, default=datetime.utcnow)
    location_name = Column(String, nullable=True)
    location_latitude = Column(Float, nullable=False)
    location_longitude = Column(Float, nullable=False)
    predicted_event_start_time = Column(DateTime, nullable=False)
    predicted_event_end_time = Column(DateTime, nullable=False)
    prediction_type = Column(String, nullable=False)  # e.g., "incident_likelihood", "congestion_spike"
    predicted_value = Column(JSON, nullable=False)    # e.g., {"likelihood_score_percent": 75, "details": "..."}
    source_of_prediction = Column(String, nullable=False) # e.g., "PredictionScheduler_HighLikelihood"
    kpi_snapshot_at_prediction = Column(JSON, nullable=True)
    outcome_verified = Column(Boolean, default=False, index=True)
    actual_outcome_type = Column(String, nullable=True) # e.g., "incident_occurred", "no_event"
    actual_outcome_details = Column(JSON, nullable=True)
    outcome_verified_at = Column(DateTime, nullable=True)


class AnalyticsService:
    def __init__(self, config: Dict[str, Any], connection_manager: ConnectionManager, database_manager: DatabaseManager): # Added database_manager
        self.config = config.get("analytics_service", {})
        self._connection_manager = connection_manager
        self._db_manager = database_manager # Store DatabaseManager instance

        # Attempt to create the PredictionLogModel table if engine is accessible
        # This is a temporary measure. Ideally, table creation is handled by Alembic migrations or a central DB setup.
        try:
            if hasattr(self._db_manager, 'engine') and self._db_manager.engine is not None:
                PredictionLogModel.__table__.create(self._db_manager.engine, checkfirst=True)
                logger.info("PredictionLogModel table checked/created successfully.")
            else:
                logger.warning("Database engine not directly accessible via DatabaseManager. "
                               "PredictionLogModel table creation will need to be handled externally (e.g., by Alembic or main app setup).")
        except Exception as e:
            logger.error(f"Error attempting to create PredictionLogModel table: {e}. "
                         "Table creation may need to be handled externally.")

        # Initialize ML components
        self._traffic_predictor = TrafficPredictor(self.config)
        self._data_cache = TrafficDataCache(
            max_history_hours=self.config.get("data_retention_hours", 24)
        )
        
        # For periodic node congestion broadcasts
        self._node_congestion_broadcast_interval_seconds = self.config.get("node_congestion_broadcast_interval", 10)
        self._node_congestion_task: Optional[asyncio.Task] = None
        self._stop_node_congestion_event: Optional[asyncio.Event] = None

        # For periodic prediction correlation
        self._prediction_correlation_interval_seconds = self.config.get("prediction_correlation_interval_seconds", 300) # Default 5 minutes
        self._prediction_correlation_task: Optional[asyncio.Task] = None
        self._stop_prediction_correlation_event: Optional[asyncio.Event] = None

        logger.info("AnalyticsService initialized with ML components, DatabaseManager, and background task setup.")
        
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

    async def _broadcast_node_congestion_updates(self):
        """
        Fetches current node congestion data and broadcasts it via WebSocket.
        """
        if not self._connection_manager:
            logger.debug("Skipping node congestion broadcast: ConnectionManager not available.")
            return

        try:
            node_data_list = await self.get_all_location_congestion_data()
            if not node_data_list:
                logger.debug("No node congestion data available to broadcast.")
                return

            # Convert list of dicts to list of NodeCongestionUpdateData if strict typing is needed for payload
            # However, Pydantic will validate on assignment to NodeCongestionUpdatePayload.
            # node_updates = [NodeCongestionUpdateData(**data) for data in node_data_list]
            # For now, assuming node_data_list structure is compatible.

            payload = NodeCongestionUpdatePayload(nodes=node_data_list)
            message = WebSocketMessage(
                event_type=WebSocketMessageTypeEnum.NODE_CONGESTION_UPDATE,
                payload=payload
            )

            await self._connection_manager.broadcast_message_model(message, specific_topic="node_congestion")
            logger.info(f"Broadcasted node congestion update for {len(node_data_list)} nodes.")

        except Exception as e:
            logger.error(f"Error during node congestion broadcast: {e}", exc_info=True)

    async def _run_node_congestion_broadcast_loop(self):
        """
        Periodically calls _broadcast_node_congestion_updates.
        """
        if not self._stop_node_congestion_event: # Should be set before starting
            logger.error("Stop event for node congestion broadcast loop not initialized!")
            return

        logger.info("Node congestion broadcast loop started.")
        while not self._stop_node_congestion_event.is_set():
            try:
                await self._broadcast_node_congestion_updates()
                await asyncio.sleep(self._node_congestion_broadcast_interval_seconds)
            except asyncio.CancelledError:
                logger.info("Node congestion broadcast loop was cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in node congestion broadcast loop: {e}", exc_info=True)
                # Avoid rapid-fire error loops by sleeping even on error
                await asyncio.sleep(self._node_congestion_broadcast_interval_seconds)
        logger.info("Node congestion broadcast loop stopped.")

    async def start_background_tasks(self):
        """
        Starts background tasks for the AnalyticsService.
        """
        if self._node_congestion_task is None or self._node_congestion_task.done():
            self._stop_node_congestion_event = asyncio.Event()
            self._node_congestion_task = asyncio.create_task(self._run_node_congestion_broadcast_loop())
            logger.info("AnalyticsService: Node congestion broadcast task started.")
        else:
            logger.info("AnalyticsService: Node congestion broadcast task already running.")

        if self._prediction_correlation_task is None or self._prediction_correlation_task.done():
            self._stop_prediction_correlation_event = asyncio.Event()
            self._prediction_correlation_task = asyncio.create_task(self._run_prediction_correlation_loop())
            logger.info("AnalyticsService: Prediction correlation task started.")
        else:
            logger.info("AnalyticsService: Prediction correlation task already running.")

    async def stop_background_tasks(self):
        """
        Stops background tasks for the AnalyticsService.
        """
        if self._node_congestion_task and not self._node_congestion_task.done():
            logger.info("AnalyticsService: Stopping node congestion broadcast task...")
            if self._stop_node_congestion_event:
                self._stop_node_congestion_event.set()
            try:
                await asyncio.wait_for(self._node_congestion_task, timeout=5.0)
                logger.info("AnalyticsService: Node congestion broadcast task stopped gracefully.")
            except asyncio.TimeoutError:
                logger.warning("AnalyticsService: Node congestion broadcast task did not stop within timeout. Cancelling.")
                self._node_congestion_task.cancel()
                try:
                    await self._node_congestion_task
                except asyncio.CancelledError:
                    logger.info("AnalyticsService: Node congestion broadcast task cancelled.")
            except Exception as e:
                logger.error(f"AnalyticsService: Error stopping node congestion task: {e}", exc_info=True)
            self._node_congestion_task = None
            self._stop_node_congestion_event = None # Clear event for potential restart
        else:
            logger.info("AnalyticsService: Node congestion broadcast task not running or already stopped.")

        if self._prediction_correlation_task and not self._prediction_correlation_task.done():
            logger.info("AnalyticsService: Stopping prediction correlation task...")
            if self._stop_prediction_correlation_event:
                self._stop_prediction_correlation_event.set()
            try:
                await asyncio.wait_for(self._prediction_correlation_task, timeout=5.0)
                logger.info("AnalyticsService: Prediction correlation task stopped gracefully.")
            except asyncio.TimeoutError:
                logger.warning("AnalyticsService: Prediction correlation task did not stop within timeout. Cancelling.")
                self._prediction_correlation_task.cancel()
                try:
                    await self._prediction_correlation_task
                except asyncio.CancelledError:
                    logger.info("AnalyticsService: Prediction correlation task cancelled.")
            except Exception as e:
                logger.error(f"AnalyticsService: Error stopping prediction correlation task: {e}", exc_info=True)
            self._prediction_correlation_task = None
            self._stop_prediction_correlation_event = None
        else:
            logger.info("AnalyticsService: Prediction correlation task not running or already stopped.")

    def get_current_system_kpis_summary(self) -> Dict[str, Any]:
        """
        Returns a synchronous summary of system-wide KPIs based on cached data.
        """
        logger.debug("AnalyticsService: Generating current system KPI summary from data cache.")
        summaries = self._data_cache.get_all_location_summaries()

        if not summaries:
            return {
                "overall_congestion_level": "UNKNOWN",
                "average_speed_kmh": 0.0,
                "total_vehicle_flow_estimate": 0, # Represents current snapshot count, not rate yet
                "active_monitored_locations": 0,
                "system_stability_indicator": "NO_DATA"
            }

        all_congestion_scores = [s['congestion_score'] for s in summaries if s.get('congestion_score') is not None]
        all_speeds = [s['average_speed'] for s in summaries if s.get('average_speed') is not None]
        total_vehicles_snapshot = sum(s.get('vehicle_count', 0) for s in summaries if s.get('vehicle_count') is not None)

        avg_congestion_score = np.mean(all_congestion_scores) if all_congestion_scores else 0.0
        avg_speed = np.mean(all_speeds) if all_speeds else 0.0

        congestion_str = "UNKNOWN"
        if avg_congestion_score < 30:
            congestion_str = "LOW"
        elif avg_congestion_score <= 70:
            congestion_str = "MEDIUM"
        else:
            congestion_str = "HIGH"

        return {
            "overall_congestion_level": congestion_str,
            "average_speed_kmh": round(avg_speed, 1),
            "total_vehicle_flow_estimate": total_vehicles_snapshot, # This is a sum of current counts, not a rate yet
            "active_monitored_locations": len(summaries),
            "system_stability_indicator": "STABLE" # Placeholder, could be based on error rates etc.
        }

    async def get_critical_alert_summary(self) -> Dict[str, Any]: # Made async
        """
        Returns a summary of critical and high-severity, unacknowledged alerts from the database.
        """
        logger.debug("AnalyticsService: Fetching critical alert summary from database.")
        filters = {
            "severity_in": [AlertSeverityEnum.CRITICAL.value, AlertSeverityEnum.ERROR.value], # Assuming ERROR maps to high severity
            "acknowledged": False
        }

        try:
            # These calls are now to async methods in DatabaseManager
            critical_alerts_count = await self._db_manager.count_alerts_filtered(filters=filters)
            recent_critical_alerts_data = await self._db_manager.get_alerts_filtered(filters=filters, limit=3, offset=0)

            # Extract messages (as types) and potentially locations if available in 'details'
            recent_critical_types = []
            for alert_dict in recent_critical_alerts_data:
                # Assuming alert_dict is a dict from DB, not Pydantic model yet
                msg_summary = alert_dict.get('message', 'Unknown Type')[:50] # Truncate for summary
                if alert_dict.get('details') and isinstance(alert_dict['details'], str):
                    try:
                        details_dict = json.loads(alert_dict['details'])
                        if details_dict.get('incident_type'):
                            msg_summary = f"{details_dict['incident_type']}: {msg_summary}"
                    except json.JSONDecodeError:
                        pass # Keep msg_summary as is
                recent_critical_types.append(msg_summary)

            # Find oldest unresolved critical alert (optional, could be intensive)
            # For now, returning placeholder for this
            oldest_unresolved_age_hours = None

            return {
                "critical_unack_alert_count": critical_alerts_count,
                "recent_critical_types": recent_critical_types,
                "oldest_unresolved_critical_alert_age_hours": oldest_unresolved_age_hours
            }
        except Exception as e:
            logger.error(f"Error fetching critical alert summary: {e}", exc_info=True)
            return { # Return default/error structure
                "critical_unack_alert_count": 0,
                "recent_critical_types": [],
                "oldest_unresolved_critical_alert_age_hours": None,
                "error": str(e)
            }

    async def broadcast_operational_alert(
        self,
        title: str,
        message_text: str, # Renamed from 'message' to avoid conflict with WebSocketMessage model
        severity: str, # e.g., "info", "warning", "error"
        suggested_actions: Optional[List[str]] = None
    ):
        """
        Broadcasts an operational alert generated by the AgentCore or other system logic.
        Uses GeneralNotification payload type.
        """
        if not self._connection_manager:
            logger.warning("ConnectionManager not available in AnalyticsService. Cannot broadcast operational alert.")
            return

        # Validate severity if possible (e.g., against an enum or list of allowed values)
        # For now, assume severity is a string like "info", "warning", "error"

        payload = GeneralNotification(
            message_type="operational_alert_by_agent", # Custom type for client-side differentiation
            title=title,
            message=message_text,
            severity=severity.lower(), # Ensure lowercase if frontend expects it
            suggested_actions=suggested_actions # New field
        )
        ws_message = WebSocketMessage(
            event_type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION, # Use existing enum for general purpose notifications
            payload=payload
        )
        try:
            await self._connection_manager.broadcast_message_model(ws_message, specific_topic="operational_alerts")
            logger.info(f"Broadcasted operational alert: '{title}' - Severity: {severity}")
        except Exception as e:
            logger.error(f"Failed to broadcast operational alert: {e}", exc_info=True)

    async def record_prediction_log(self, log_entry_data: Dict[str, Any]) -> Optional[str]:
        """
        Records a prediction event into the PredictionLogModel table.
        """
        try:
            # Convert dict to PredictionLogModel instance
            # Ensure all required fields are present in log_entry_data or have defaults
            log_entry = PredictionLogModel(**log_entry_data)

            # Use DatabaseManager to add the object
            # Assuming DatabaseManager has an async method `add_object` or similar
            # and handles session management (begin, commit, rollback, close).
            # If not, direct session usage would be needed:
            # session = self._db_manager.get_session()
            # try:
            #     session.add(log_entry)
            #     await session.commit() # if db_manager uses async sessions
            #     await session.refresh(log_entry) # to get ID if generated by DB
            #     return log_entry.id
            # except Exception as e:
            #     await session.rollback()
            #     logger.error(f"Error recording prediction log: {e}", exc_info=True)
            #     return None
            # finally:
            #     await session.close()

            # Simpler approach if db_manager.add_object handles all session logic:
            # We need to know if add_object returns the committed object or its ID,
            # and if it handles async operations correctly.
            # For now, assuming add_object can take the model instance.
            # If it's synchronous, it needs to be run in a thread pool.
            # Let's assume there's an async version or it's safe to call directly.

            # If add_object is synchronous:
            # await asyncio.to_thread(self._db_manager.add_object, log_entry)
            # The ID might not be available immediately if not refreshed and db_manager does not do it.

            # For now, let's assume a synchronous add_object that commits and potentially refreshes,
            # and run it in a thread_pool. This is a common pattern if the DB manager is sync.
            # If DatabaseManager is fully async, this would be simpler.

            # Given the existing DatabaseManager methods like count_alerts_filtered are async,
            # it's plausible add_object or a similar method for saving is also async.
            # Let's assume an `async_add_object` exists or `add_object` is awaitable
            # and it handles commit and refresh to get the ID.

            # Modification: Directly use session from db_manager if possible, assuming it provides one.
            # This gives more control over the async commit and refresh.
            async with self._db_manager.get_session() as session: # Assuming get_session is an async context manager
                try:
                    session.add(log_entry)
                    await session.commit()
                    await session.refresh(log_entry) # Ensure the ID is populated
                    logger.info(f"Prediction log recorded successfully. ID: {log_entry.id}")
                    return log_entry.id
                except Exception as e:
                    await session.rollback()
                    logger.error(f"Error recording prediction log in session: {e}", exc_info=True)
                    return None

        except Exception as e:
            # This outer try-except catches errors during model instantiation (e.g. bad data)
            # or if get_session itself fails.
            logger.error(f"Failed to prepare or record prediction log: {e}", exc_info=True)
            return None

    async def _fetch_relevant_incidents(
        self,
        prediction_log_entry: PredictionLogModel,
        search_start_time: datetime,
        search_end_time: datetime,
        vicinity_radius_km: float = 1.0
    ) -> List[IncidentReport]: # Assuming IncidentReport is the Pydantic model from app.models.traffic
        """
        Placeholder method to fetch incidents relevant to a prediction.
        This should be implemented using DatabaseManager to query the incident table.
        """
        logger.info(f"Attempting to fetch incidents for prediction {prediction_log_entry.id} "
                    f"between {search_start_time} and {search_end_time} "
                    f"within {vicinity_radius_km}km of "
                    f"({prediction_log_entry.location_latitude}, {prediction_log_entry.location_longitude}).")

        # Mock implementation: Returns an empty list.
        # TODO: Replace with actual DatabaseManager call, e.g.:
        # incidents_data = await self._db_manager.get_incidents_in_vicinity_timeframe(
        #     latitude=prediction_log_entry.location_latitude,
        #     longitude=prediction_log_entry.location_longitude,
        #     start_time=search_start_time,
        #     end_time=search_end_time,
        #     vicinity_radius_km=vicinity_radius_km
        # )
        # return [IncidentReport(**data) for data in incidents_data] # Assuming db_manager returns list of dicts

        # For now, returning an empty list to simulate no incidents found.
        # To test with found incidents, you could return a mock list:
        # mock_incident_location = LocationModel(
        #     latitude=prediction_log_entry.location_latitude,
        #     longitude=prediction_log_entry.location_longitude,
        #     name=prediction_log_entry.location_name
        # )
        # return [
        #     IncidentReport(
        #         id=str(uuid_pkg.uuid4()),
        #         timestamp=prediction_log_entry.predicted_event_start_time + timedelta(minutes=10), # within window
        #         type=IncidentTypeEnum.ACCIDENT,
        #         severity=IncidentSeverityEnum.MEDIUM,
        #         location=mock_incident_location,
        #         description="Mocked accident for testing correlation."
        #     )
        # ]
        return []

    async def correlate_predictions_with_outcomes(
        self,
        correlation_window_hours: int = 2,
        lookback_hours: int = 24
    ):
        """
        Correlates unverified predictions with actual outcomes (incidents).
        - correlation_window_hours: Total window width (e.g., 2 means +/-1 hour around prediction time)
        - lookback_hours: How far back to check for unverified predictions.
        """
        processed_count = 0
        updated_count = 0

        time_threshold = datetime.utcnow() - timedelta(hours=lookback_hours)

        logger.info(f"Starting prediction outcome correlation. Lookback: {lookback_hours}hrs, "
                    f"Correlation window: +/- {correlation_window_hours/2}hrs.")

        async with self._db_manager.get_session() as session:
            try:
                unverified_predictions = await session.execute(
                    PredictionLogModel.__table__.select().where(
                        PredictionLogModel.outcome_verified == False,
                        PredictionLogModel.prediction_made_at >= time_threshold
                    )
                )
                # SQLAlchemy 2.0 style for fetching results
                # For older versions, it might be: session.query(PredictionLogModel).filter(...).all()
                # predictions_to_check = [PredictionLogModel(**row._asdict()) for row in unverified_predictions.fetchall()]

                # Simpler if using ORM query directly (assuming PredictionLogModel is mapped to this session's metadata)
                # This part depends on how PredictionLogBase and self._db_manager.get_session() are integrated.
                # If they are not integrated, a raw query as above is okay, but mapping to model instances is manual.
                # Let's assume for now that the PredictionLogModel is usable with the session from db_manager.
                # If not, the select() and manual mapping is an alternative.

                # Using SQLAlchemy ORM query style (preferred if PredictionLogModel is known to the session)
                from sqlalchemy import select # Explicit import for clarity
                stmt = (
                    select(PredictionLogModel)
                    .where(PredictionLogModel.outcome_verified == False)
                    .where(PredictionLogModel.prediction_made_at >= time_threshold)
                )
                result = await session.execute(stmt)
                predictions_to_check = result.scalars().all()

                processed_count = len(predictions_to_check)
                logger.info(f"Found {processed_count} unverified predictions to correlate.")

                for pred_log in predictions_to_check:
                    search_start_time = pred_log.predicted_event_start_time - timedelta(hours=correlation_window_hours / 2)
                    # The predicted_event_end_time is the end of the *prediction* window, not just the start.
                    # So, we should search around the entire predicted event window.
                    # If predicted_event_end_time is just predicted_start + fixed_duration, this is fine.
                    # If predicted_event_end_time itself defines a window, then adjust:
                    search_end_time = pred_log.predicted_event_end_time + timedelta(hours=correlation_window_hours / 2)

                    # Fetch relevant incidents (using the placeholder for now)
                    # The vicinity_radius_km could also be configurable or per-prediction type.
                    found_incidents = await self._fetch_relevant_incidents(
                        pred_log, search_start_time, search_end_time, vicinity_radius_km=1.0
                    )

                    if found_incidents:
                        pred_log.outcome_verified = True
                        pred_log.actual_outcome_type = "incident_occurred" # Could be refined based on incident types
                        # Store salient details. Ensure it's JSON serializable.
                        pred_log.actual_outcome_details = {
                            "incidents": [inc.model_dump(exclude_none=True) for inc in found_incidents]
                        }
                        pred_log.outcome_verified_at = datetime.utcnow()
                        logger.info(f"Prediction {pred_log.id} correlated: Incident(s) occurred.")
                        updated_count += 1
                    else:
                        # Only mark as 'no_event_detected' if the prediction window + correlation window has fully passed.
                        # Otherwise, it's still too early to tell for sure.
                        if datetime.utcnow() > search_end_time: # The correlation window has passed
                            pred_log.outcome_verified = True
                            pred_log.actual_outcome_type = "no_event_detected"
                            pred_log.outcome_verified_at = datetime.utcnow()
                            logger.info(f"Prediction {pred_log.id} correlated: No event detected within window.")
                            updated_count += 1
                        else:
                            logger.info(f"Prediction {pred_log.id}: Still within correlation window, outcome pending.")

                    # The changes to pred_log are tracked by the session.
                    # A single commit at the end of the loop, or per item?
                    # Per item is safer for atomicity of each update but slower.
                    # Batching commits might be better for performance if many entries.
                    # For now, let's commit each one.
                    await session.commit()

            except Exception as e:
                logger.error(f"Error during prediction correlation: {e}", exc_info=True)
                await session.rollback() # Rollback any partial changes in this batch
            finally:
                logger.info(f"Prediction correlation finished. Processed: {processed_count}, Updated: {updated_count}.")

        # Note: A mechanism to periodically call this method is needed (e.g., APScheduler job).

    async def _run_prediction_correlation_loop(self):
        """
        Periodically calls correlate_predictions_with_outcomes.
        """
        if not self._stop_prediction_correlation_event:
            logger.error("Stop event for prediction correlation loop not initialized!")
            return

        logger.info("Prediction correlation loop started.")
        while not self._stop_prediction_correlation_event.is_set():
            try:
                logger.info("Running prediction correlation task...")
                await self.correlate_predictions_with_outcomes()
                logger.info("Prediction correlation task finished run.")

                # Wait for the next interval or until stop event is set
                if self._stop_prediction_correlation_event.is_set():
                    break
                await asyncio.wait_for(
                    self._stop_prediction_correlation_event.wait(),
                    timeout=self._prediction_correlation_interval_seconds
                )
            except asyncio.TimeoutError: # This means sleep completed without stop event
                continue
            except asyncio.CancelledError:
                logger.info("Prediction correlation loop was cancelled.")
                break
            except Exception as e:
                logger.error(f"Error in prediction correlation loop: {e}", exc_info=True)
                # Avoid rapid-fire error loops by sleeping for a shorter, fixed duration on unexpected error
                try:
                    await asyncio.wait_for(self._stop_prediction_correlation_event.wait(), timeout=60)
                except asyncio.TimeoutError:
                    continue # Continue to next iteration after short delay
                except asyncio.CancelledError: # If cancelled during error sleep
                    break
        logger.info("Prediction correlation loop stopped.")

    async def get_prediction_outcome_summary(
        self,
        location_latitude: Optional[float] = None,
        location_longitude: Optional[float] = None,
        time_since: Optional[datetime] = None, # Filter for predictions made after this time
        source_of_prediction: Optional[str] = None,
        location_radius_km: float = 0.5 # Radius for lat/lon query
    ) -> Dict[str, Any]:
        """
        Retrieves a summary of prediction outcomes based on specified filters.
        Latitude/Longitude queries will use a small radius for matching.
        """
        query_filters_dict = {
            "location_latitude": location_latitude,
            "location_longitude": location_longitude,
            "location_radius_km": location_radius_km if location_latitude is not None and location_longitude is not None else None,
            "time_since": time_since.isoformat() if time_since else None,
            "source_of_prediction": source_of_prediction
        }

        base_query_filters = [PredictionLogModel.outcome_verified == True]
        if time_since:
            base_query_filters.append(PredictionLogModel.prediction_made_at >= time_since)
        if source_of_prediction:
            base_query_filters.append(PredictionLogModel.source_of_prediction == source_of_prediction)

        # Location filtering (approximate match within a radius)
        # This requires a spatial query capability. If using PostGIS, ST_DWithin would be used.
        # For simplicity here, if lat/lon are provided, we'll filter by a small bounding box.
        # A proper spatial index and query would be much more efficient.
        if location_latitude is not None and location_longitude is not None:
            # Approximate radius to degree conversion (very rough, varies by latitude)
            # 1 degree latitude ~ 111 km. 1 degree longitude ~ 111 km * cos(latitude)
            lat_delta = location_radius_km / 111.0
            lon_delta = location_radius_km / (111.0 * abs(np.cos(np.radians(location_latitude))) if location_latitude != 0 else 111.0) # Avoid division by zero at poles, though unlikely for traffic

            base_query_filters.append(PredictionLogModel.location_latitude >= location_latitude - lat_delta)
            base_query_filters.append(PredictionLogModel.location_latitude <= location_latitude + lat_delta)
            base_query_filters.append(PredictionLogModel.location_longitude >= location_longitude - lon_delta)
            base_query_filters.append(PredictionLogModel.location_longitude <= location_longitude + lon_delta)

        results = {
            "query_filters": query_filters_dict,
            "total_verified_predictions": 0,
            "outcomes": {},
            "accuracy_metrics": {}
        }

        async with self._db_manager.get_session() as session:
            try:
                # Total verified predictions matching filters
                total_stmt = select(func.count(PredictionLogModel.id)).where(*base_query_filters)
                total_verified_result = await session.execute(total_stmt)
                total_verified = total_verified_result.scalar_one_or_none() or 0
                results["total_verified_predictions"] = total_verified

                if total_verified == 0:
                    results["accuracy_metrics"]["incident_hit_rate"] = 0.0
                    return results # No verified predictions matching criteria

                # Count outcomes
                outcome_types_stmt = (
                    select(PredictionLogModel.actual_outcome_type, func.count(PredictionLogModel.id).label("count"))
                    .where(*base_query_filters)
                    .group_by(PredictionLogModel.actual_outcome_type)
                )
                outcome_counts_result = await session.execute(outcome_types_stmt)

                for row in outcome_counts_result.mappings().all(): # .mappings() provides dict-like rows
                    results["outcomes"][row["actual_outcome_type"]] = row["count"]

                # Calculate accuracy (example: incident_hit_rate)
                # This defines "hit" as a prediction that resulted in "incident_occurred".
                # Other definitions of accuracy might be relevant depending on use case.
                incident_occurred_count = results["outcomes"].get("incident_occurred", 0)

                if total_verified > 0:
                    results["accuracy_metrics"]["incident_hit_rate"] = round(incident_occurred_count / total_verified, 3)
                else:
                    results["accuracy_metrics"]["incident_hit_rate"] = 0.0

                # Placeholder for average likelihood scores (more complex query)
                # Example: avg likelihood for 'incident_occurred' vs 'no_event_detected'
                # This would require parsing JSON `predicted_value` which is DB specific or done in Python.
                results["accuracy_metrics"]["average_likelihood_scores"] = "not_implemented"

            except Exception as e:
                logger.error(f"Error getting prediction outcome summary: {e}", exc_info=True)
                # Return partial results or an error structure
                results["error"] = str(e)

        return results

    async def send_user_specific_alert( # Renamed method
        self,
        user_id: str,
        notification_model: UserSpecificConditionAlert # Updated model type
    ):
        """
        Sends a user-specific alert to all active WebSocket connections
        associated with the given user_id.
        """
        if not self._connection_manager:
            logger.warning("ConnectionManager not available in AnalyticsService. Cannot send user-specific notification.")
            return

        if not user_id or not notification_model:
            logger.warning("User ID or notification model not provided. Cannot send user-specific notification.")
            return

        # Ensure the notification_model's user_id matches the target user_id for consistency
        if notification_model.user_id != user_id:
            logger.warning(f"Mismatch between target user_id ('{user_id}') and notification_model.user_id ('{notification_model.user_id}'). "
                           "Sending based on target user_id.")
            # Optionally, update notification_model.user_id = user_id here if desired,
            # or reject if strict matching is required. For now, proceed with target user_id.

        ws_message = WebSocketMessage(
            event_type=WebSocketMessageTypeEnum.USER_SPECIFIC_ALERT, # Updated enum
            payload=notification_model
            # client_id is not set here, as send_personal_message_model targets a specific client_id
        )

        sent_to_clients_count = 0
        # Iterate over a copy of values if modification during iteration is a concern,
        # but here we are just reading user_info and calling a send method.
        # Ensure active_connections is thread-safe if ConnectionManager is accessed by multiple coroutines
        # For this example, assuming direct iteration is fine.
        active_connections_list = list(self._connection_manager.active_connections.values())


        for connection in active_connections_list:
            # Assuming connection.user_info is a dict like {'uid': 'user_id_value', ...}
            # This was based on a hypothetical structure. If user_id is directly on connection, adjust.
            # For now, let's assume user_id is stored in connection.client_id directly for simplicity,
            # or that ConnectionManager has a way to map user_id to client_id(s).
            # The current loop structure implies user_info.get('uid') was the intended way.
            if hasattr(connection, 'user_info') and connection.user_info and connection.user_info.get('uid') == user_id:
                try:
                    # Assumes send_personal_message_model can handle the Pydantic model directly
                    await self._connection_manager.send_personal_message_model(connection.client_id, ws_message)
                    logger.info(f"Sent user-specific alert (type: {notification_model.alert_type}) " # Use .alert_type
                                f"to client {connection.client_id} for user {user_id}.")
                    sent_to_clients_count += 1
                except Exception as e:
                    logger.error(f"Error sending user-specific alert to client {connection.client_id} for user {user_id}: {e}",
                                 exc_info=True)
            # Fallback or alternative: if user_id is directly the client_id for the connection
            elif connection.client_id == user_id and (not hasattr(connection, 'user_info') or not connection.user_info):
                 try:
                    await self._connection_manager.send_personal_message_model(connection.client_id, ws_message)
                    logger.info(f"Sent user-specific alert (type: {notification_model.alert_type}) "
                                f"to client {connection.client_id} (matched directly) for user {user_id}.")
                    sent_to_clients_count += 1
                 except Exception as e:
                    logger.error(f"Error sending user-specific alert to client {connection.client_id} (direct match) for user {user_id}: {e}",
                                 exc_info=True)

        if sent_to_clients_count > 0:
            logger.info(f"User-specific alert (type: {notification_model.alert_type}) for user {user_id} " # Use .alert_type
                        f"was dispatched to {sent_to_clients_count} client(s).")
        else:
            logger.info(f"No active clients found for user {user_id} to send alert (type: {notification_model.alert_type}).") # Use .alert_type