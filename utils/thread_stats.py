import asyncio
import logging
from discord import Thread
from typing import Dict, Optional
from datetime import datetime, timedelta
import time

logger = logging.getLogger('discord_bot.thread_stats')

class ThreadStatsCache:
    def __init__(self, ttl: int = 300, cleanup_interval: int = 3600):  # 5 minutes cache, 1 hour cleanup
        self.cache: Dict[int, Dict] = {}
        self.ttl = ttl
        self.last_updated: Dict[int, datetime] = {}
        self.last_cleanup = time.time()
        self.cleanup_interval = cleanup_interval

    def get(self, thread_id: int) -> Optional[Dict]:
        current_time = datetime.now()
        
        # Check if cache cleanup is needed
        if time.time() - self.last_cleanup > self.cleanup_interval:
            self._cleanup_cache()
        
        if thread_id in self.cache:
            if current_time - self.last_updated[thread_id] < timedelta(seconds=self.ttl):
                return self.cache[thread_id]
            else:
                self._remove_entry(thread_id)
        return None

    def set(self, thread_id: int, stats: Dict):
        self.cache[thread_id] = stats
        self.last_updated[thread_id] = datetime.now()

    def _remove_entry(self, thread_id: int):
        """Safely remove cache entry"""
        self.cache.pop(thread_id, None)
        self.last_updated.pop(thread_id, None)

    def _cleanup_cache(self):
        """Clean up expired cache entries"""
        current_time = datetime.now()
        expired_ids = [
            thread_id for thread_id, updated_time in self.last_updated.items()
            if current_time - updated_time >= timedelta(seconds=self.ttl)
        ]
        
        for thread_id in expired_ids:
            self._remove_entry(thread_id)
            
        self.last_cleanup = time.time()
        logger.debug(f"Cache cleanup finished, removed {len(expired_ids)} expired entries")

# Create global cache instance
_stats_cache = ThreadStatsCache()

async def get_thread_stats(thread: Thread) -> dict:
    """
    Get thread statistics, including reaction_count and reply_count.
    Use cache to reduce API calls and optimize data retrieval methods.
    """
    try:
        # Check cache
        cached_stats = _stats_cache.get(thread.id)
        if cached_stats:
            return cached_stats

        stats = {'reaction_count': 0, 'reply_count': 0}
        
        # Use fetch_message to get the first message directly
        try:
            first_message = await thread.fetch_message(thread.id)
            if first_message:
                stats['reaction_count'] = sum(r.count for r in first_message.reactions) if first_message.reactions else 0
        except Exception as e:
            logger.warning(f"Could not get first message for thread {thread.id}: {e}")
            try:
                # If fetching fails, try using history
                async for msg in thread.history(limit=1, oldest_first=True):
                    stats['reaction_count'] = sum(r.count for r in msg.reactions) if msg.reactions else 0
                    break
            except Exception as e2:
                logger.error(f"Could not get history for thread {thread.id}: {e2}")

        # Optimize reply count calculation logic
        try:
            # Prefer using the message_count attribute (if available)
            if hasattr(thread, "message_count") and thread.message_count is not None:
                stats['reply_count'] = max(0, thread.message_count - 1)
            else:
                # Use history counting as a reliable fallback
                count = 0
                async for _ in thread.history(limit=None):
                    count += 1
                stats['reply_count'] = max(0, count - 1)  # Subtract the initial message
        except Exception as e:
            logger.error(f"Error calculating reply count for thread {thread.id}: {e}", exc_info=True)
            stats['reply_count'] = 0

        # Save to cache
        _stats_cache.set(thread.id, stats)
        return stats

    except Exception as e:
        logger.error(f"Error calculating statistics for thread {thread.name} ({thread.id}): {e}", exc_info=True)
        return {'reaction_count': 0, 'reply_count': 0}
