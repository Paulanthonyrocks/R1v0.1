import redis
import redis.asyncio as async_redis
import logging
from typing import Optional, Union
from app.config import get_current_config

logger = logging.getLogger("app.utils.redis_client")

class RedisClient:
    _instance: Optional[redis.Redis] = None
    _raw_instance: Optional[redis.Redis] = None
    _async_instance: Optional[async_redis.Redis] = None
    _async_raw_instance: Optional[async_redis.Redis] = None

    @classmethod
    def get_client(cls, decode_responses: bool = True) -> redis.Redis:
        if decode_responses:
            if cls._instance is None:
                config = get_current_config().redis
                if not config.enabled:
                    raise RuntimeError("Redis is disabled in configuration")
                try:
                    cls._instance = redis.Redis(
                        host=config.host,
                        port=config.port,
                        db=config.db,
                        password=config.password,
                        decode_responses=True
                    )
                    cls._instance.ping()
                    logger.info(f"Connected to Redis (Sync - Decoded) at {config.host}:{config.port}")
                except Exception as e:
                    logger.error(f"Failed to connect to Redis (Sync - Decoded): {e}")
                    raise
            return cls._instance
        else:
            if cls._raw_instance is None:
                config = get_current_config().redis
                if not config.enabled:
                    raise RuntimeError("Redis is disabled in configuration")
                try:
                    cls._raw_instance = redis.Redis(
                        host=config.host,
                        port=config.port,
                        db=config.db,
                        password=config.password,
                        decode_responses=False
                    )
                    cls._raw_instance.ping()
                    logger.info(f"Connected to Redis (Sync - Raw) at {config.host}:{config.port}")
                except Exception as e:
                    logger.error(f"Failed to connect to Redis (Sync - Raw): {e}")
                    raise
            return cls._raw_instance

    @classmethod
    async def get_async_client(cls, decode_responses: bool = True) -> async_redis.Redis:
        if decode_responses:
            if cls._async_instance is None:
                config = get_current_config().redis
                if not config.enabled:
                    raise RuntimeError("Redis is disabled in configuration")
                try:
                    cls._async_instance = async_redis.Redis(
                        host=config.host,
                        port=config.port,
                        db=config.db,
                        password=config.password,
                        decode_responses=True
                    )
                    await cls._async_instance.ping()
                    logger.info(f"Connected to Redis (Async - Decoded) at {config.host}:{config.port}")
                except Exception as e:
                    logger.error(f"Failed to connect to Redis (Async - Decoded): {e}")
                    raise
            return cls._async_instance
        else:
            if cls._async_raw_instance is None:
                config = get_current_config().redis
                if not config.enabled:
                    raise RuntimeError("Redis is disabled in configuration")
                try:
                    cls._async_raw_instance = async_redis.Redis(
                        host=config.host,
                        port=config.port,
                        db=config.db,
                        password=config.password,
                        decode_responses=False
                    )
                    await cls._async_raw_instance.ping()
                    logger.info(f"Connected to Redis (Async - Raw) at {config.host}:{config.port}")
                except Exception as e:
                    logger.error(f"Failed to connect to Redis (Async - Raw): {e}")
                    raise
            return cls._async_raw_instance

def get_redis_client(decode_responses: bool = True) -> redis.Redis:
    return RedisClient.get_client(decode_responses=decode_responses)

async def get_async_redis_client(decode_responses: bool = True) -> async_redis.Redis:
    return await RedisClient.get_async_client(decode_responses=decode_responses)
