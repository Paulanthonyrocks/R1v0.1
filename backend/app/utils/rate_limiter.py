import asyncio
import logging
import time
from typing import Dict

logger = logging.getLogger(__name__)

class TokenBucketLimiter:
    """
    A simple implementation of the Token Bucket algorithm for rate limiting.
    """
    def __init__(self, rate: float, capacity: float):
        """
        Args:
            rate: Number of tokens added per second.
            capacity: Maximum number of tokens the bucket can hold.
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.monotonic()
        self.last_used = time.monotonic()
        self._lock = asyncio.Lock()

    async def consume(self, tokens: float = 1.0) -> bool:
        """
        Attempts to consume the specified number of tokens.
        Returns True if successful, False otherwise.
        """
        async with self._lock:
            now = time.monotonic()
            # Refill tokens based on time elapsed
            elapsed = now - self.last_update
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_update = now
            self.last_used = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

class RateLimiterManager:
    """
    Manages multiple TokenBucketLimiters, one for each client.
    """
    def __init__(self, rate: float, capacity: float, cleanup_interval: int = 300):
        self.rate = rate
        self.capacity = capacity
        self.cleanup_interval = cleanup_interval
        self.limiters: Dict[str, TokenBucketLimiter] = {}
        self.last_cleanup = time.monotonic()
        self._lock = asyncio.Lock()

    async def is_allowed(self, client_id: str) -> bool:
        """
        Checks if a request from the given client_id is allowed.
        """
        async with self._lock:
            # Periodic cleanup of old limiters
            now = time.monotonic()
            if now - self.last_cleanup > self.cleanup_interval:
                self._cleanup()
                self.last_cleanup = now

            if client_id not in self.limiters:
                self.limiters[client_id] = TokenBucketLimiter(self.rate, self.capacity)
            
            return await self.limiters[client_id].consume()

    def _cleanup(self):
        """
        Removes limiters that haven't been used recently.
        """
        now = time.monotonic()
        to_remove = []
        for client_id, limiter in self.limiters.items():
            if now - limiter.last_used > self.cleanup_interval:
                to_remove.append(client_id)
        
        for client_id in to_remove:
            del self.limiters[client_id]
            
        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} expired rate limiters.")
