from __future__ import annotations

from dataclasses import dataclass
import threading
import time
from typing import Generic, TypeVar


T = TypeVar('T')


@dataclass
class CacheRecord(Generic[T]):
    value: T
    expires_at: float
    cached_at: float
    ttl_seconds: int


class TTLCache(Generic[T]):
    """Tiny in-memory TTL cache with hit/miss counters for observability."""

    def __init__(self) -> None:
        self._records: dict[str, CacheRecord[T]] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> CacheRecord[T] | None:
        now = time.time()
        with self._lock:
            record = self._records.get(key)
            if record is None:
                self._misses += 1
                return None
            if record.expires_at <= now:
                self._misses += 1
                return None
            self._hits += 1
            return record

    def get_stale(self, key: str) -> CacheRecord[T] | None:
        with self._lock:
            return self._records.get(key)

    def set(self, key: str, value: T, ttl_seconds: int) -> CacheRecord[T]:
        now = time.time()
        record = CacheRecord(value=value, expires_at=now + max(1, ttl_seconds), cached_at=now, ttl_seconds=max(1, ttl_seconds))
        with self._lock:
            self._records[key] = record
        return record

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {'hits': self._hits, 'misses': self._misses, 'size': len(self._records)}

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._hits = 0
            self._misses = 0
