from typing import Dict, Optional, Any
import hashlib
import logging
import asyncio
from fastapi import Header

logger = logging.getLogger(__name__)

class IdempotencyManager:
    def __init__(self, ttl_seconds: int = 3600):
        self._cache = {}  # In production, use Redis
        self._ttl = ttl_seconds
    
    async def get_or_execute(
        self, 
        idempotency_key: str, 
        operation: callable
    ) -> tuple[Any, bool]:
        """Execute operation only once per key.
        
        Returns: (result, was_cached)
        """
        if idempotency_key in self._cache:
            logger.info(f"Returning cached result for key: {idempotency_key}")
            return self._cache[idempotency_key], True
        
        if asyncio.iscoroutinefunction(operation):
             result = await operation()
        else:
             result = operation()

        self._cache[idempotency_key] = result
        
        # Schedule cleanup
        asyncio.create_task(self._cleanup_after_ttl(idempotency_key))
        
        return result, False
    
    async def _cleanup_after_ttl(self, key: str):
        await asyncio.sleep(self._ttl)
        self._cache.pop(key, None)

# Global instance
idempotency_mgr = IdempotencyManager()
