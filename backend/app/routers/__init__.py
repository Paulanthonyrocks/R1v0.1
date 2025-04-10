# /content/drive/MyDrive/R1v0.1/backend/app/routers/__init__.py

import logging

# Option 1: Make individual router instances accessible (Less common, usually import module)
# from . import feeds, config as config_router, analysis, alerts
# router_feeds = feeds.router
# router_config = config_router.router
# router_analysis = analysis.router
# router_alerts = alerts.router

# Option 2: Just mark as package (most common)
# No explicit imports needed here if you usually do `from app.routers import feeds` in main.py

logger = logging.getLogger(__name__)
logger.debug("app.routers package initialized.")

# Optional: Define __all__ if you want `from app.routers import *` to work
# __all__ = ["feeds", "config_router", "analysis", "alerts"] # Expose modules
# Or if using Option 1 above:
# __all__ = ["router_feeds", "router_config", "router_analysis", "router_alerts"]