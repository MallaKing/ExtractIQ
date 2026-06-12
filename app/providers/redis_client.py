import redis
from app.config import settings
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class RedisClient:
    """Redis client wrapper."""
    
    def __init__(self):
        """Initialize Redis connection."""
        self.redis = redis.from_url(settings.redis_url, decode_responses=True)
        logger.info("Redis client initialized")
    
    def get(self, key: str) -> Optional[str]:
        """Get value by key."""
        try:
            return self.redis.get(key)
        except Exception as e:
            logger.error(f"Redis GET error: {e}")
            return None
    
    def set(self, key: str, value: str, ex: Optional[int] = None) -> bool:
        """Set value with optional expiration."""
        try:
            self.redis.set(key, value, ex=ex)
            return True
        except Exception as e:
            logger.error(f"Redis SET error: {e}")
            return False
    
    def delete(self, key: str) -> bool:
        """Delete key."""
        try:
            self.redis.delete(key)
            return True
        except Exception as e:
            logger.error(f"Redis DELETE error: {e}")
            return False
    
    def exists(self, key: str) -> bool:
        """Check if key exists."""
        try:
            return self.redis.exists(key)
        except Exception as e:
            logger.error(f"Redis EXISTS error: {e}")
            return False
    
    def incr(self, key: str) -> int:
        """Increment counter."""
        try:
            return self.redis.incr(key)
        except Exception as e:
            logger.error(f"Redis INCR error: {e}")
            return 0
    
    def health_check(self) -> bool:
        """Check Redis connection health."""
        try:
            self.redis.ping()
            return True
        except Exception as e:
            logger.error(f"Redis health check failed: {e}")
            return False


# Singleton instance
_redis_client = None


def get_redis_client() -> RedisClient:
    """Get Redis client singleton."""
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient()
    return _redis_client
