import asyncio
import random
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List
from app.services.analytics_service import AnalyticsService
from app.services.traffic_signal_service import TrafficSignalService
from app.models.signals import SignalPhaseEnum

logger = logging.getLogger("app.services.simulation_service")

class SimulationService:
    def __init__(
        self, 
        analytics_service: AnalyticsService,
        traffic_signal_service: TrafficSignalService,
        interval: float = 2.0
    ):
        self.analytics_service = analytics_service
        self.traffic_signal_service = traffic_signal_service
        self.interval = interval
        self._task: asyncio.Task | None = None
        self._nodes: List[Dict[str, Any]] = []
        self._initialize_grid()

    def _initialize_grid(self):
        """Create a 5x5 grid of nodes similar to RouteOptimizer"""
        for i in range(5):
            for j in range(5):
                node_id = f"{i},{j}"
                lat = 34.0 + i * 0.01
                lon = -118.0 + j * 0.01
                
                # Assign signals to some nodes
                signal_id = None
                if i == 1 and j == 1: signal_id = "sig_101"
                if i == 3 and j == 3: signal_id = "sig_102"
                
                self._nodes.append({
                    "id": node_id,
                    "latitude": lat,
                    "longitude": lon,
                    "base_vehicle_count": random.randint(10, 50),
                    "congestion_score": 0.2,
                    "signal_id": signal_id
                })
        logger.info(f"Simulation grid initialized with {len(self._nodes)} nodes.")

    async def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
            logger.info("Simulation service started.")

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
            logger.info("Simulation service stopped.")

    async def _run(self):
        while True:
            try:
                await self._update_simulation()
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in simulation loop: {e}", exc_info=True)
                await asyncio.sleep(self.interval)

    async def _update_simulation(self):
        """Update congestion scores based on random fluctuations and signal states"""
        now = datetime.now(timezone.utc)
        
        for node in self._nodes:
            # Random fluctuation
            node["congestion_score"] = max(0, min(1.0, node["congestion_score"] + random.uniform(-0.1, 0.1)))
            
            # If node has a signal, simulate impact of signal on congestion
            if node["signal_id"]:
                signal_state = await self.traffic_signal_service.get_signal_state(node["signal_id"])
                if signal_state:
                    # If Red, congestion increases slightly
                    if "RED" in signal_state.current_phase.value:
                        node["congestion_score"] = min(1.0, node["congestion_score"] + 0.05)
                    # If Green, congestion decreases slightly
                    elif "GREEN" in signal_state.current_phase.value:
                        node["congestion_score"] = max(0, node["congestion_score"] - 0.05)
            
            # Simple vehicle count based on congestion
            vehicle_count = int(node["base_vehicle_count"] * (1 + node["congestion_score"]))
            
            # Mock average speed (inversely proportional to congestion)
            avg_speed = 60.0 * (1 - node["congestion_score"] * 0.8)
            
            # Add to analytics cache (which broadcasts via AnalyticsService later)
            metrics = {
                "id": node["id"],
                "vehicle_count": vehicle_count,
                "average_speed": avg_speed,
                "congestion_score": node["congestion_score"],
                "latitude": node["latitude"],
                "longitude": node["longitude"],
                "signal_id": node["signal_id"],
                "timestamp": now
            }
            
            self.analytics_service._data_cache.add_data_point(
                node["latitude"], node["longitude"], now, metrics
            )
            
        logger.debug("Simulation step completed.")
