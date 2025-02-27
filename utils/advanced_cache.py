import redis
import pickle
import logging
import asyncio
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import time

class AdvancedCache:
    """高级缓存系统，支持内存缓存和Redis，针对大型服务器优化
    
    这个缓存系统设计用于高负载环境，提供了:
    - 双层缓存结构(内存+Redis)
    - 自动过期和清理机制
    - 缓存统计和监控
    - 异步操作支持
    """
    
    def __init__(self, use_redis=False, redis_url=None, ttl=300, max_items=10000):
        """初始化缓存系统
        
        Args:
            use_redis: 是否使用Redis作为二级缓存
            redis_url: Redis连接URL，默认为localhost:6379/0
            ttl: 缓存项生存时间(秒)
            max_items: 内存缓存最大项数
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
        
        # Redis连接设置
        if use_redis:
            try:
                self._redis = redis.from_url(redis_url or "redis://localhost:6379/0")
                self._logger.info(f"Redis缓存已初始化: {redis_url}")
            except Exception as e:
                self._logger.error(f"Redis连接失败，将使用内存缓存: {e}")
                self._use_redis = False
    
    async def get(self, key: str) -> Optional[Any]:
        """获取缓存项
        
        Args:
            key: 缓存键名
            
        Returns:
            缓存值或None(如果不存在)
        """
        async with self._lock:
            current_time = datetime.now().timestamp()
            
            # 尝试从内存缓存获取
            if key in self._memory_cache:
                item = self._memory_cache[key]
                if current_time - item['timestamp'] < self._ttl:
                    self._stats['memory_hits'] += 1
                    self._logger.debug(f"内存缓存命中: {key}")
                    return item['data']
                else:
                    # 过期数据清理
                    del self._memory_cache[key]
            
            # 如果启用Redis，从Redis获取
            if self._use_redis:
                try:
                    data = self._redis.get(key)
                    if data:
                        self._stats['redis_hits'] += 1
                        self._logger.debug(f"Redis缓存命中: {key}")
                        decoded_data = pickle.loads(data)
                        # 同时更新内存缓存
                        self._memory_cache[key] = {
                            'data': decoded_data,
                            'timestamp': current_time
                        }
                        return decoded_data
                except Exception as e:
                    self._logger.error(f"Redis读取错误: {e}")
            
            self._stats['misses'] += 1
            return None
    
    async def set(self, key: str, data: Any) -> None:
        """设置缓存项
        
        Args:
            key: 缓存键名
            data: 要缓存的数据
        """
        async with self._lock:
            current_time = datetime.now().timestamp()
            
            # 清理检查
            if len(self._memory_cache) >= self._max_items:
                await self._cleanup_oldest()
            
            # 更新内存缓存
            self._memory_cache[key] = {
                'data': data,
                'timestamp': current_time
            }
            
            # 如果启用Redis，同时更新Redis
            if self._use_redis:
                try:
                    pickled_data = pickle.dumps(data)
                    self._redis.setex(key, self._ttl, pickled_data)
                except Exception as e:
                    self._logger.error(f"Redis写入错误: {e}")
            
            self._stats['sets'] += 1
    
    async def invalidate(self, key: str) -> None:
        """使缓存项失效
        
        Args:
            key: 要失效的缓存键名
        """
        async with self._lock:
            if key in self._memory_cache:
                del self._memory_cache[key]
            
            if self._use_redis:
                try:
                    self._redis.delete(key)
                except Exception as e:
                    self._logger.error(f"Redis删除错误: {e}")
            
            self._stats['invalidations'] += 1
    
    async def invalidate_pattern(self, pattern: str) -> int:
        """使匹配模式的所有缓存项失效
        
        Args:
            pattern: 匹配模式(如'user_*'删除所有以user_开头的键)
            
        Returns:
            删除的项数
        """
        count = 0
        
        # 清理内存缓存
        async with self._lock:
            matching_keys = [k for k in self._memory_cache.keys() if pattern in k]
            for key in matching_keys:
                del self._memory_cache[key]
                count += 1
        
        # 清理Redis缓存
        if self._use_redis:
            try:
                # 查找匹配的Redis键
                redis_keys = self._redis.keys(f"*{pattern}*")
                if redis_keys:
                    self._redis.delete(*redis_keys)
                    count += len(redis_keys)
            except Exception as e:
                self._logger.error(f"Redis模式删除错误: {e}")
        
        self._stats['invalidations'] += count
        self._logger.info(f"模式'{pattern}'缓存清理: {count}项")
        return count
    
    async def cleanup(self) -> int:
        """清理过期项
        
        Returns:
            清理的项数
        """
        self._stats['cleanups'] += 1
        self._stats['last_cleanup'] = time.time()
        current_time = datetime.now().timestamp()
        
        # 清理内存缓存
        async with self._lock:
            expired_keys = [
                k for k, v in self._memory_cache.items() 
                if current_time - v['timestamp'] >= self._ttl
            ]
            
            for key in expired_keys:
                del self._memory_cache[key]
            
            cleaned_count = len(expired_keys)
            self._stats['items_cleaned'] += cleaned_count
            
            self._logger.info(f"缓存清理: 移除了 {cleaned_count} 个过期项，当前缓存大小: {len(self._memory_cache)}")
            return cleaned_count
    
    async def _cleanup_oldest(self) -> None:
        """清理最旧的缓存项"""
        # 计算要清理的数量(最老的20%)
        items_to_clear = max(int(self._max_items * 0.2), 1)
        
        # 按时间戳排序
        sorted_items = sorted(
            self._memory_cache.items(), 
            key=lambda x: x[1]['timestamp']
        )
        
        # 删除最老的项
        for old_key, _ in sorted_items[:items_to_clear]:
            del self._memory_cache[old_key]
        
        self._stats['items_cleaned'] += items_to_clear
        self._logger.info(f"缓存空间清理: 移除了 {items_to_clear} 项最老的缓存")
    
    async def start_background_cleanup(self, interval=60) -> None:
        """启动定期后台清理任务
        
        Args:
            interval: 清理间隔(秒)
        """
        self._logger.info(f"启动缓存后台清理，间隔: {interval}秒")
        
        while True:
            await asyncio.sleep(interval)
            try:
                cleaned = await self.cleanup()
                self._logger.debug(f"自动缓存清理完成: {cleaned}项")
            except Exception as e:
                self._logger.error(f"缓存清理错误: {e}")
    
    def get_stats(self) -> Dict[str, Any]:
        """获取缓存统计信息
        
        Returns:
            包含各种统计数据的字典
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
            'last_cleanup': datetime.fromtimestamp(self._stats['last_cleanup']).strftime('%Y-%m-%d %H:%M:%S'),
            'use_redis': self._use_redis,
            'ttl': self._ttl
        }


