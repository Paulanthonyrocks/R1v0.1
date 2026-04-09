import hashlib
import logging
import threading
from typing import Dict, Any, Optional
import json
from ..utils.redis_client import get_redis_client

logger = logging.getLogger(__name__)

class FeatureFlags:
    """Manages feature flags for the application with distributed state using Redis."""
    
    def __init__(self, config: Dict[str, Any]):
        self._lock = threading.RLock()
        self._config_ref = config 
        self.reload()
        # Local cache to avoid Redis hits on every is_enabled call
        self._local_overrides = {} 
        self._last_redis_sync = 0.0
        self._sync_interval = 1.0 # Sync with Redis every 1 second

    def reload(self, new_config: Optional[Dict[str, Any]] = None):
        """Reloads flags from the configuration."""
        with self._lock:
            if new_config is not None:
                self._config_ref = new_config
            self._flags = self._config_ref.get("feature_flags", {})
            logger.info("Feature flags reloaded.")

    def _get_redis_overrides(self) -> Dict[str, Any]:
        """Fetch current overrides from Redis."""
        try:
            client = get_redis_client()
            data = client.get("app:feature_flag_overrides")
            if data:
                return json.loads(data)
        except Exception as e:
            logger.error(f"Error fetching feature flag overrides from Redis: {e}")
        return {}

    def _sync_overrides(self):
        """Periodically sync local overrides with Redis."""
        import time
        now = time.time()
        if now - self._last_redis_sync > self._sync_interval:
            with self._lock:
                self._local_overrides = self._get_redis_overrides()
                self._last_redis_sync = now

    def is_enabled(self, feature: str, user_id: Optional[str] = None) -> bool:
        """
        Check if a feature is enabled.
        """
        self._sync_overrides()
        
        with self._lock:
            # 1. Check overrides (synced from Redis) first
            if feature in self._local_overrides:
                return self._local_overrides[feature]
            
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
                    try:
                        hasher = hashlib.md5(f"{feature}:{user_id}".encode(), usedforsecurity=False)
                    except TypeError:
                        hasher = hashlib.md5(f"{feature}:{user_id}".encode())
                    
                    hash_val = int(hasher.hexdigest(), 16)
                    return (hash_val % 100) < flag_config["percentage"]
                
                return flag_config.get("default_without_user", False)
            
            return flag_config.get("enabled", False)
        
        if flag_config is not None:
            logger.warning(f"Unexpected type for feature flag '{feature}': {type(flag_config)}. Defaulting to False.")
        
        return False
    
    def set_override(self, feature: str, enabled: bool):
        """Set a runtime override for a feature flag and persist it to Redis."""
        with self._lock:
            logger.info(f"Setting feature flag override: {feature}={enabled}")
            try:
                client = get_redis_client()
                # Get current, update, and set back (simple atomic-ish approach for this scale)
                overrides = self._get_redis_overrides()
                overrides[feature] = enabled
                client.set("app:feature_flag_overrides", json.dumps(overrides))
                # Update local cache immediately
                self._local_overrides[feature] = enabled
            except Exception as e:
                logger.error(f"Failed to set feature flag override in Redis: {e}")
                # Fallback: only update locally
                self._local_overrides[feature] = enabled
    
    def remove_override(self, feature: str):
        """Remove a runtime override for a feature flag and persist it to Redis."""
        with self._lock:
            if feature in self._local_overrides or self._get_redis_overrides().get(feature) is not None:
                logger.info(f"Removing feature flag override for: {feature}")
                try:
                    client = get_redis_client()
                    overrides = self._get_redis_overrides()
                    if feature in overrides:
                        del overrides[feature]
                        client.set("app:feature_flag_overrides", json.dumps(overrides))
                    self._local_overrides.pop(feature, None)
                except Exception as e:
                    logger.error(f"Failed to remove feature flag override from Redis: {e}")
