# /content/drive/MyDrive/R1v0.1/backend/app/websocket/__init__.py

import logging

from .connection_manager import ConnectionManager # Ensure this line is correct

logger = logging.getLogger(__name__)
logger.debug("app.websocket package initialized.")

__all__ = ["ConnectionManager"]