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
        self._lock = threading.Lock()
        
        # Initialize Redis client for shared overrides
        try:
            from app.utils.redis_client import get_redis_client
            self._redis = get_redis_client()
            self._redis_prefix = "ff_override:"
        except Exception as e:
            logger.error(f"Failed to initialize Redis for FeatureFlags: {e}")
            self._redis = None
    
    def is_enabled(self, feature: str, user_id: Optional[str] = None) -> bool:
        """
        Check if a feature is enabled.
        
        Args:
            feature: The name of the feature to check.
            user_id: Optional user ID for percentage rollouts.
            
        Returns:
            bool: True if the feature is enabled, False otherwise.
        """
        # 1. Check shared Redis overrides first (Network call outside lock)
        if self._redis:
            try:
                val = self._redis.get(f"{self._redis_prefix}{feature}")
                if val is not None:
                    return val.decode('utf-8').lower() == 'true'
            except Exception as e:
                logger.warning(f"Error reading feature override from Redis: {e}")

        with self._lock:
            # 2. Get configuration for the feature
            if feature not in self._flags:
                logger.debug(f"Querying unknown feature flag: {feature}")
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
                    
                    # Relaxed type check: allow int or float
                    if not isinstance(percentage, (int, float)) or not (0 <= percentage <= 100):
                        logger.warning(f"Invalid percentage value for feature {feature}: {percentage}. Expected numeric [0, 100].")
                        return False
                        
                    percentage = int(percentage)
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
        """Set a runtime override for a feature flag in shared Redis store.
        Overrides have a TTL to prevent indefinite accumulation.
        """
        with self._lock:
            if not self._redis:
                logger.error("Cannot set override: Redis client not available.")
                return
            
            try:
                ttl = self._config.get("feature_flags", {}).get("override_ttl", 3600)
                self._redis.setex(f"{self._redis_prefix}{feature}", ttl, str(enabled).lower())
                logger.info(f"Setting shared feature flag override: {feature}={enabled} (TTL: {ttl}s)")
            except Exception as e:
                logger.error(f"Failed to set feature override in Redis: {e}")
    
    def remove_override(self, feature: str):
        """Remove a runtime override for a feature flag from shared Redis store."""
        if not self._redis:
            logger.error("Cannot remove override: Redis client not available.")
            return
            
        try:
            # Delete directly. Redis delete returns the number of keys removed.
            deleted_count = self._redis.delete(f"{self._redis_prefix}{feature}")
            if deleted_count > 0:
                logger.info(f"Removed shared feature flag override for: {feature}")
        except Exception as e:
            logger.error(f"Failed to remove feature override from Redis: {e}")

    def get_all_flags(self) -> Dict[str, Any]:
        """
        Returns the state of all configured feature flags.
        
        For simple flags, returns the boolean value.
        For complex flags, returns a dict with 'enabled' and 'percentage' (if applicable).
        """
        with self._lock:
            effective_flags = {}
            for feature in self._flags:
                # 1. Check Redis override first (highest priority)
                if self._redis:
                    try:
                        val = self._redis.get(f"{self._redis_prefix}{feature}")
                        if val is not None:
                            # Overrides are treated as absolute boolean state
                            effective_flags[feature] = val.decode('utf-8').lower() == 'true'
                            continue
                    except Exception:
                        pass
                
                conf = self._flags[feature]
                if isinstance(conf, bool):
                    effective_flags[feature] = conf
                elif isinstance(conf, dict):
                    # Return the rich configuration to allow the caller to distinguish 
                    # global enable vs percentage rollout.
                    effective_flags[feature] = {
                        "enabled": conf.get("enabled", False),
                        "percentage": conf.get("percentage")
                    }
                else:
                    effective_flags[feature] = False
            return effective_flags
