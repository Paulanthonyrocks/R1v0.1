from typing import Dict, Any, Optional, List, Tuple
import hashlib
import logging
import threading

logger = logging.getLogger(__name__)

from typing import Dict, Any, Optional, List, Tuple
import hashlib
import logging
import threading

logger = logging.getLogger(__name__)

class FeatureFlags:
    """Manages feature flags for the application with support for overrides and percentage rollouts."""
    
    def __init__(self, config: Dict[str, Any]):
        self._config = config
        self._flags = config.get("feature_flags", {})
        self._lock = threading.RLock()
        
        # Initialize Redis client for shared overrides
        try:
            from app.utils.redis_client import get_redis_client
            self._redis = get_redis_client()
            self._redis_prefix = "ff_override:"
        except Exception as e:
            logger.error(f"Failed to initialize Redis for FeatureFlags: {e}")
            self._redis = None
            self._redis_prefix = None
    
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
            # 1. Check shared Redis overrides first
            if self._redis:
                try:
                    val = self._redis.get(f"{self._redis_prefix}{feature}")
                    if val is not None:
                        # Redis returns bytes; convert to boolean
                        return val.decode('utf-8').lower() == 'true'
                except Exception as e:
                    logger.warning(f"Error reading feature override from Redis: {e}")
            
            # 2. Get configuration for the feature
            if feature not in self._flags:
                logger.warning(f"Querying unknown feature flag: {feature}")
                return False
                
            flag_config = self._flags.get(feature)
            
            # 3. Simple boolean flag
            if isinstance(flag_config, bool):
                return flag_config
            
            # 4. Dictionary configuration
            if isinstance(flag_config, dict):
                # If explicitly disabled, it's off regardless of other settings
                if flag_config.get("enabled") is False:
                    return False
                    
                # Percentage rollout logic
                if "percentage" in flag_config:
                    percentage = flag_config["percentage"]
                    
                    # Validate percentage is a valid integer in [0, 100]
                    if not isinstance(percentage, int) or not (0 <= percentage <= 100):
                        logger.warning(f"Invalid percentage value for feature {feature}: {percentage}. Expected int [0, 100].")
                        return False
                        
                    if user_id:
                        # Use SHA-256 for secure and consistent bucketing
                        hash_input = f"{feature}:{user_id}".encode()
                        hash_val = int(hashlib.sha256(hash_input).hexdigest(), 16)
                        return (hash_val % 100) < percentage
                    
                    # Fallback when percentage is set but no user_id is provided
                    return flag_config.get("default_without_user", False)
                
                # Default to 'enabled' key value, or False (opt-in)
                return flag_config.get("enabled", False)
            
            # Invalid config type
            logger.warning(f"Invalid configuration type for feature {feature}: {type(flag_config)}. Expected bool or dict.")
            return False
    
    def set_override(self, feature: str, enabled: bool):
        """Set a runtime override for a feature flag in shared Redis store."""
        with self._lock:
            if not self._redis:
                logger.error("Cannot set override: Redis client not available.")
                return
            
            try:
                self._redis.set(f"{self._redis_prefix}{feature}", str(enabled).lower())
                logger.info(f"Setting shared feature flag override: {feature}={enabled}")
            except Exception as e:
                logger.error(f"Failed to set feature override in Redis: {e}")
    
    def remove_override(self, feature: str):
        """Remove a runtime override for a feature flag from shared Redis store."""
        with self._lock:
            if not self._redis:
                logger.error("Cannot remove override: Redis client not available.")
                return
                
            try:
                # Check if it exists first to provide the warning from the audit
                if self._redis.exists(f"{self._redis_prefix}{feature}"):
                    self._redis.delete(f"{self._redis_prefix}{feature}")
                    logger.info(f"Removing shared feature flag override for: {feature}")
                else:
                    logger.warning(f"Attempted to remove override for unknown feature: {feature}")
            except Exception as e:
                logger.error(f"Failed to remove feature override from Redis: {e}")

    def get_all_flags(self) -> Dict[str, bool]:
        """Returns the effective state of all configured feature flags."""
        with self._lock:
            effective_flags = {}
            for feature in self._flags:
                # Check Redis first
                if self._redis:
                    try:
                        val = self._redis.get(f"{self._redis_prefix}{feature}")
                        if val is not None:
                            effective_flags[feature] = val.decode('utf-8').lower() == 'true'
                            continue
                    except Exception:
                        pass
                
                conf = self._flags[feature]
                if isinstance(conf, bool):
                    effective_flags[feature] = conf
                elif isinstance(conf, dict):
                    is_on = conf.get("enabled", False)
                    has_perc = conf.get("percentage", 0) > 0 if isinstance(conf.get("percentage"), int) else False
                    effective_flags[feature] = is_on or has_perc
                else:
                    effective_flags[feature] = False
            return effective_flags
