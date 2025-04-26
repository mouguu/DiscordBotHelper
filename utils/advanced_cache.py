import redis
import pickle
import logging
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import time

class AdvancedCache:
    """Two-layer cache with memory+Redis backends with auto-expiry and stats tracking"""
    
    def __init__(self, use_redis=False, redis_url=None, ttl=300, max_items=10000):
        self._memory_cache = {}
        self._use_redis = use_redis
        self._ttl = ttl
        self._max_items = max_items
        self._logger = logging.getLogger('discord_bot.cache')
        self._lock = asyncio.Lock()
        self._stats = {
            'memory_hits': 0,
            'redis_hits': 0,
            'misses': 0,
            'sets': 0,
            'invalidations': 0,
            'cleanups': 0,
            'items_cleaned': 0,
            'last_cleanup': time.time()
        }
        
        if use_redis:
            try:
                self._redis = redis.from_url(redis_url or "redis://localhost:6379/0")
                self._logger.info(f"[init] Redis cache connected: {redis_url}")
            except Exception as e:
                self._logger.error(f"[boundary:error] Redis connection failed: {e}")
                self._use_redis = False
    
    async def get(self, key: str) -> Optional[Any]:
        """Get cache item or None if missing/expired"""
        async with self._lock:
            current_time = datetime.now().timestamp()
            
            # Try memory cache first
            if key in self._memory_cache:
                item = self._memory_cache[key]
                if current_time - item['timestamp'] < self._ttl:
                    self._stats['memory_hits'] += 1
                    return item['data']
                # Clean expired items
                del self._memory_cache[key]
            
            # Fall back to Redis
            if self._use_redis:
                try:
                    data = self._redis.get(key)
                    if data:
                        self._stats['redis_hits'] += 1
                        decoded_data = pickle.loads(data)
                        # Update memory cache
                        self._memory_cache[key] = {
                            'data': decoded_data,
                            'timestamp': current_time
                        }
                        return decoded_data
                except Exception as e:
                    self._logger.error(f"[boundary:error] Redis read failed for key {key}: {e}")
            
            self._stats['misses'] += 1
            return None
    
    async def set(self, key: str, data: Any) -> None:
        """Store item in both memory and Redis caches"""
        async with self._lock:
            current_time = datetime.now().timestamp()
            
            # Cleanup if at capacity
            if len(self._memory_cache) >= self._max_items:
                await self._cleanup_oldest()
            
            # Update memory cache
            self._memory_cache[key] = {
                'data': data,
                'timestamp': current_time
            }
            
            # Update Redis if enabled
            if self._use_redis:
                try:
                    pickled_data = pickle.dumps(data)
                    self._redis.setex(key, self._ttl, pickled_data)
                except Exception as e:
                    self._logger.error(f"[boundary:error] Redis write failed for key {key}: {e}")
            
            self._stats['sets'] += 1
    
    async def invalidate(self, key: str) -> None:
        """Remove item from both caches"""
        async with self._lock:
            if key in self._memory_cache:
                del self._memory_cache[key]
            
            if self._use_redis:
                try:
                    self._redis.delete(key)
                except Exception as e:
                    self._logger.error(f"[boundary:error] Redis delete failed for key {key}: {e}")
            
            self._stats['invalidations'] += 1
    
    async def invalidate_pattern(self, pattern: str) -> int:
        """Remove all items matching pattern, returns count of removed items"""
        count = 0
        
        # Clean memory cache
        async with self._lock:
            matching_keys = [k for k in self._memory_cache.keys() if pattern in k]
            for key in matching_keys:
                del self._memory_cache[key]
                count += 1
        
        # Clean Redis cache
        if self._use_redis:
            try:
                redis_keys = self._redis.keys(f"*{pattern}*")
                if redis_keys:
                    self._redis.delete(*redis_keys)
                    count += len(redis_keys)
            except Exception as e:
                self._logger.error(f"[boundary:error] Redis pattern delete failed: {e}")
        
        self._stats['invalidations'] += count
        self._logger.info(f"[signal] Pattern '{pattern}' cleanup: {count} items")
        return count
    
    async def cleanup(self) -> int:
        """Remove all expired items, returns count of cleaned items"""
        self._stats['cleanups'] += 1
        self._stats['last_cleanup'] = time.time()
        current_time = datetime.now().timestamp()
        
        # Clean memory cache
        async with self._lock:
            expired_keys = [
                k for k, v in self._memory_cache.items() 
                if current_time - v['timestamp'] >= self._ttl
            ]
            
            for key in expired_keys:
                del self._memory_cache[key]
            
            cleaned_count = len(expired_keys)
            self._stats['items_cleaned'] += cleaned_count
            
            if cleaned_count > 0:
                self._logger.info(f"[signal] Cache cleanup: {cleaned_count} items, size: {len(self._memory_cache)}")
            return cleaned_count
    
    async def _cleanup_oldest(self) -> None:
        """Remove oldest 20% of items when cache is full"""
        items_to_clear = max(int(self._max_items * 0.2), 1)
        
        sorted_items = sorted(
            self._memory_cache.items(), 
            key=lambda x: x[1]['timestamp']
        )
        
        for old_key, _ in sorted_items[:items_to_clear]:
            del self._memory_cache[old_key]
        
        self._stats['items_cleaned'] += items_to_clear
        self._logger.info(f"[signal] Cache eviction: {items_to_clear} oldest items")
    
    async def start_background_cleanup(self, interval=60) -> None:
        """Start periodic cleanup task"""
        self._logger.info(f"[init] Starting cache cleanup, interval: {interval}s")
        
        while True:
            await asyncio.sleep(interval)
            try:
                cleaned = await self.cleanup()
                if cleaned > 0:
                    self._logger.debug(f"[signal] Auto cleanup: {cleaned} items")
            except Exception as e:
                self._logger.error(f"[boundary:error] Cleanup failed: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Return performance statistics"""
        total_requests = self._stats['memory_hits'] + self._stats['redis_hits'] + self._stats['misses']
        hit_rate = ((self._stats['memory_hits'] + self._stats['redis_hits']) / total_requests * 100) if total_requests > 0 else 0
        
        return {
            'memory_size': len(self._memory_cache),
            'memory_limit': self._max_items,
            'memory_usage_pct': (len(self._memory_cache) / self._max_items * 100) if self._max_items > 0 else 0,
            'hit_rate_pct': hit_rate,
            'memory_hits': self._stats['memory_hits'],
            'redis_hits': self._stats['redis_hits'],
            'misses': self._stats['misses'],
            'sets': self._stats['sets'],
            'invalidations': self._stats['invalidations'],
            'cleanups': self._stats['cleanups'],
            'items_cleaned': self._stats['items_cleaned'],
            'last_cleanup_time': datetime.fromtimestamp(self._stats['last_cleanup']).strftime('%Y-%m-%d %H:%M:%S'),
            'use_redis': self._use_redis,
            'ttl': self._ttl
        }


class ThreadCache(AdvancedCache):
    """Thread-specific cache with domain-focused accessors"""
    
    def __init__(self, use_redis=False, redis_url=None, ttl=300, max_items=5000):
        super().__init__(use_redis, redis_url, ttl, max_items)
        self._logger = logging.getLogger('discord_bot.thread_cache')
    
    async def get_thread_stats(self, thread_id: str) -> Optional[Dict]:
        return await self.get(f"thread_stats:{thread_id}")
    
    async def set_thread_stats(self, thread_id: str, stats: Dict) -> None:
        await self.set(f"thread_stats:{thread_id}", stats)
    
    async def invalidate_thread(self, thread_id: str) -> None:
        await self.invalidate_pattern(f":{thread_id}")
    
    async def get_thread_messages(self, thread_id: str, page: int = 0) -> Optional[List]:
        return await self.get(f"thread_messages:{thread_id}:{page}")
    
    async def set_thread_messages(self, thread_id: str, page: int, messages: List) -> None:
        await self.set(f"thread_messages:{thread_id}:{page}", messages)
    
    async def get_forum_threads(self, forum_id: str) -> Optional[List]:
        return await self.get(f"forum_threads:{forum_id}")
    
    async def set_forum_threads(self, forum_id: str, threads: List) -> None:
        await self.set(f"forum_threads:{forum_id}", threads)
    
    async def invalidate_forum(self, forum_id: str) -> None:
        await self.invalidate_pattern(f":{forum_id}") 