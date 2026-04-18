import json
import logging
import time
import queue
from typing import Any, Optional
from .redis_client import get_redis_client

logger = logging.getLogger("app.utils.redis_queue")

class RedisQueue:
    """
    A simple queue implementation using Redis Lists.
    Matches the basic interface of multiprocessing.Queue for easy swap-in.
    """
    def __init__(self, name: str, maxsize: int = 0):
        self._redis = None
        self.name = name
        self.key = f"q:{name}"
        self.maxsize = maxsize
        logger.info(f"Initialized RedisQueue '{name}'")

    @property
    def redis(self):
        if self._redis is None:
            self._redis = get_redis_client()
        return self._redis

    def put(self, item: Any, block: bool = True, timeout: Optional[float] = None):
        """Pushes an item to the back of the queue."""
        # Serialize to JSON (or pickle for complex objects, but JSON is safer across langs)
        # Note: Bbox and frames might be large, we might need a different strategy for frames
        data = json.dumps(item)
        
        if self.maxsize > 0:
            if self.qsize() >= self.maxsize:
                if not block:
                    raise Exception("Queue full")
                
                # Simple backoff wait if full
                start_time = time.time()
                while self.qsize() >= self.maxsize:
                    if timeout and (time.time() - start_time) > timeout:
                        raise Exception("Queue full timeout")
                    time.sleep(0.01)
        
        self.redis.rpush(self.key, data)

    def get(self, block: bool = True, timeout: Optional[float] = None) -> Any:
        """Pops an item from the front of the queue."""
        if block:
            # BLPOP blocks until an item is available
            # Redis blpop returns (key, value)
            res = self.redis.blpop(self.key, timeout=int(timeout) if timeout else 0)
            if res:
                return json.loads(res[1])
            else:
                raise queue.Empty
        else:
            res = self.redis.lpop(self.key)
            if res:
                return json.loads(res)
            else:
                raise queue.Empty

    def qsize(self) -> int:
        """Returns the approximate size of the queue."""
        return self.redis.llen(self.key)

    def empty(self) -> bool:
        return self.qsize() == 0

    def close(self):
        pass

    def cancel_join_thread(self):
        pass
