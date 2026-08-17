import pickle
import logging
import time
import queue
import os
import redis
from typing import Any, Optional, Tuple, Union, Callable, List
from .redis_client import get_redis_client

logger = logging.getLogger("app.utils.redis_queue")

class RedisEvent:
    """
    A Redis-backed event primitive.
    Matches the basic interface of multiprocessing.Event.
    """
    def __init__(self, name: str):
        self.name = name
        self.key = f"event:{name}"
        self.channel = f"event_chan:{name}"

    @property
    def redis(self):
        return get_redis_client()

    def set(self):
        """Set the event to true and notify waiters."""
        self.redis.set(self.key, "1")
        self.redis.publish(self.channel, "1")

    def clear(self):
        """Reset the event to false."""
        self.redis.delete(self.key)

    def is_set(self) -> bool:
        """Check if the event is set."""
        return self.redis.exists(self.key) > 0

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Block until the event is set or timeout occurs using Pub/Sub notification."""
        if self.is_set():
            return True

        pubsub = self.redis.pubsub()
        try:
            pubsub.subscribe(self.channel)
            # use get_message with timeout to block efficiently
            # we use a small internal timeout loop to allow for timeout check and non-blocking checks
            start_time = time.time()
            while True:
                elapsed = time.time() - start_time
                if timeout and elapsed > timeout:
                    return False
                
                # block for a reasonable amount of time, but not exceeding the remaining timeout
                wait_time = 1.0
                if timeout:
                    wait_time = min(1.0, timeout - elapsed)
                    if wait_time <= 0:
                        return False

                message = pubsub.get_message(ignore_subscribe_messages=True, timeout=wait_time)
                if message:
                    return True
                
                # Double check in case we missed the publish (race condition)
                if self.is_set():
                    return True
        finally:
            pubsub.unsubscribe(self.channel)
            pubsub.close()

class RedisPubSubSignal:
    """
    A Redis-backed signal using Pub/Sub for instant notification.
    """
    def __init__(self, name: str):
        self.name = name
        self.channel = f"signal:{name}"
        self._redis = get_redis_client()

    def publish(self, message: str = "1"):
        """Broadcast a signal to all subscribers."""
        self._redis.publish(self.channel, message)

    def subscribe_and_wait(self, timeout: Optional[float] = None) -> bool:
        """
        Block until a message is received on the channel.
        Returns True if a message was received, False if timeout occurred.
        
        Note: This is a fire-and-forget mechanism. Signals published to the 
        channel before the call to subscribe_and_wait are not buffered and 
        will be missed.
        """
        pubsub = self._redis.pubsub()
        try:
            pubsub.subscribe(self.channel)
            start_time = time.time()
            while True:
                elapsed = time.time() - start_time
                if timeout and elapsed > timeout:
                    return False
                
                # use dynamic timeout to avoid overshooting outer timeout
                wait_time = 0.5
                if timeout:
                    wait_time = min(0.5, timeout - elapsed)
                    if wait_time <= 0:
                        return False

                # Check for message
                # We use get_message(timeout=...) to avoid busy wait
                message = pubsub.get_message(ignore_subscribe_messages=True, timeout=wait_time)
                if message:
                    return True
            return False
        finally:
            pubsub.unsubscribe(self.channel)
            pubsub.close()

class RedisValue:
    """
    A Redis-backed shared value.
    Matches the basic interface of multiprocessing.Value.
    """
    def __init__(self, typecode: str, initial_value: Any, name: str):
        self.name = name
        self.key = f"value:{name}"
        self.typecode = typecode
        
        # Initialize value in Redis if not present
        r = get_redis_client()
        if not r.exists(self.key):
            self.value = initial_value

    @property
    def redis(self):
        return get_redis_client()

    @property
    def value(self) -> Any:
        """
        Returns the shared value. 
        Note: If typecode is 'i', 'f', or 'b', this will raise ValueError if the 
        stored value cannot be converted to that type.
        """
        val = self.redis.get(self.key)
        if val is None:
            return None
        
        # Basic type conversion based on typecode
        if self.typecode == 'i': return int(val)
        if self.typecode == 'f': return float(val)
        if self.typecode == 'b': return bool(int(val))
        return val.decode() if isinstance(val, bytes) else val

    @value.setter
    def value(self, new_val: Any):
        self.redis.set(self.key, new_val)

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
                    raise queue.Full()
                start_time = time.time()
                while self.qsize() >= self.maxsize:
                    if timeout and (time.time() - start_time) > timeout:
                        raise queue.Full()
                    time.sleep(0.01)
        
        self.redis.rpush(self.key, data)

    def put_nowait(self, item: Any):
        """Pushes an item to the back of the queue without blocking."""
        self.put(item, block=False)

    def get(self, block: bool = True, timeout: Optional[float] = None) -> Any:
        """Pops an item from the front of the queue."""
        if not block:
            res = self.redis.lpop(self.key)
            if res:
                return pickle.loads(res)
            else:
                raise queue.Empty

        # For blocking reads
        start_time = time.time()
        while True:
            # If we have a timeout and it's less than 1 second, we can't use BLPOP 
            # directly with that value. We use LPOP and sleep.
            remaining = timeout - (time.time() - start_time) if timeout is not None else None
            
            if timeout is not None and remaining is not None and remaining <= 0:
                raise queue.Empty

            if timeout is not None and remaining is not None and remaining < 1.0:
                # Sub-second wait: use LPOP + sleep to avoid Redis ERR value is not an integer
                res = self.redis.lpop(self.key)
                if res:
                    return pickle.loads(res)
                time.sleep(0.05)
                continue
            
            # Wait for 1s or the remaining integer timeout
            blpop_timeout = int(remaining) if remaining is not None else 0
            res = self.redis.blpop(self.key, timeout=blpop_timeout)
            if res:
                return pickle.loads(res[1])
            
            if timeout is not None:
                # BLPOP returned None (timeout), check if we've exceeded the overall timeout
                if (time.time() - start_time) > timeout:
                    raise queue.Empty
            else:
                # block=True but timeout=None and BLPOP returned None unexpectedly.
                # This should not happen for timeout=0 unless there is a connection issue.
                # Use a longer sleep to prevent CPU spin during transient failures.
                logger.warning("Redis BLPOP returned None unexpectedly during indefinite block. Retrying with backoff...")
                time.sleep(1.0)

    def get_nowait(self) -> Any:
        """Pops an item from the front of the queue without blocking."""
        return self.get(block=False)

    def qsize(self) -> int:
        """Returns the approximate size of the queue."""
        return self.redis.llen(self.key)

    def clear(self):
        """Removes all items from the queue."""
        if self.redis:
            self.redis.delete(self.key)

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
        logger.info(f"Initialized RedisStreamQueue '{name}' (Group: {group_name}, Consumer: {self.consumer_id}, Maxlen: {maxlen})")

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

    def put(self, item: Any, block: bool = True, timeout: Optional[float] = None):
        """
        Pushes an item to the stream using XADD.
        This method is non-blocking by nature. If the stream reaches `maxlen`, 
        Redis automatically evicts old entries.
        """
        data = pickle.dumps(item)

        # Use maxlen to prevent the stream from growing indefinitely
        self.redis.xadd(self.key, {"data": data}, maxlen=self.maxlen, approximate=True)

    def put_nowait(self, item: Any):
        """Pushes an item to the stream without blocking."""
        self.put(item, block=False)

    def get(self, block: bool = True, timeout: Optional[float] = None) -> Tuple[str, Any]:
        """
        Reads a message from the stream using XREADGROUP.
        First attempts to read pending messages (ID '0'), then new messages (ID '>').
        Returns: (message_id, item)
        """
        # If timeout is explicitly 0, it should be non-blocking.
        # If timeout is None and block=True, it should block indefinitely.
        block_ms = int(timeout * 1000) if timeout is not None else 0
        
        # Diagnostic log to trace group and consumer
        logger.debug(f"Polling stream {self.key} | Group: {self.group_name} | Consumer: {self.consumer_id}")

        # 1. Try to read pending messages first (ID '0')
        # Always read pending non-blockingly to avoid hanging before checking for new messages
        try:
            res = self.redis.xreadgroup(
                self.group_name, 
                self.consumer_id, 
                {self.key: "0"}, 
                count=1, 
                block=0
            )
            if res and res[0][1]:
                msg_id, data_dict = res[0][1][0]
                return msg_id, pickle.loads(data_dict[b"data"])
        except Exception as e:
            logger.debug(f"Error reading pending messages: {e}")

        # 2. Fallback to new messages (ID '>')
        if not block or (timeout is not None and timeout <= 0):
            res = self.redis.xreadgroup(self.group_name, self.consumer_id, {self.key: ">"}, count=1)
        else:
            # block=True and (timeout is None or timeout > 0)
            # Redis XREADGROUP block=0 means block indefinitely.
            actual_block = block_ms if timeout is not None else 0
            res = self.redis.xreadgroup(self.group_name, self.consumer_id, {self.key: ">"}, count=1, block=actual_block)

        if res and res[0][1]:
            msg_id, data_dict = res[0][1][0]
            return msg_id, pickle.loads(data_dict[b"data"])
        else:
            raise queue.Empty

    def get_nowait(self) -> Tuple[str, Any]:
        """Pops an item from the stream without blocking."""
        return self.get(block=False)

    def get_batch(self, batch_size: int = 100, block: bool = False, timeout: Optional[float] = None) -> List[Tuple[str, Any]]:
        """
        Reads a batch of messages from the stream.
        First attempts to read pending messages, then fills the rest from new messages.
        
        Note: This method prioritizes processing pending messages to ensure at-least-once 
        delivery. If a large backlog of pending messages exists, new messages may be 
        delayed until the backlog is cleared.
        """
        block_ms = int(timeout * 1000) if timeout is not None else 0
        results = []

        # 1. Try to read pending messages first (ID '0')
        # Always read pending non-blockingly to avoid hanging and process backlog first
        try:
            res = self.redis.xreadgroup(
                self.group_name, 
                self.consumer_id, 
                {self.key: "0"}, 
                count=batch_size, 
                block=0
            )
            if res and res[0][1]:
                for msg_id, data_dict in res[0][1]:
                    results.append((msg_id, pickle.loads(data_dict[b"data"])))
        except Exception as e:
            logger.debug(f"Error reading pending batch: {e}")

        # 2. Fill remaining batch size from new messages (ID '>')
        remaining = batch_size - len(results)
        if remaining > 0:
            try:
                # Use non-blocking if not block or if timeout is explicitly 0
                if not block or (timeout is not None and timeout <= 0):
                    res = self.redis.xreadgroup(
                        self.group_name, 
                        self.consumer_id, 
                        {self.key: ">"}, 
                        count=remaining
                    )
                else:
                    # block=True and (timeout is None or timeout > 0)
                    actual_block = block_ms if timeout is not None else 0
                    res = self.redis.xreadgroup(
                        self.group_name, 
                        self.consumer_id, 
                        {self.key: ">"}, 
                        count=remaining, 
                        block=actual_block
                    )
                if res and res[0][1]:
                    for msg_id, data_dict in res[0][1]:
                        results.append((msg_id, pickle.loads(data_dict[b"data"])))
            except Exception as e:
                logger.debug(f"Error reading new batch: {e}")

        return results

    def ack(self, message_id: str):
        """Acknowledges a message to mark it as processed."""
        self.redis.xack(self.key, self.group_name, message_id)

    def __getstate__(self):
        state = self.__dict__.copy()
        state['_redis'] = None
        return state

    def __setstate__(self, state):
        self.__dict__.update(state)
        # CRITICAL (duplicate-consumption audit): feed_manager sets the
        # multiprocessing start method to 'spawn'. Process args are pickled
        # ONCE in the parent, so every child unpickles the SAME consumer_id
        # baked at construction (default: worker_<parent_pid>). Redis
        # consumer groups then see all workers on a slot as ONE consumer,
        # and the pending-first read in get() (XREADGROUP ID '0') re-reads
        # every in-flight, unacked message on every call -- each worker on a
        # 4-worker slot re-processed the same frames (observed: identical
        # frame_idx=0 results 2-3x in result_processor logs). Regenerate a
        # per-process consumer id so XREADGROUP round-robins correctly.
        # Unconditional: every construction site in this codebase omits
        # consumer_id, and the documented default is explicitly pid-derived.
        self.consumer_id = f"worker_{os.getpid()}"

    def qsize(self) -> int:
        """
        Returns the approximate number of messages that need to be processed.
        This includes messages that have not yet been delivered to any consumer in the group (lag)
        and messages that have been delivered but not yet acknowledged (pending).
        """
        try:
            groups = self.redis.xinfo_groups(self.key)
            for group in groups:
                if group['name'] == self.group_name:
                    # 'lag' is available in Redis 7.0+. It represents messages not yet delivered to the group.
                    # 'pending' represents messages delivered but not yet acknowledged.
                    lag = group.get('lag', 0)
                    pending = group.get('pending', 0)
                    return lag + pending
            # If the group isn't found, fall back to total stream length as a coarse estimate.
            return self.redis.xlen(self.key)
        except Exception as e:
            logger.error(f"Error getting stream group info for {self.key}: {e}")
            # Fallback to xlen if xinfo_groups fails (e.g. older Redis version)
            try:
                return self.redis.xlen(self.key)
            except:
                return 0

    def empty(self) -> bool:
        return self.qsize() == 0

    def close(self):
        pass

    def cancel_join_thread(self):
        pass
