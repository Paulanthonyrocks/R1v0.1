import redis
import redis.asyncio as async_redis
import logging
from typing import Optional, Union
from app.config import get_current_config

logger = logging.getLogger("app.utils.redis_client")

class RedisClient:
    _instance: Optional[redis.Redis] = None
    _async_instance: Optional[async_redis.Redis] = None

    @classmethod
    def get_client(cls) -> redis.Redis:
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
                # Test connection
                cls._instance.ping()
                logger.info(f"Connected to Redis (Sync) at {config.host}:{config.port}")
            except Exception as e:
                logger.error(f"Failed to connect to Redis (Sync): {e}")
                raise
        return cls._instance

    @classmethod
    async def get_async_client(cls) -> async_redis.Redis:
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
                # Test connection
                await cls._async_instance.ping()
                logger.info(f"Connected to Redis (Async) at {config.host}:{config.port}")
            except Exception as e:
                logger.error(f"Failed to connect to Redis (Async): {e}")
                raise
        return cls._async_instance

def get_redis_client() -> redis.Redis:
    return RedisClient.get_client()

async def get_async_redis_client() -> async_redis.Redis:
    return await RedisClient.get_async_client()