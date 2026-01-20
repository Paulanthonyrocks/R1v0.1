from typing import Dict, Any, Optional
import hashlib
import logging

logger = logging.getLogger(__name__)

class FeatureFlags:
    """Manages feature flags for the application."""
    
    def __init__(self, config: Dict[str, Any]):
        self._flags = config.get("feature_flags", {})
        self._overrides = {}  # Runtime overrides
    
    def is_enabled(self, feature: str, user_id: Optional[str] = None) -> bool:
        """
        Check if a feature is enabled.
        
        Args:
            feature: The name of the feature to check.
            user_id: Optional user ID for percentage rollouts.
            
        Returns:
            bool: True if the feature is enabled, False otherwise.
        """
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
                    hash_val = int(hashlib.md5(f"{feature}:{user_id}".encode()).hexdigest(), 16)
                    return (hash_val % 100) < flag_config["percentage"]
                
                # If percentage is set but no user_id is provided, default to disabled
                # unless a default_without_user is specified
                return flag_config.get("default_without_user", False)
            
            return flag_config.get("enabled", False)
        
        return False
    
    def set_override(self, feature: str, enabled: bool):
        """Set a runtime override for a feature flag."""
        logger.info(f"Setting feature flag override: {feature}={enabled}")
        self._overrides[feature] = enabled
    
    def remove_override(self, feature: str):
        """Remove a runtime override for a feature flag."""
        if feature in self._overrides:
            logger.info(f"Removing feature flag override for: {feature}")
            del self._overrides[feature]
