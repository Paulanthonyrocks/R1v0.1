import hashlib
import logging
import threading
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class FeatureFlags:
    """Manages feature flags for the application with thread safety and reload support."""
    
    def __init__(self, config: Dict[str, Any]):
        self._lock = threading.RLock()
        self._config_ref = config # Keep reference to original config if it's mutable
        self.reload()
        self._overrides = {}  # Runtime overrides
    
    def reload(self, new_config: Optional[Dict[str, Any]] = None):
        """Reloads flags from the configuration."""
        with self._lock:
            if new_config is not None:
                self._config_ref = new_config
            self._flags = self._config_ref.get("feature_flags", {})
            logger.info("Feature flags reloaded.")

    def is_enabled(self, feature: str, user_id: Optional[str] = None) -> bool:
        """
        Check if a feature is enabled.
        
        Args:
            feature: The name of the feature to check.
            user_id: Optional user ID for percentage rollouts.
            
        Returns:
            bool: True if the feature is enabled, False otherwise.
        """
        with self._lock:
            # 1. Check runtime overrides first
            if feature in self._overrides:
                return self._overrides[feature]
            
            # 2. Get configuration for the feature
            flag_config = self._flags.get(feature, False)
        
        # 3. Simple boolean flag
        if isinstance(flag_config, bool):
            return flag_config
        
        # 4. Dictionary configuration
        if isinstance(flag_config, dict):
            # Check for global enabled flag
            if not flag_config.get("enabled", True):
                return False
                
            # Percentage rollout
            if "percentage" in flag_config:
                if user_id:
                    # Consistent hashing for same user to ensure stable experience
                    # Use usedforsecurity=False for FIPS compliance
                    try:
                        hasher = hashlib.md5(f"{feature}:{user_id}".encode(), usedforsecurity=False)
                    except TypeError:
                        # Fallback for older Python versions
                        hasher = hashlib.md5(f"{feature}:{user_id}".encode())
                    
                    hash_val = int(hasher.hexdigest(), 16)
                    return (hash_val % 100) < flag_config["percentage"]
                
                # If percentage is set but no user_id is provided, default to disabled
                # unless a default_without_user is specified
                return flag_config.get("default_without_user", False)
            
            return flag_config.get("enabled", False)
        
        if flag_config is not None:
            logger.warning(f"Unexpected type for feature flag '{feature}': {type(flag_config)}. Defaulting to False.")
        
        return False
    
    def set_override(self, feature: str, enabled: bool):
        """Set a runtime override for a feature flag."""
        with self._lock:
            logger.info(f"Setting feature flag override: {feature}={enabled}")
            self._overrides[feature] = enabled
    
    def remove_override(self, feature: str):
        """Remove a runtime override for a feature flag."""
        with self._lock:
            if feature in self._overrides:
                logger.info(f"Removing feature flag override for: {feature}")
                del self._overrides[feature]
