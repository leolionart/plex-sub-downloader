"""
Redis cache client cho subtitle search results.
Giảm API calls bằng cách cache kết quả search.
"""

import logging
import json
from typing import Any
from datetime import timedelta

import httpx

from app.models.runtime_config import RuntimeConfig
from app.models.subtitle import SubtitleResult, SubtitleSearchParams
from app.utils.logger import get_logger

logger = get_logger(__name__)


class CacheClient:
    """
    Cache client sử dụng Redis hoặc in-memory fallback.

    Features:
    - Cache subtitle search results
    - TTL-based expiration
    - Automatic serialization
    - Graceful degradation nếu Redis unavailable
    """

    def __init__(self, config: RuntimeConfig) -> None:
        """Initialize cache client."""
        self._config = config
        self.redis_url = config.redis_url
        self.cache_ttl = getattr(config, "cache_ttl_seconds", 3600)  # 1 hour default
        self.enabled = getattr(config, "cache_enabled", True)

        # In-memory cache fallback
        self._memory_cache: dict[str, tuple[Any, float]] = {}

        # Try Redis connection
        self._redis_client = None
        if self.redis_url and self.enabled:
            self._init_redis()
        else:
            logger.info("Cache: Using in-memory fallback (no Redis configured)")

    def _init_redis(self) -> None:
        """Initialize Redis client."""
        try:
            # Import redis only if needed
            import redis.asyncio as redis

            self._redis_client = redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            logger.info(f"Cache: Connected to Redis at {self.redis_url}")

        except ImportError:
            logger.warning("Redis library not installed, using in-memory cache")
            logger.info("Install with: poetry add redis")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            logger.info("Falling back to in-memory cache")

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis_client:
            await self._redis_client.close()

    def _make_cache_key(self, params: SubtitleSearchParams) -> str:
        """
        Generate cache key từ search params.

        Format: subtitle:search:{hash}
        """
        # Create unique key từ search parameters
        key_parts = [
            f"lang={params.language}",
            f"imdb={params.imdb_id}" if params.imdb_id else "",
            f"tmdb={params.tmdb_id}" if params.tmdb_id else "",
            f"title={params.title}" if params.title else "",
            f"year={params.year}" if params.year else "",
            f"s={params.season}" if params.season else "",
            f"e={params.episode}" if params.episode else "",
        ]

        key_string = ":".join(filter(None, key_parts))
        import hashlib
        key_hash = hashlib.md5(key_string.encode()).hexdigest()[:12]

        return f"subtitle:search:{key_hash}"

    async def get_search_results(
        self,
        params: SubtitleSearchParams,
    ) -> list[SubtitleResult] | None:
        """
        Lấy cached search results.

        Returns:
            List of SubtitleResult nếu hit cache, None nếu miss
        """
        if not self.enabled:
            return None

        cache_key = self._make_cache_key(params)

        try:
            # Try Redis first
            if self._redis_client:
                cached = await self._redis_client.get(cache_key)
                if cached:
                    logger.debug(f"Cache HIT (Redis): {cache_key}")
                    data = json.loads(cached)
                    return [SubtitleResult(**item) for item in data]
            else:
                # Fallback to in-memory
                import time
                if cache_key in self._memory_cache:
                    cached_data, expire_time = self._memory_cache[cache_key]
                    if time.time() < expire_time:
                        logger.debug(f"Cache HIT (memory): {cache_key}")
                        return [SubtitleResult(**item) for item in cached_data]
                    else:
                        # Expired
                        del self._memory_cache[cache_key]

            logger.debug(f"Cache MISS: {cache_key}")
            return None

        except Exception as e:
            logger.warning(f"Cache get error: {e}")
            return None

    async def set_search_results(
        self,
        params: SubtitleSearchParams,
        results: list[SubtitleResult],
    ) -> bool:
        """
        Cache search results.

        Args:
            params: Search parameters (used for key)
            results: List of SubtitleResult to cache

        Returns:
            True nếu cache thành công
        """
        if not self.enabled or not results:
            return False

        cache_key = self._make_cache_key(params)

        try:
            # Serialize results
            data = [result.model_dump() for result in results]
            json_data = json.dumps(data)

            # Try Redis first
            if self._redis_client:
                await self._redis_client.setex(
                    cache_key,
                    self.cache_ttl,
                    json_data,
                )
                logger.debug(f"Cache SET (Redis): {cache_key} (TTL={self.cache_ttl}s)")
                return True
            else:
                # Fallback to in-memory
                import time
                expire_time = time.time() + self.cache_ttl
                self._memory_cache[cache_key] = (data, expire_time)
                logger.debug(f"Cache SET (memory): {cache_key}")
                return True

        except Exception as e:
            logger.warning(f"Cache set error: {e}")
            return False

    async def invalidate_pattern(self, pattern: str = "subtitle:*") -> int:
        """
        Invalidate cache keys matching pattern.

        Args:
            pattern: Redis pattern (e.g., "subtitle:search:*")

        Returns:
            Number of keys deleted
        """
        if not self._redis_client:
            # In-memory: clear all
            count = len(self._memory_cache)
            self._memory_cache.clear()
            logger.info(f"Cache: Cleared {count} in-memory keys")
            return count

        try:
            keys = []
            async for key in self._redis_client.scan_iter(match=pattern):
                keys.append(key)

            if keys:
                deleted = await self._redis_client.delete(*keys)
                logger.info(f"Cache: Invalidated {deleted} keys matching '{pattern}'")
                return deleted

            return 0

        except Exception as e:
            logger.error(f"Cache invalidation error: {e}")
            return 0

    async def get_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        if self._redis_client:
            try:
                info = await self._redis_client.info("stats")
                return {
                    "type": "redis",
                    "connected": True,
                    "keyspace_hits": info.get("keyspace_hits", 0),
                    "keyspace_misses": info.get("keyspace_misses", 0),
                }
            except Exception as e:
                logger.error(f"Failed to get Redis stats: {e}")
                return {"type": "redis", "connected": False, "error": str(e)}
        else:
            return {
                "type": "in-memory",
                "keys_count": len(self._memory_cache),
            }
