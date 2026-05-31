import redis
import redis.asyncio as async_redis
import logging
import threading
import asyncio
from typing import Optional, Union
from app.config import get_current_config

logger = logging.getLogger("app.utils.redis_client")

class RedisClient:
    """
    Singleton manager for Redis connections.
    
    Maintains separate connection pools for decoded responses (UTF-8 strings) 
    and raw responses (bytes) to avoid repeated encoding/decoding overhead 
    and allow low-level binary operations. This approach doubles the number of 
    pools but optimizes per-call performance.
    
    Important:
    - For complete cleanup, both `shutdown()` and `shutdown_async()` must be called.
    - The `_async_lock` is lazy-initialized to prevent binding to an incorrect 
      or closed event loop during module import.
    """
    _instance: Optional[redis.Redis] = None
    _raw_instance: Optional[redis.Redis] = None
    _async_instance: Optional[async_redis.Redis] = None
    _async_raw_instance: Optional[async_redis.Redis] = None
    
    _lock = threading.Lock()
    _async_lock: Optional[asyncio.Lock] = None

    @classmethod
    def get_client(cls, decode_responses: bool = True) -> redis.Redis:
        if decode_responses:
            if cls._instance is None:
                with cls._lock:
                    if cls._instance is None:
                        config = get_current_config().redis
                        if not config.enabled:
                            raise RuntimeError("Redis is disabled in configuration")
                        
                        client = None
                        for attempt in range(3):
                            try:
                                client = redis.Redis(
                                    host=config.host,
                                    port=config.port,
                                    db=config.db,
                                    password=config.password,
                                    decode_responses=True,
                                    socket_timeout=10.0,
                                    socket_connect_timeout=5.0
                                )
                                client.ping()
                                logger.info(f"Connected to Redis (Sync - Decoded) at {config.host}:{config.port}")
                                break
                            except Exception as e:
                                if attempt == 2:
                                    logger.error(f"Failed to connect to Redis (Sync - Decoded) after 3 attempts: {e}")
                                    raise
                                wait_time = (attempt + 1) * 2
                                logger.warning(f"Redis connection attempt {attempt + 1} failed, retrying in {wait_time}s... Error: {e}")
                                time.sleep(wait_time)
                        cls._instance = client
            return cls._instance
        else:
            if cls._raw_instance is None:
                with cls._lock:
                    if cls._raw_instance is None:
                        config = get_current_config().redis
                        if not config.enabled:
                            raise RuntimeError("Redis is disabled in configuration")
                        
                        client = None
                        for attempt in range(3):
                            try:
                                client = redis.Redis(
                                    host=config.host,
                                    port=config.port,
                                    db=config.db,
                                    password=config.password,
                                    decode_responses=False,
                                    socket_timeout=10.0,
                                    socket_connect_timeout=5.0
                                )
                                client.ping()
                                logger.info(f"Connected to Redis (Sync - Raw) at {config.host}:{config.port}")
                                break
                            except Exception as e:
                                if attempt == 2:
                                    logger.error(f"Failed to connect to Redis (Sync - Raw) after 3 attempts: {e}")
                                    raise
                                wait_time = (attempt + 1) * 2
                                logger.warning(f"Redis connection attempt {attempt + 1} failed, retrying in {wait_time}s... Error: {e}")
                                time.sleep(wait_time)
                        cls._raw_instance = client
            return cls._raw_instance

    @classmethod
    async def get_async_client(cls, decode_responses: bool = True) -> async_redis.Redis:
        if cls._async_lock is None:
            with cls._lock:
                if cls._async_lock is None:
                    cls._async_lock = asyncio.Lock()

        instance_attr = '_async_instance' if decode_responses else '_async_raw_instance'
        instance = getattr(cls, instance_attr)
        if instance is not None:
            return instance

        config = get_current_config().redis
        if not config.enabled:
            raise RuntimeError("Redis is disabled in configuration")

        try:
            # Create client and ping outside the lock to avoid blocking other callers
            client = async_redis.Redis(
                host=config.host,
                port=config.port,
                db=config.db,
                password=config.password,
                decode_responses=decode_responses,
                socket_timeout=10.0,
                socket_connect_timeout=5.0
            )
            await client.ping()

            async with cls._async_lock:
                if getattr(cls, instance_attr) is None:
                    setattr(cls, instance_attr, client)
                    logger.info(f"Connected to Redis (Async - {'Decoded' if decode_responses else 'Raw'}) at {config.host}:{config.port}")
                else:
                    # Concurrent initialization happened; close the redundant client
                    await client.close()
        except Exception as e:
            logger.error(f"Failed to connect to Redis (Async - {'Decoded' if decode_responses else 'Raw'}): {e}")
            raise

        return getattr(cls, instance_attr)

    @classmethod
    def shutdown(cls):
        """Gracefully closes all active Redis connection pools."""
        with cls._lock:
            for attr in ['_instance', '_raw_instance']:
                client = getattr(cls, attr)
                if client:
                    try:
                        client.close()
                        logger.debug(f"Closed sync redis client {attr}")
                    except Exception as e:
                        logger.error(f"Error closing {attr}: {e}")
            cls._instance = None
            cls._raw_instance = None

    @classmethod
    async def shutdown_async(cls):
        """Gracefully closes all active async Redis connection pools."""
        if cls._async_lock is None:
            return

        async with cls._async_lock:
            for attr in ['_async_instance', '_async_raw_instance']:
                client = getattr(cls, attr)
                if client:
                    try:
                        await client.close()
                        logger.debug(f"Closed async redis client {attr}")
                    except Exception as e:
                        logger.error(f"Error closing {attr}: {e}")
            cls._async_instance = None
            cls._async_raw_instance = None

    @classmethod
    async def shutdown_all(cls):
        """
        Performs a complete cleanup of all Redis connection pools.
        Calls both sync and async shutdown methods.
        """
        cls.shutdown()
        await cls.shutdown_async()

    @classmethod
    def reset(cls):
        """
        Invalidates all singleton instances and closes their connection pools.
        Should be called when configuration is reloaded to ensure 
        clients are recreated with new connection parameters.
        """
        with cls._lock:
            for attr in ['_instance', '_raw_instance', '_async_instance', '_async_raw_instance']:
                client = getattr(cls, attr)
                if client:
                    try:
                        client.close()
                        logger.debug(f"Closed Redis client {attr} during reset.")
                    except Exception as e:
                        logger.error(f"Error closing {attr} during reset: {e}")
            
            cls._instance = None
            cls._raw_instance = None
            cls._async_instance = None
            cls._async_raw_instance = None
            logger.info("RedisClient singleton instances have been reset and connections closed.")


def get_redis_client(decode_responses: bool = True) -> redis.Redis:
    return RedisClient.get_client(decode_responses=decode_responses)

async def get_async_redis_client(decode_responses: bool = True) -> async_redis.Redis:
    return await RedisClient.get_async_client(decode_responses=decode_responses)
