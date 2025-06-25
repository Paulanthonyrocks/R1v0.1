import logging
from typing import Optional, Dict, Any, List
from datetime import datetime

from app.models.traffic import LocationModel
from app.models.signals import SignalControlStatusEnum # Reusing for command response status
from app.models.dms import DmsState, DmsMessage, DmsCommandResponse, DmsStatusEnum
from app.websocket.connection_manager import ConnectionManager # Optional, for future use if DMS changes are broadcast

logger = logging.getLogger(__name__)

# Mock DMS inventory data (can be moved to config later)
MOCK_DMS_INVENTORY_DATA = {
    "DMS_MAIN_ST_NORTH_01": {
        "dms_id": "DMS_MAIN_ST_NORTH_01",
        "location": {"latitude": 34.065, "longitude": -118.24, "name": "DMS Main St North Approach"},
        "operational_status": DmsStatusEnum.ONLINE,
        "capabilities": ["set_custom_message", "clear_message", "max_pages_3"],
        "target_roadway_segment_id": "main_st_seg_01",
        "viewable_directions": ["SB"] # Viewable by Southbound traffic
    },
    "DMS_HWY_101_EAST_05": {
        "dms_id": "DMS_HWY_101_EAST_05",
        "location": {"latitude": 34.052, "longitude": -118.235, "name": "DMS HWY 101 East Approach"},
        "operational_status": DmsStatusEnum.ONLINE,
        "capabilities": ["set_custom_message", "clear_message", "set_predefined_message_by_id"],
        "target_roadway_segment_id": "hwy_101_seg_12",
        "viewable_directions": ["WB"] # Viewable by Westbound traffic
    },
    "DMS_OAK_AVE_WEST_02": {
        "dms_id": "DMS_OAK_AVE_WEST_02",
        "location": {"latitude": 34.051, "longitude": -118.255, "name": "DMS Oak Ave West Approach"},
        "operational_status": DmsStatusEnum.OFFLINE, # Example of an offline DMS
        "capabilities": ["set_custom_message", "clear_message"],
        "target_roadway_segment_id": "oak_ave_seg_03",
        "viewable_directions": ["EB"]
    }
}


