import asyncio
import logging
from discord import Thread
from typing import Dict, Optional
from datetime import datetime, timedelta
import time

logger = logging.getLogger('discord_bot.thread_stats')

class ThreadStatsCache:
    def __init__(self, ttl: int = 300, cleanup_interval: int = 3600):
        self.cache: Dict[int, Dict] = {}
        self.ttl = ttl
        self.last_updated: Dict[int, datetime] = {}
        self.last_cleanup = time.time()
        self.cleanup_interval = cleanup_interval

    def get(self, thread_id: int) -> Optional[Dict]:
        current_time = datetime.now()
        
        # Auto-cleanup check
        if time.time() - self.last_cleanup > self.cleanup_interval:
            self._cleanup_cache()
        
        if thread_id in self.cache:
            if current_time - self.last_updated[thread_id] < timedelta(seconds=self.ttl):
                return self.cache[thread_id]
            
            self._remove_entry(thread_id)
        return None

    def set(self, thread_id: int, stats: Dict):
        self.cache[thread_id] = stats
        self.last_updated[thread_id] = datetime.now()

    def _remove_entry(self, thread_id: int):
        self.cache.pop(thread_id, None)
        self.last_updated.pop(thread_id, None)

    def _cleanup_cache(self):
        current_time = datetime.now()
        expired_ids = [
            thread_id for thread_id, updated_time in self.last_updated.items()
            if current_time - updated_time >= timedelta(seconds=self.ttl)
        ]
        
        for thread_id in expired_ids:
            self._remove_entry(thread_id)
            
        self.last_cleanup = time.time()
        
        if expired_ids:
            logger.debug(f"[signal] Cache cleanup removed {len(expired_ids)} entries")

# Global cache instance
_stats_cache = ThreadStatsCache()

async def get_thread_stats(thread: Thread) -> dict:
    """Get thread reaction and reply counts with caching"""
    try:
        # Return from cache if available
        if cached_stats := _stats_cache.get(thread.id):
            return cached_stats

        stats = {'reaction_count': 0, 'reply_count': 0}
        
        # First try direct message fetch (most efficient)
        try:
            first_message = await thread.fetch_message(thread.id)
            if first_message:
                stats['reaction_count'] = sum(r.count for r in first_message.reactions) if first_message.reactions else 0
        except Exception as e:
            logger.warning(f"[boundary:error] First message fetch failed for {thread.id}: {e}")
            # Fall back to history method
            try:
                async for msg in thread.history(limit=1, oldest_first=True):
                    stats['reaction_count'] = sum(r.count for r in msg.reactions) if msg.reactions else 0
                    break
            except Exception as e2:
                logger.error(f"[boundary:error] History fallback failed for {thread.id}: {e2}")

        # Calculate reply count
        try:
            # Use message_count attribute when available
            if hasattr(thread, "message_count") and thread.message_count is not None:
                stats['reply_count'] = max(0, thread.message_count - 1)
            else:
                # Count history as fallback
                count = 0
                async for _ in thread.history(limit=None):
                    count += 1
                stats['reply_count'] = max(0, count - 1)
        except Exception as e:
            logger.error(f"[boundary:error] Reply count failed for {thread.id}: {e}")

        # Save to cache and return
        _stats_cache.set(thread.id, stats)
        return stats

    except Exception as e:
        logger.error(f"[boundary:error] Thread stats calculation failed for {thread.id}: {e}")
        return {'reaction_count': 0, 'reply_count': 0}