class ThreadCache(AdvancedCache):
    """专用于线程数据的缓存管理器"""
    
    def __init__(self, use_redis=False, redis_url=None, ttl=300, max_items=5000):
        super().__init__(use_redis, redis_url, ttl, max_items)
        self._logger = logging.getLogger('discord_bot.thread_cache')
    
    async def get_thread_stats(self, thread_id: str) -> Optional[Dict]:
        """获取线程统计信息，带缓存
        
        Args:
            thread_id: Discord线程ID
            
        Returns:
            线程统计信息或None
        """
        cache_key = f"thread_stats_{thread_id}"
        return await self.get(cache_key)
    
    async def set_thread_stats(self, thread_id: str, stats: Dict) -> None:
        """缓存线程统计信息
        
        Args:
            thread_id: Discord线程ID
            stats: 线程统计数据
        """
        cache_key = f"thread_stats_{thread_id}"
        await self.set(cache_key, stats)
    
    async def invalidate_thread(self, thread_id: str) -> None:
        """使线程相关缓存失效
        
        Args:
            thread_id: Discord线程ID
        """
        # 清除所有与此线程相关的缓存
        await self.invalidate_pattern(f"thread_{thread_id}")
    
    async def get_thread_messages(self, thread_id: str, page: int = 0) -> Optional[List]:
        """获取线程消息，带分页缓存
        
        Args:
            thread_id: Discord线程ID
            page: 页码
            
        Returns:
            消息列表或None
        """
        cache_key = f"thread_msgs_{thread_id}_p{page}"
        return await self.get(cache_key)
    
    async def set_thread_messages(self, thread_id: str, page: int, messages: List) -> None:
        """缓存线程消息
        
        Args:
            thread_id: Discord线程ID
            page: 页码
            messages: 消息列表
        """
        cache_key = f"thread_msgs_{thread_id}_p{page}"
        await self.set(cache_key, messages)
    
    async def get_forum_threads(self, forum_id: str) -> Optional[List]:
        """获取论坛线程列表缓存
        
        Args:
            forum_id: Discord论坛ID
            
        Returns:
            线程列表或None
        """
        cache_key = f"forum_threads_{forum_id}"
        return await self.get(cache_key)
    
    async def set_forum_threads(self, forum_id: str, threads: List) -> None:
        """缓存论坛线程列表
        
        Args:
            forum_id: Discord论坛ID
            threads: 线程列表
        """
        cache_key = f"forum_threads_{forum_id}"
        await self.set(cache_key, threads)
    
    async def invalidate_forum(self, forum_id: str) -> None:
        """使论坛相关缓存失效
        
        Args:
            forum_id: Discord论坛ID
        """
        await self.invalidate_pattern(f"forum_{forum_id}") 