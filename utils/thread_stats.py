import asyncio
import logging
from discord import Thread
from typing import Dict, Optional
from datetime import datetime, timedelta
import time

logger = logging.getLogger('discord_bot.thread_stats')

class ThreadStatsCache:
    def __init__(self, ttl: int = 300, cleanup_interval: int = 3600):  # 5分钟缓存，1小时清理
        self.cache: Dict[int, Dict] = {}
        self.ttl = ttl
        self.last_updated: Dict[int, datetime] = {}
        self.last_cleanup = time.time()
        self.cleanup_interval = cleanup_interval

    def get(self, thread_id: int) -> Optional[Dict]:
        current_time = datetime.now()
        
        # 检查是否需要清理缓存
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
        """安全地移除缓存条目"""
        self.cache.pop(thread_id, None)
        self.last_updated.pop(thread_id, None)

    def _cleanup_cache(self):
        """清理过期的缓存条目"""
        current_time = datetime.now()
        expired_ids = [
            thread_id for thread_id, updated_time in self.last_updated.items()
            if current_time - updated_time >= timedelta(seconds=self.ttl)
        ]
        
        for thread_id in expired_ids:
            self._remove_entry(thread_id)
            
        self.last_cleanup = time.time()
        logger.debug(f"缓存清理完成，移除了 {len(expired_ids)} 个过期条目")

# 创建全局缓存实例
_stats_cache = ThreadStatsCache()

async def get_thread_stats(thread: Thread) -> dict:
    """
    获取线程的统计数据，包括 reaction_count 和 reply_count。
    使用缓存减少API调用，并优化数据获取方式。
    """
    try:
        # 检查缓存
        cached_stats = _stats_cache.get(thread.id)
        if cached_stats:
            return cached_stats

        stats = {'reaction_count': 0, 'reply_count': 0}
        
        # 使用 fetch_message 直接获取第一条消息
        try:
            first_message = await thread.fetch_message(thread.id)
            if first_message:
                stats['reaction_count'] = sum(r.count for r in first_message.reactions) if first_message.reactions else 0
        except Exception as e:
            logger.warning(f"无法获取线程 {thread.id} 的第一条消息: {e}")
            try:
                # 如果获取失败，尝试使用history
                async for msg in thread.history(limit=1, oldest_first=True):
                    stats['reaction_count'] = sum(r.count for r in msg.reactions) if msg.reactions else 0
                    break
            except Exception as e2:
                logger.error(f"无法获取线程 {thread.id} 的历史消息: {e2}")

        # 优化回复数计算逻辑
        try:
            # 首选使用message_count属性（如果可用）
            if hasattr(thread, "message_count") and thread.message_count is not None:
                stats['reply_count'] = max(0, thread.message_count - 1)
            else:
                # 使用history计数作为可靠的备选方案
                count = 0
                async for _ in thread.history(limit=None):
                    count += 1
                stats['reply_count'] = max(0, count - 1)  # 减去初始消息
        except Exception as e:
            logger.error(f"计算线程 {thread.id} 的回复数时出错: {e}", exc_info=True)
            stats['reply_count'] = 0

        # 保存到缓存
        _stats_cache.set(thread.id, stats)
        return stats

    except Exception as e:
        logger.error(f"计算线程 {thread.name} ({thread.id}) 的统计数据时出错: {e}", exc_info=True)
        return {'reaction_count': 0, 'reply_count': 0}
