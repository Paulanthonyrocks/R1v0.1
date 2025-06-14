# backend/app/utils/__init__.py

"""
Utility package for the application.

This __init__.py file re-exports key components from the various utility modules,
making them available directly under the `app.utils` namespace. This provides a
stable API even after internal refactoring of the utility modules.
"""

import logging # Keep logging if the __init__ itself logs.

# Import from .config module
from .config import ConfigError, load_config, DEFAULT_CONFIG, merge_dicts

# Import from .database module
from .database import DatabaseError, DatabaseManager

# Import from .image_processing module
from .image_processing import LicensePlatePreprocessor

# Import from .video module
from .video import FrameReader, FrameTimer

# Import from .visualization module
from .visualization import visualize_data, create_lane_overlay, create_grid_overlay, alpha_blend

# Import from .monitoring module
from .monitoring import TrafficMonitor

# Import from .utils module (the refactored utils.py which now only contains check_system_resources)
from .utils import check_system_resources


logger = logging.getLogger(__name__)
# Optional: Log a message when the package is initialized (for debugging)
# logger.debug("app.utils package initialized, components re-exported.")

# Define __all__ to specify the public API of the app.utils package
# This list includes all the components intended to be imported when a client
# executes `from app.utils import *`. It also helps linters and IDEs understand
# the public interface of the package.
__all__ = [
    # From config.py
    'ConfigError',
    'load_config',
    'DEFAULT_CONFIG',
    'merge_dicts',

    # From database.py
    'DatabaseError',
    'DatabaseManager',

    # From image_processing.py
    'LicensePlatePreprocessor',

    # From video.py
    'FrameReader',
    'FrameTimer',

    # From visualization.py
    'visualize_data',
    'create_lane_overlay',
    'create_grid_overlay',
    'alpha_blend',

    # From monitoring.py
    'TrafficMonitor',

    # From utils.py (the remaining part of the original utils)
    'check_system_resources'
]