# app/services.py (Example)
import logging
from app.services.feed_manager import FeedManager as FMClass # Assuming class is separate
from app.websocket.connection_manager import ConnectionManager # Assuming class is separate
from typing import Optional

logger = logging.getLogger(__name__)

feed_manager_instance: Optional[FMClass] = None
connection_manager_instance: Optional[ConnectionManager] = None # Add connection manager instance

def initialize_services(config: dict):
    global feed_manager_instance, connection_manager_instance
    if connection_manager_instance is None:
         try:
              connection_manager_instance = ConnectionManager()
              logger.info("WebSocket ConnectionManager initialized via app.services.")
         except Exception as e:
               logger.error(f"Failed to initialize ConnectionManager in app.services: {e}")
               connection_manager_instance = None # Ensure it's None on failure
               logger.info(f"WebSocket ConnectionManager initialization: Failed. Reason: {e}")
         else:
              logger.info("WebSocket ConnectionManager initialization: Successful.")


    if feed_manager_instance is None:
        try:
            feed_manager_instance = FMClass(config=config)
            # Inject connection manager if both initialized
            if connection_manager_instance:
                 feed_manager_instance.set_connection_manager(connection_manager_instance)
            logger.info("FeedManager initialized via app.services.")
        except Exception as e:
            logger.error(f"Failed to initialize FeedManager in app.services: {e}", exc_info=True)
            feed_manager_instance = None  
            logger.info(f"FeedManager initialization: Failed. Reason: {e}")
        else:
             logger.info("FeedManager initialization: Successful.")
   

def get_feed_manager() -> FMClass:
    if feed_manager_instance is None: raise RuntimeError("FeedManager not initialized.")
    return feed_manager_instance

def get_connection_manager() -> ConnectionManager:
     if connection_manager_instance is None:
          raise RuntimeError("WebSocket ConnectionManager not initialized.")
     return connection_manager_instance

async def shutdown_services(): # Make async for feed manager shutdown
    global feed_manager_instance, connection_manager_instance

    # 1. Disconnect WebSockets
    if connection_manager_instance:
        try:
            logger.info("Disconnecting all WebSocket connections...")
            await connection_manager_instance.disconnect_all()
            logger.info("Successfully disconnected all WebSocket connections.")
        except Exception as e:
            logger.error(f"Failed to disconnect all websockets: {e}")
    else:
        logger.info("WebSocket ConnectionManager not initialized, skipping disconnect.")

    # 2. Shut down FeedManager
    if feed_manager_instance:
        try:
            logger.info("Requesting FeedManager shutdown from app.services...")
            await feed_manager_instance.shutdown()
            logger.info("FeedManager shutdown completed successfully.")
        except Exception as e:
            logger.error(f"Error during FeedManager shutdown: {e}")
    else:
        logger.info("FeedManager not initialized, skipping shutdown.")

def health_check() -> bool:
     return True # Simple health check