import asyncio
import logging
from datetime import datetime, timedelta
from typing import Callable, Any, Dict, Optional, Type, Union, Tuple
from functools import wraps
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

logger = logging.getLogger(__name__)

class CircuitBreaker:
    """Implementation of the Circuit Breaker pattern."""
    def __init__(self, name: str, failure_threshold: int = 5, timeout_duration: int = 60):
        self.name = name
        self.failure_threshold = failure_threshold
        self.timeout_duration = timeout_duration
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
    
    def __call__(self, func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            if self.state == "OPEN":
                if datetime.now() - self.last_failure_time > timedelta(seconds=self.timeout_duration):
                    self.state = "HALF_OPEN"
                    logger.info(f"Circuit breaker '{self.name}' entering HALF_OPEN state")
                else:
                    raise Exception(f"Circuit breaker '{self.name}' is OPEN")
            
            try:
                result = await func(*args, **kwargs)
                if self.state == "HALF_OPEN":
                    self.state = "CLOSED"
                    self.failure_count = 0
                    logger.info(f"Circuit breaker '{self.name}' reset to CLOSED state")
                return result
            except Exception as e:
                self.failure_count += 1
                self.last_failure_time = datetime.now()
                
                if self.failure_count >= self.failure_threshold:
                    self.state = "OPEN"
                    logger.error(f"Circuit breaker '{self.name}' opened after {self.failure_count} failures. Error: {e}")
                raise
        
        return wrapper

def retry_on_failure(
    attempts: int = 3, 
    min_wait: int = 2, 
    max_wait: int = 10, 
    exceptions: Union[Type[Exception], Tuple[Type[Exception], ...]] = (Exception,)
):
    """Decorator for retrying a function with exponential backoff."""
    return retry(
        stop=stop_after_attempt(attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=retry_if_exception_type(exceptions),
        reraise=True
    )
