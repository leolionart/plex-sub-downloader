import concurrent.futures
import time
from app.utils.ttl_cache import TTLCache


def test_ttl_cache_basic_ops():
    cache = TTLCache(default_ttl=60, maxsize=10)
    cache.set("a", 1)
    cache.set("b", 2)

    assert cache.get("a") == 1
    assert cache.get("b") == 2
    assert cache.get("c") is None
    assert cache.get("c", default="fallback") == "fallback"

    assert "a" in cache
    assert "c" not in cache
    assert len(cache) == 2

    assert cache.delete("a") is True
    assert "a" not in cache
    assert cache.get("a") is None
    assert cache.delete("a") is False

    assert cache.pop("b") == 2
    assert cache.pop("b", "default") == "default"
    assert len(cache) == 0


def test_ttl_cache_expiration():
    cache = TTLCache(default_ttl=0.1, maxsize=10)
    cache.set("short", "hello")
    cache.set("long", "world", ttl=2.0)

    assert cache.get("short") == "hello"
    assert cache.get("long") == "world"

    time.sleep(0.15)

    assert cache.get("short") is None
    assert "short" not in cache
    assert cache.get("long") == "world"
    assert "long" in cache


def test_ttl_cache_lru_eviction():
    cache = TTLCache(default_ttl=60, maxsize=3)
    cache.set("k1", 1)
    cache.set("k2", 2)
    cache.set("k3", 3)

    # Access k1 so k2 becomes the least recently used
    assert cache.get("k1") == 1

    # Adding k4 should evict k2
    cache.set("k4", 4)

    assert "k1" in cache
    assert "k2" not in cache
    assert "k3" in cache
    assert "k4" in cache
    assert len(cache) == 3

    stats = cache.stats()
    assert stats["evictions"] == 1
    assert stats["hits"] >= 1


def test_ttl_cache_dict_protocol():
    cache = TTLCache(default_ttl=60, maxsize=5)
    cache["x"] = 100
    cache["y"] = 200

    assert "x" in cache
    assert cache["x"] == 100
    assert cache.items() == [("x", 100), ("y", 200)] or sorted(cache.items()) == [("x", 100), ("y", 200)]
    assert set(cache.keys()) == {"x", "y"}
    assert set(cache.values()) == {100, 200}

    del cache["x"]
    assert "x" not in cache


def test_ttl_cache_invalidate_predicate():
    cache = TTLCache(default_ttl=60, maxsize=10)
    cache.set(("show", "101"), {"title": "Show A"})
    cache.set(("show", "102"), {"title": "Show B"})
    cache.set(("movie", "201"), {"title": "Movie A"})

    # Invalidate all keys with prefix "show"
    removed = cache.invalidate_predicate(lambda k, v: isinstance(k, tuple) and k[0] == "show")
    assert removed == 2
    assert ("show", "101") not in cache
    assert ("show", "102") not in cache
    assert ("movie", "201") in cache


def test_ttl_cache_copy_on_read():
    cache = TTLCache(default_ttl=60, maxsize=10, copy_on_read=True)
    orig = {"items": [1, 2, 3]}
    cache.set("data", orig)

    read1 = cache.get("data")
    read1["items"].append(4)

    read2 = cache.get("data")
    assert read2["items"] == [1, 2, 3]


def test_ttl_cache_thread_safety():
    cache = TTLCache(default_ttl=60, maxsize=50)

    def worker(worker_id: int):
        for i in range(100):
            key = f"key_{i % 30}"
            cache.set(key, {"worker": worker_id, "val": i})
            val = cache.get(key)
            if i % 10 == 0:
                cache.invalidate_predicate(lambda k, v: k == f"key_{i % 30}")
            if i % 20 == 0:
                cache.delete(key)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, i) for i in range(8)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    assert cache.stats()["size"] <= 50