class DmsService:
    def __init__(self, config: Dict[str, Any], connection_manager: Optional[ConnectionManager] = None):
        self.config = config.get("dms_service", {})
        self._connection_manager = connection_manager # For potential future WebSocket updates
        self.logger = logger

        self._dms_inventory: Dict[str, DmsState] = {}
        self._initialize_dms_inventory()
        self.logger.info(f"DmsService initialized with {len(self._dms_inventory)} mock DMS units.")

    def _initialize_dms_inventory(self):
        """Loads DMS inventory from mock data or config."""
        # For now, using MOCK_DMS_INVENTORY_DATA.
        # In a real system, this might come from self.config or a database.
        for dms_id, data in MOCK_DMS_INVENTORY_DATA.items():
            loc_data = data["location"]
            self._dms_inventory[dms_id] = DmsState(
                dms_id=data["dms_id"],
                location=LocationModel(**loc_data),
                current_messages=[], # Initially empty
                operational_status=data["operational_status"],
                last_updated=datetime.utcnow(),
                capabilities=data.get("capabilities"),
                target_roadway_segment_id=data.get("target_roadway_segment_id"),
                viewable_directions=data.get("viewable_directions")
            )

    async def get_all_dms_states(self) -> List[DmsState]:
        """Returns the current state of all known DMS signs."""
        self.logger.debug("Fetching all DMS states from mock inventory.")
        # In a real system, this might involve querying hardware or a cache.
        # For mock, just return values from our internal dict.
        return list(self._dms_inventory.values())

    async def set_dms_message(
        self,
        dms_id: str,
        messages: List[DmsMessage],
        duration_minutes: Optional[int] = None # Overall duration for the message sequence
    ) -> DmsCommandResponse:
        """
        Sets a message (potentially multi-page) on a specified DMS.
        Mock implementation updates internal state.
        """
        self.logger.info(f"Attempting to set message on DMS {dms_id}. Message pages: {len(messages)}, Duration: {duration_minutes} mins.")
        if dms_id not in self._dms_inventory:
            self.logger.warning(f"DMS {dms_id} not found in inventory.")
            return DmsCommandResponse(dms_id=dms_id, status=SignalControlStatusEnum.FAILED, message="DMS not found.")

        dms = self._dms_inventory[dms_id]
        if dms.operational_status != DmsStatusEnum.ONLINE:
            self.logger.warning(f"DMS {dms_id} is not ONLINE (status: {dms.operational_status.value}). Cannot set message.")
            return DmsCommandResponse(dms_id=dms_id, status=SignalControlStatusEnum.REJECTED, message=f"DMS not ONLINE (status: {dms.operational_status.value}).")

        # Basic capability check (example)
        if "set_custom_message" not in (dms.capabilities or []):
             self.logger.warning(f"DMS {dms_id} does not support 'set_custom_message' capability.")
             return DmsCommandResponse(dms_id=dms_id, status=SignalControlStatusEnum.NOT_SUPPORTED, message="DMS does not support custom messages.")

        dms.current_messages = messages
        dms.last_updated = datetime.utcnow()
        # In a real system, would interact with DMS hardware/API here.
        # Optionally, store duration if the DMS or system needs to track it for auto-clearing.

        self.logger.info(f"Message successfully set on DMS {dms_id}.")
        return DmsCommandResponse(dms_id=dms_id, status=SignalControlStatusEnum.SUCCESS, message="Message set successfully.")

    async def clear_dms_message(self, dms_id: str) -> DmsCommandResponse:
        """
        Clears the message from a specified DMS.
        Mock implementation updates internal state.
        """
        self.logger.info(f"Attempting to clear message from DMS {dms_id}.")
        if dms_id not in self._dms_inventory:
            self.logger.warning(f"DMS {dms_id} not found in inventory.")
            return DmsCommandResponse(dms_id=dms_id, status=SignalControlStatusEnum.FAILED, message="DMS not found.")

        dms = self._dms_inventory[dms_id]
        if dms.operational_status != DmsStatusEnum.ONLINE:
            # Still allow clearing an offline DMS's logical state in the mock,
            # but the response might indicate the physical unit wouldn't respond.
            self.logger.warning(f"DMS {dms_id} is not ONLINE (status: {dms.operational_status.value}), but attempting to clear its logical state.")

        if "clear_message" not in (dms.capabilities or []):
             self.logger.warning(f"DMS {dms_id} does not support 'clear_message' capability.")
             return DmsCommandResponse(dms_id=dms_id, status=SignalControlStatusEnum.NOT_SUPPORTED, message="DMS does not support clearing messages.")

        dms.current_messages = []
        dms.last_updated = datetime.utcnow()
        # In a real system, would interact with DMS hardware/API here.

        self.logger.info(f"Message successfully cleared from DMS {dms_id}.")
        return DmsCommandResponse(dms_id=dms_id, status=SignalControlStatusEnum.SUCCESS, message="Message cleared successfully.")

    async def get_dms_state(self, dms_id: str) -> Optional[DmsState]:
        """Returns the current state of a specific DMS sign."""
        self.logger.debug(f"Fetching state for DMS {dms_id}.")
        return self._dms_inventory.get(dms_id)

# Example usage (for testing purposes, not part of the service typically)
async def _test_dms_service():
    mock_config = {"dms_service": {}} # Minimal config for this mock
    dms_service = DmsService(config=mock_config)

    all_states = await dms_service.get_all_dms_states()
    logger.info(f"Initial DMS States: {[s.model_dump_json(indent=2) for s in all_states]}")

    test_dms_id = "DMS_MAIN_ST_NORTH_01"
    test_messages = [DmsMessage(text="ACCIDENT AHEAD", page_number=1), DmsMessage(text="EXPECT DELAYS", page_number=2)]

    response = await dms_service.set_dms_message(test_dms_id, test_messages, duration_minutes=30)
    logger.info(f"Set Message Response: {response.model_dump_json()}")

    updated_state = await dms_service.get_dms_state(test_dms_id)
    if updated_state:
        logger.info(f"Updated DMS State for {test_dms_id}: {updated_state.model_dump_json(indent=2)}")

    response_clear = await dms_service.clear_dms_message(test_dms_id)
    logger.info(f"Clear Message Response: {response_clear.model_dump_json()}")

    cleared_state = await dms_service.get_dms_state(test_dms_id)
    if cleared_state:
        logger.info(f"Cleared DMS State for {test_dms_id}: {cleared_state.model_dump_json(indent=2)}")

    offline_dms_id = "DMS_OAK_AVE_WEST_02"
    response_offline = await dms_service.set_dms_message(offline_dms_id, [DmsMessage(text="TEST")])
    logger.info(f"Set Message Response for Offline DMS: {response_offline.model_dump_json()}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    # asyncio.run(_test_dms_service())
    logger.info("DmsService module defined. Example _test_dms_service() function available for manual testing.")
