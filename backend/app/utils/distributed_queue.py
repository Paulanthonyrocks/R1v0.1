import pickle
import logging
import time
import queue
import os
import redis
from typing import Any, Optional, Tuple
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
            self._redis = get_redis_client(decode_responses=False)
        return self._redis

    def put(self, item: Any, block: bool = True, timeout: Optional[float] = None):
        """Pushes an item to the back of the queue."""
        data = pickle.dumps(item)
        
        if self.maxsize > 0:
            if self.qsize() >= self.maxsize:
                if not block:
                    raise Exception("Queue full")
                start_time = time.time()
                while self.qsize() >= self.maxsize:
                    if timeout and (time.time() - start_time) > timeout:
                        raise Exception("Queue full timeout")
                    time.sleep(0.01)
        
        self.redis.rpush(self.key, data)

    def put_nowait(self, item: Any):
        """Pushes an item to the back of the queue without blocking."""
        self.put(item, block=False)

    def get(self, block: bool = True, timeout: Optional[float] = None) -> Any:
        """Pops an item from the front of the queue."""
        if block:
            res = self.redis.blpop(self.key, timeout=int(timeout) if timeout else 0)
            if res:
                return pickle.loads(res[1])
            else:
                raise queue.Empty
        else:
            res = self.redis.lpop(self.key)
            if res:
                return pickle.loads(res)
            else:
                raise queue.Empty

    def get_nowait(self) -> Any:
        """Pops an item from the front of the queue without blocking."""
        return self.get(block=False)

    def qsize(self) -> int:
        """Returns the approximate size of the queue."""
        return self.redis.llen(self.key)

    def empty(self) -> bool:
        return self.qsize() == 0

    def close(self):
        pass

    def cancel_join_thread(self):
        pass

    def __getstate__(self):
        state = self.__dict__.copy()
        state['_redis'] = None
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)

class RedisStreamQueue:
    """
    A scalable queue implementation using Redis Streams and Consumer Groups.
    Provides at-least-once delivery and load balancing across multiple workers.
    """
    def __init__(self, name: str, group_name: str = "worker-group", consumer_id: Optional[str] = None, maxlen: int = 10000):
        self._redis = None
        self.name = name
        self.key = f"stream:{name}"
        self.group_name = group_name
        self.consumer_id = consumer_id or f"worker_{os.getpid()}"
        self.maxlen = maxlen
        
        # Ensure group is created
        self._ensure_group()
        logger.info(f"Initialized RedisStreamQueue '{name}' (Group: {group_name}, Consumer: {self.consumer_id})")

    @property
    def redis(self):
        if self._redis is None:
            self._redis = get_redis_client(decode_responses=False)
        return self._redis

    def _ensure_group(self):
        try:
            self.redis.xgroup_create(self.key, self.group_name, id="0", mkstream=True)
        except redis.exceptions.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise e

    def put(self, item: Any):
        """Pushes an item to the stream using XADD."""
        data = pickle.dumps(item)
        # Use maxlen to prevent the stream from growing indefinitely
        self.redis.xadd(self.key, {"data": data}, maxlen=self.maxlen, approximate=True)

    def put_nowait(self, item: Any):
        """Pushes an item to the stream without blocking."""
        self.put(item)

    def get(self, block: bool = True, timeout: Optional[float] = None) -> Tuple[str, Any]:
        """
        Reads a message from the stream using XREADGROUP.
        Returns: (message_id, item)
        """
        if not block:
            # Non-blocking read (timeout=0)
            res = self.redis.xreadgroup(self.group_name, self.consumer_id, {self.key: ">"}, count=1, block=0)
        else:
            # Blocking read
            res = self.redis.xreadgroup(self.group_name, self.consumer_id, {self.key: ">"}, count=1, block=int(timeout * 1000) if timeout else 0)

        if res and res[0][1]:
            # res format: [[stream_name, [[message_id, {data}]]]]
            msg_id, data_dict = res[0][1][0]
            return msg_id, pickle.loads(data_dict[b"data"])
        else:
            raise queue.Empty

    def get_nowait(self) -> Tuple[str, Any]:
        """Pops an item from the stream without blocking."""
        return self.get(block=False)

    def ack(self, message_id: str):
        """Acknowledges a message to mark it as processed."""
        self.redis.xack(self.key, self.group_name, message_id)

    def __getstate__(self):
        state = self.__dict__.copy()
        state['_redis'] = None
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)

    def qsize(self) -> int:
        """Returns the approximate size of the stream."""
        return self.redis.xlen(self.key)

    def empty(self) -> bool:
        return self.qsize() == 0

    def close(self):
        pass

    def cancel_join_thread(self):
        pass
