# backend/app/__init__.py
# This file marks the 'app' directory as a Python package.
import logging
import sys

# Optional: Configure root logger minimally here if needed before config load
# This helps capture early import errors or setup issues.
# However, main.py also sets up basicConfig, so duplication might occur.
# logging.basicConfig(level=logging.INFO, stream=sys.stdout)

# You can also define package-level variables or perform initializations here,
# but it's often cleaner to do this in main.py's startup event.

logger = logging.getLogger(__name__)
logger.debug("App package initialized.")