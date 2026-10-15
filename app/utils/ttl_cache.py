"""
Thread-safe in-memory TTL (Time-To-Live) cache with LRU eviction.
Dùng để cache các truy vấn nặng tới Plex Server (show tree, thư viện, media details)
nhằm chống giật lag và giảm thiểu queries trùng lặp.
"""

from collections import OrderedDict
import copy
import threading
import time
from typing import Any, Callable, Hashable, Iterator


class TTLCache:
    """
    Thread-safe in-memory cache supporting Time-To-Live (TTL) and LRU eviction.
    """

    def __init__(
        self,
        default_ttl: float = 300.0,
        maxsize: int = 256,
        copy_on_read: bool = False,
    ) -> None:
        """
        Initialize TTLCache.

        Args:
            default_ttl: Expiration duration in seconds (default 300s = 5m).
                         0 or negative means no automatic expiration.
            maxsize: Maximum number of entries allowed before LRU eviction.
            copy_on_read: If True, returns deepcopy of cached items on get to prevent
                          external mutations from altering the cache.
        """
        if maxsize <= 0:
            raise ValueError("maxsize must be greater than 0")

        self.default_ttl = float(default_ttl)
        self.maxsize = int(maxsize)
        self.copy_on_read = copy_on_read

        self._store: OrderedDict[Hashable, tuple[Any, float]] = OrderedDict()
        self._lock = threading.RLock()

        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def _is_expired(self, expire_at: float, now: float) -> bool:
        if expire_at <= 0:
            return False
        return now >= expire_at

    def get(self, key: Hashable, default: Any = None) -> Any:
        """
        Retrieve value for key if present and not expired.

        Moves key to end (MRU) upon hit.
        """
        now = time.time()
        with self._lock:
            if key not in self._store:
                self._misses += 1
                return default

            value, expire_at = self._store[key]
            if self._is_expired(expire_at, now):
                del self._store[key]
                self._misses += 1
                return default

            self._store.move_to_end(key, last=True)
            self._hits += 1

            if self.copy_on_read:
                return copy.deepcopy(value)
            return value

    def set(self, key: Hashable, value: Any, ttl: float | None = None) -> None:
        """
        Store key-value pair with optional custom TTL.

        Evicts expired items or the least recently used item if maxsize is reached.
        """
        now = time.time()
        effective_ttl = self.default_ttl if ttl is None else float(ttl)
        expire_at = (now + effective_ttl) if effective_ttl > 0 else 0.0

        with self._lock:
            if key in self._store:
                self._store[key] = (value, expire_at)
                self._store.move_to_end(key, last=True)
                return

            if len(self._store) >= self.maxsize:
                # First attempt: evict any expired items
                self._purge_expired_locked(now)

            if len(self._store) >= self.maxsize:
                # Evict oldest / LRU item
                self._store.popitem(last=False)
                self._evictions += 1

            self._store[key] = (value, expire_at)

    def delete(self, key: Hashable) -> bool:
        """Remove key from cache. Returns True if key was present, False otherwise."""
        with self._lock:
            if key in self._store:
                del self._store[key]
                return True
            return False

    def pop(self, key: Hashable, default: Any = None) -> Any:
        """Remove key from cache and return its value if unexpired, else default."""
        now = time.time()
        with self._lock:
            if key not in self._store:
                return default
            value, expire_at = self._store.pop(key)
            if self._is_expired(expire_at, now):
                return default
            return value

    def clear(self) -> None:
        """Remove all entries from the cache."""
        with self._lock:
            self._store.clear()

    def invalidate_predicate(self, predicate: Callable[[Hashable, Any], bool]) -> int:
        """
        Remove all entries whose (key, value) satisfy predicate.

        Returns count of removed items.
        """
        removed = 0
        now = time.time()
        with self._lock:
            keys_to_remove = []
            for k, (v, exp) in self._store.items():
                if self._is_expired(exp, now):
                    keys_to_remove.append(k)
                    continue
                try:
                    if predicate(k, v):
                        keys_to_remove.append(k)
                except Exception:
                    pass

            for k in keys_to_remove:
                if k in self._store:
                    del self._store[k]
                    removed += 1
        return removed

    def cleanup_expired(self) -> int:
        """Purge all expired entries. Returns count of purged entries."""
        now = time.time()
        with self._lock:
            return self._purge_expired_locked(now)

    def _purge_expired_locked(self, now: float) -> int:
        expired_keys = [k for k, (_, exp) in self._store.items() if self._is_expired(exp, now)]
        for k in expired_keys:
            del self._store[k]
        return len(expired_keys)

    def items(self) -> list[tuple[Hashable, Any]]:
        """Return list of (key, value) for all valid unexpired entries."""
        now = time.time()
        with self._lock:
            result = []
            expired_keys = []
            for k, (v, exp) in self._store.items():
                if self._is_expired(exp, now):
                    expired_keys.append(k)
                else:
                    result.append((k, v))
            for k in expired_keys:
                del self._store[k]
            return result

    def keys(self) -> list[Hashable]:
        """Return list of all valid unexpired keys."""
        return [k for k, _ in self.items()]

    def values(self) -> list[Any]:
        """Return list of all valid unexpired values."""
        return [v for _, v in self.items()]

    def stats(self) -> dict[str, Any]:
        """Return cache runtime statistics."""
        with self._lock:
            total_requests = self._hits + self._misses
            hit_ratio = (self._hits / total_requests) if total_requests > 0 else 0.0
            return {
                "size": len(self._store),
                "maxsize": self.maxsize,
                "default_ttl": self.default_ttl,
                "hits": self._hits,
                "misses": self._misses,
                "evictions": self._evictions,
                "hit_ratio": round(hit_ratio, 4),
            }

    # Dict-like protocol support
    def __contains__(self, key: Hashable) -> bool:
        now = time.time()
        with self._lock:
            if key not in self._store:
                return False
            _, expire_at = self._store[key]
            if self._is_expired(expire_at, now):
                del self._store[key]
                return False
            return True

    def __getitem__(self, key: Hashable) -> Any:
        val = self.get(key)
        if val is None and key not in self:
            raise KeyError(key)
        return val

    def __setitem__(self, key: Hashable, value: Any) -> None:
        self.set(key, value)

    def __delitem__(self, key: Hashable) -> None:
        if not self.delete(key):
            raise KeyError(key)

    def __len__(self) -> int:
        with self._lock:
            now = time.time()
            return sum(1 for _, (_, exp) in self._store.items() if not self._is_expired(exp, now))

    def __iter__(self) -> Iterator[Hashable]:
        return iter(self.keys())
