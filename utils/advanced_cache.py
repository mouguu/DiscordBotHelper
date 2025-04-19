import redis
import pickle
import logging
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import time

class AdvancedCache:
    """Advanced caching system, supports memory cache and Redis, optimized for large servers
    
    This caching system is designed for high-load environments, providing:
    - Two-layer cache structure (Memory + Redis)
    - Automatic expiration and cleanup mechanisms
    - Cache statistics and monitoring
    - Asynchronous operation support
    """
    
    def __init__(self, use_redis=False, redis_url=None, ttl=300, max_items=10000):
        """Initialize the cache system
        
        Args:
            use_redis: Whether to use Redis as a secondary cache
            redis_url: Redis connection URL, defaults to localhost:6379/0
            ttl: Cache item time-to-live (seconds)
            max_items: Maximum number of items in memory cache
        """
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
        
        # Redis connection settings
        if use_redis:
            try:
                self._redis = redis.from_url(redis_url or "redis://localhost:6379/0")
                self._logger.info(f"Redis cache initialized: {redis_url}")
            except Exception as e:
                self._logger.error(f"Redis connection failed, will use memory cache: {e}")
                self._use_redis = False
    
    async def get(self, key: str) -> Optional[Any]:
        """Get cache item
        
        Args:
            key: Cache key name
            
        Returns:
            Cache value or None (if it doesn't exist)
        """
        async with self._lock:
            current_time = datetime.now().timestamp()
            
            # Try to get from memory cache
            if key in self._memory_cache:
                item = self._memory_cache[key]
                if current_time - item['timestamp'] < self._ttl:
                    self._stats['memory_hits'] += 1
                    self._logger.debug(f"Memory cache hit: {key}")
                    return item['data']
                else:
                    # Expired data cleanup
                    del self._memory_cache[key]
            
            # If Redis is enabled, get from Redis
            if self._use_redis:
                try:
                    data = self._redis.get(key)
                    if data:
                        self._stats['redis_hits'] += 1
                        self._logger.debug(f"Redis cache hit: {key}")
                        decoded_data = pickle.loads(data)
                        # Update memory cache at the same time
                        self._memory_cache[key] = {
                            'data': decoded_data,
                            'timestamp': current_time
                        }
                        return decoded_data
                except Exception as e:
                    self._logger.error(f"Redis read error: {e}")
            
            self._stats['misses'] += 1
            return None
    
    async def set(self, key: str, data: Any) -> None:
        """Set cache item
        
        Args:
            key: Cache key name
            data: Data to cache
        """
        async with self._lock:
            current_time = datetime.now().timestamp()
            
            # Cleanup check
            if len(self._memory_cache) >= self._max_items:
                await self._cleanup_oldest()
            
            # Update memory cache
            self._memory_cache[key] = {
                'data': data,
                'timestamp': current_time
            }
            
            # If Redis is enabled, update Redis at the same time
            if self._use_redis:
                try:
                    pickled_data = pickle.dumps(data)
                    self._redis.setex(key, self._ttl, pickled_data)
                except Exception as e:
                    self._logger.error(f"Redis write error: {e}")
            
            self._stats['sets'] += 1
    
    async def invalidate(self, key: str) -> None:
        """Invalidate cache item
        
        Args:
            key: Cache key name to invalidate
        """
        async with self._lock:
            if key in self._memory_cache:
                del self._memory_cache[key]
            
            if self._use_redis:
                try:
                    self._redis.delete(key)
                except Exception as e:
                    self._logger.error(f"Redis delete error: {e}")
            
            self._stats['invalidations'] += 1
    
    async def invalidate_pattern(self, pattern: str) -> int:
        """Invalidate all cache items matching a pattern
        
        Args:
            pattern: Matching pattern (e.g., 'user_*' deletes all keys starting with user_)
            
        Returns:
            Number of items deleted
        """
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
                # Find matching Redis keys
                redis_keys = self._redis.keys(f"*{pattern}*")
                if redis_keys:
                    self._redis.delete(*redis_keys)
                    count += len(redis_keys)
            except Exception as e:
                self._logger.error(f"Redis pattern delete error: {e}")
        
        self._stats['invalidations'] += count
        self._logger.info(f"Cache cleanup for pattern '{pattern}': {count} items")
        return count
    
    async def cleanup(self) -> int:
        """Clean up expired items
        
        Returns:
            Number of items cleaned
        """
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
            
            self._logger.info(f"Cache cleanup: Removed {cleaned_count} expired items, current cache size: {len(self._memory_cache)}")
            return cleaned_count
    
    async def _cleanup_oldest(self) -> None:
        """Clean up the oldest cache items"""
        # Calculate the number to clean (oldest 20%)
        items_to_clear = max(int(self._max_items * 0.2), 1)
        
        # Sort by timestamp
        sorted_items = sorted(
            self._memory_cache.items(), 
            key=lambda x: x[1]['timestamp']
        )
        
        # Delete the oldest item
        for old_key, _ in sorted_items[:items_to_clear]:
            del self._memory_cache[old_key]
        
        self._stats['items_cleaned'] += items_to_clear
        self._logger.info(f"Cache space cleanup: Removed {items_to_clear} oldest cache items")
    
    async def start_background_cleanup(self, interval=60) -> None:
        """Start periodic background cleanup task
        
        Args:
            interval: Cleanup interval (seconds)
        """
        self._logger.info(f"Starting cache background cleanup, interval: {interval} seconds")
        
        while True:
            await asyncio.sleep(interval)
            try:
                cleaned = await self.cleanup()
                self._logger.debug(f"Automatic cache cleanup finished: {cleaned} items")
            except Exception as e:
                self._logger.error(f"Cache cleanup error: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics information
        
        Returns:
            Dictionary containing various statistical data
        """
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
    """Dedicated cache for thread data, inherits from AdvancedCache"""
    
    def __init__(self, use_redis=False, redis_url=None, ttl=300, max_items=5000):
        super().__init__(use_redis, redis_url, ttl, max_items)
        self._logger = logging.getLogger('discord_bot.thread_cache')
    
    async def get_thread_stats(self, thread_id: str) -> Optional[Dict]:
        """Get thread statistics cache"""
        cache_key = f"thread_stats:{thread_id}"
        return await self.get(cache_key)
    
    async def set_thread_stats(self, thread_id: str, stats: Dict) -> None:
        """Set thread statistics cache"""
        cache_key = f"thread_stats:{thread_id}"
        await self.set(cache_key, stats)
    
    async def invalidate_thread(self, thread_id: str) -> None:
        """Invalidate all related caches for a single thread"""
        await self.invalidate_pattern(f":{thread_id}")
    
    async def get_thread_messages(self, thread_id: str, page: int = 0) -> Optional[List]:
        """Get thread message pagination cache"""
        cache_key = f"thread_messages:{thread_id}:{page}"
        return await self.get(cache_key)
    
    async def set_thread_messages(self, thread_id: str, page: int, messages: List) -> None:
        """Set thread message pagination cache"""
        cache_key = f"thread_messages:{thread_id}:{page}"
        await self.set(cache_key, messages)
    
    async def get_forum_threads(self, forum_id: str) -> Optional[List]:
        """Get forum thread list cache"""
        cache_key = f"forum_threads:{forum_id}"
        return await self.get(cache_key)
    
    async def set_forum_threads(self, forum_id: str, threads: List) -> None:
        """Set forum thread list cache"""
        cache_key = f"forum_threads:{forum_id}"
        await self.set(cache_key, threads)
    
    async def invalidate_forum(self, forum_id: str) -> None:
        """Invalidate all related caches for a single forum"""
        await self.invalidate_pattern(f":{forum_id}") 