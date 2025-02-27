# 大型服务器性能优化指南

本文档提供了针对大型Discord服务器(10,000+用户，大量帖子)的性能优化建议，帮助您的Discord机器人保持高效运行。

## 缓存优化

### 线程缓存优化

当前的`ThreadCache`类在大型服务器中可能成为性能瓶颈。建议以下优化：

```python
# 在utils目录下创建advanced_cache.py
import redis
import pickle
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timedelta

class AdvancedCache:
    """高级缓存系统，支持内存缓存和Redis"""
    
    def __init__(self, use_redis=False, redis_url=None, ttl=300, max_items=10000):
        self._memory_cache = {}
        self._use_redis = use_redis
        self._ttl = ttl
        self._max_items = max_items
        self._logger = logging.getLogger('discord_bot.cache')
        
        # Redis连接设置
        if use_redis:
            try:
                self._redis = redis.from_url(redis_url or "redis://localhost:6379/0")
                self._logger.info(f"Redis缓存已初始化: {redis_url}")
            except Exception as e:
                self._logger.error(f"Redis连接失败，将使用内存缓存: {e}")
                self._use_redis = False
    
    async def get(self, key: str) -> Optional[Any]:
        """获取缓存项"""
        current_time = datetime.now().timestamp()
        
        # 尝试从内存缓存获取
        if key in self._memory_cache:
            item = self._memory_cache[key]
            if current_time - item['timestamp'] < self._ttl:
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
        
        return None
    
    async def set(self, key: str, data: Any) -> None:
        """设置缓存项"""
        current_time = datetime.now().timestamp()
        
        # 内存缓存限制
        if len(self._memory_cache) >= self._max_items:
            # 清理最老的20%缓存项
            items_to_clear = int(self._max_items * 0.2)
            sorted_items = sorted(
                self._memory_cache.items(), 
                key=lambda x: x[1]['timestamp']
            )
            for old_key, _ in sorted_items[:items_to_clear]:
                del self._memory_cache[old_key]
            
            self._logger.info(f"缓存清理: 移除了 {items_to_clear} 项")
        
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
    
    async def invalidate(self, key: str) -> None:
        """使缓存项失效"""
        if key in self._memory_cache:
            del self._memory_cache[key]
        
        if self._use_redis:
            try:
                self._redis.delete(key)
            except Exception as e:
                self._logger.error(f"Redis删除错误: {e}")
    
    async def cleanup(self) -> int:
        """清理过期项，返回清理数量"""
        current_time = datetime.now().timestamp()
        expired_keys = [
            k for k, v in self._memory_cache.items() 
            if current_time - v['timestamp'] >= self._ttl
        ]
        
        for key in expired_keys:
            del self._memory_cache[key]
        
        self._logger.info(f"缓存清理: 移除了 {len(expired_keys)} 个过期项")
        return len(expired_keys)
```

## 数据加载和处理优化

### 消息加载策略

对于大型服务器，消息加载是最大的性能瓶颈之一。建议采用以下策略：

1. **增量加载**：不要一次加载所有消息，使用游标分页实现增量加载

2. **修改搜索.py中的_search_thread方法**：

```python
async def _search_thread(self, thread, search_conditions, message_limit=None):
    """优化的线程搜索方法，使用游标分页"""
    message_limit = message_limit or MAX_MESSAGES_PER_SEARCH
    batch_size = 100  # Discord API默认批量大小
    
    matched_messages = []
    last_message_id = None
    total_processed = 0
    
    while total_processed < message_limit:
        # 使用last_message_id作为游标
        current_batch_size = min(batch_size, message_limit - total_processed)
        batch = []
        
        try:
            async for message in thread.history(limit=current_batch_size, before=last_message_id):
                batch.append(message)
                
            if not batch:
                break  # 没有更多消息
                
            # 更新游标
            last_message_id = batch[-1].id
            total_processed += len(batch)
            
            # 处理当前批次
            for message in batch:
                if self._check_message_match(message, search_conditions):
                    matched_messages.append(message)
                    
                    # 如果只需要找到匹配项，可以提前返回
                    if search_conditions.get('limit_results', False) and len(matched_messages) >= search_conditions.get('max_results', 10):
                        return matched_messages
                        
        except discord.errors.Forbidden:
            self._logger.warning(f"没有权限读取线程 {thread.name} (ID: {thread.id})")
            break
        except Exception as e:
            self._logger.error(f"搜索线程时出错 {thread.name}: {e}", exc_info=True)
            break
            
    return matched_messages
```

## 并发控制

### 搜索任务限流

大型服务器中可能同时有多个用户使用搜索功能，需要实现更严格的并发控制：

```python
# 在config.py中添加
GUILD_CONCURRENT_SEARCHES = 3  # 每个服务器同时进行的搜索数量
USER_SEARCH_COOLDOWN = 60      # 用户搜索冷却时间(秒)

# 在search.py中添加
from collections import defaultdict
import time

class SearchLimiter:
    """搜索限流器"""
    
    def __init__(self, max_guild_searches=3, user_cooldown=60):
        self._guild_searches = defaultdict(int)
        self._user_timestamps = {}
        self._max_guild_searches = max_guild_searches
        self._user_cooldown = user_cooldown
        self._lock = asyncio.Lock()
        
    async def can_search(self, guild_id, user_id):
        """检查是否允许搜索"""
        async with self._lock:
            current_time = time.time()
            
            # 检查用户冷却
            if user_id in self._user_timestamps:
                elapsed = current_time - self._user_timestamps[user_id]
                if elapsed < self._user_cooldown:
                    return False, f"命令冷却中，请在 {self._user_cooldown - elapsed:.1f} 秒后重试"
            
            # 检查服务器并发限制
            if self._guild_searches[guild_id] >= self._max_guild_searches:
                return False, f"服务器搜索已达到上限 ({self._max_guild_searches})，请稍后再试"
                
            return True, ""
            
    async def register_search(self, guild_id, user_id):
        """注册新搜索"""
        async with self._lock:
            self._guild_searches[guild_id] += 1
            self._user_timestamps[user_id] = time.time()
            
    async def release_search(self, guild_id):
        """释放搜索资源"""
        async with self._lock:
            if self._guild_searches[guild_id] > 0:
                self._guild_searches[guild_id] -= 1
```

## 内存优化

### 消息内容优化

Discord消息可能包含大量文本、附件和嵌入内容，这会占用大量内存。建议优化消息处理：

```python
def optimize_message_content(message):
    """优化消息内容以减少内存使用"""
    # 创建一个轻量级消息对象
    optimized = {
        'id': message.id,
        'author_id': message.author.id,
        'author_name': message.author.display_name,
        'content': message.content[:2000] if message.content else "",  # 限制内容长度
        'created_at': message.created_at,
        'has_attachments': bool(message.attachments),
        'attachment_count': len(message.attachments),
        'attachment_preview': [{'filename': a.filename, 'url': a.url} for a in message.attachments[:3]]
    }
    
    # 仅保留必要的反应信息
    if message.reactions:
        optimized['reactions'] = [
            {'emoji': str(r.emoji), 'count': r.count} 
            for r in message.reactions[:10]  # 限制反应数量
        ]
    
    return optimized
```

## 数据库集成

对于真正大型的服务器，建议实现数据库索引以加快搜索：

```python
# 示例:SQLite集成

import aiosqlite
import json

class MessageDatabase:
    """消息数据库索引"""
    
    def __init__(self, db_path="messages.db"):
        self._db_path = db_path
        self._logger = logging.getLogger('discord_bot.database')
    
    async def initialize(self):
        """初始化数据库"""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    guild_id TEXT,
                    channel_id TEXT,
                    thread_id TEXT,
                    author_id TEXT,
                    content TEXT,
                    created_at TIMESTAMP,
                    metadata TEXT
                )
            ''')
            
            # 创建索引
            await db.execute('CREATE INDEX IF NOT EXISTS idx_thread_id ON messages(thread_id)')
            await db.execute('CREATE INDEX IF NOT EXISTS idx_content ON messages(content)')
            await db.commit()
            
    async def index_message(self, message):
        """索引消息"""
        try:
            metadata = {
                'author_name': message.author.display_name,
                'has_attachments': bool(message.attachments),
                'reaction_count': sum(r.count for r in message.reactions) if message.reactions else 0
            }
            
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute('''
                    INSERT OR REPLACE INTO messages 
                    (id, guild_id, channel_id, thread_id, author_id, content, created_at, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    str(message.id),
                    str(message.guild.id),
                    str(message.channel.id),
                    str(message.thread.id) if hasattr(message, 'thread') and message.thread else None,
                    str(message.author.id),
                    message.content,
                    message.created_at.isoformat(),
                    json.dumps(metadata)
                ))
                await db.commit()
        except Exception as e:
            self._logger.error(f"索引消息失败: {e}")
    
    async def search_messages(self, thread_id, query, limit=100):
        """搜索消息"""
        try:
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute('''
                    SELECT * FROM messages 
                    WHERE thread_id = ? AND content LIKE ? 
                    ORDER BY created_at DESC
                    LIMIT ?
                ''', (str(thread_id), f'%{query}%', limit))
                
                results = await cursor.fetchall()
                return [dict(row) for row in results]
        except Exception as e:
            self._logger.error(f"搜索消息失败: {e}")
            return []
```

## 分页性能优化

针对大型搜索结果的分页优化：

```python
# 修改pagination.py中的MultiEmbedPaginationView类

def __init__(self, items, items_per_page, generate_embeds, timeout=900.0):
    super().__init__(timeout=timeout)
    
    # 仅存储当前页面和周围页面的数据
    self.all_items = None  # 不存储完整项目列表
    self.current_page_items = []
    self.item_ids = [str(getattr(item, 'id', i)) for i, item in enumerate(items)]
    self.items_per_page = items_per_page
    self.generate_embeds = generate_embeds
    self.current_page = 0
    self.total_items = len(items)
    self.total_pages = max((self.total_items + items_per_page - 1) // items_per_page, 1)
    
    # 初始化第一页
    start_idx = 0
    end_idx = min(items_per_page, self.total_items)
    self.current_page_items = items[start_idx:end_idx]

# 修改get_page_items方法
async def get_page_items(self, page, items_provider=None):
    """获取指定页面的项目，可通过回调函数按需加载"""
    if page < 0 or page >= self.total_pages:
        return []
    
    # 如果当前页面已缓存且请求的是当前页
    if page == self.current_page and self.current_page_items:
        return self.current_page_items
    
    # 通过回调函数按需加载
    if items_provider:
        start_idx = page * self.items_per_page
        self.current_page = page
        self.current_page_items = await items_provider(
            start_idx, 
            min(start_idx + self.items_per_page, self.total_items)
        )
        return self.current_page_items
    
    # 兜底返回空列表
    return []
```

## 监控和日志优化

创建性能监控视图：

```python
# 添加到cogs目录: stats.py

import discord
from discord.ext import commands
from discord import app_commands
import psutil
import time
import asyncio
import platform
from datetime import datetime, timedelta

class Stats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._start_time = time.time()
        self._command_usage = {}
        self._search_stats = {
            'total_searches': 0,
            'successful_searches': 0,
            'failed_searches': 0,
            'avg_search_time': 0,
            'total_search_time': 0
        }
    
    @app_commands.command(name="bot_stats", description="显示机器人性能统计")
    async def bot_stats(self, interaction: discord.Interaction):
        """显示机器人性能统计"""
        # 系统信息
        process = psutil.Process()
        memory_usage = process.memory_info().rss / (1024 * 1024)  # MB
        cpu_percent = process.cpu_percent() / psutil.cpu_count()
        uptime = timedelta(seconds=int(time.time() - self._start_time))
        
        # 统计数据
        guild_count = len(self.bot.guilds)
        user_count = sum(g.member_count for g in self.bot.guilds)
        
        # 创建Embed
        embed = discord.Embed(
            title="机器人性能统计",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # 基本信息
        embed.add_field(name="运行时间", value=str(uptime), inline=True)
        embed.add_field(name="服务器数量", value=f"{guild_count}", inline=True)
        embed.add_field(name="用户数量", value=f"{user_count:,}", inline=True)
        
        # 系统资源
        embed.add_field(name="内存使用", value=f"{memory_usage:.2f} MB", inline=True)
        embed.add_field(name="CPU使用", value=f"{cpu_percent:.1f}%", inline=True)
        embed.add_field(name="Python版本", value=platform.python_version(), inline=True)
        
        # 搜索统计
        if self._search_stats['total_searches'] > 0:
            avg_time = self._search_stats['avg_search_time']
            success_rate = (self._search_stats['successful_searches'] / self._search_stats['total_searches']) * 100
            
            embed.add_field(
                name="搜索统计", 
                value=f"总计: {self._search_stats['total_searches']}\n"
                      f"成功率: {success_rate:.1f}%\n"
                      f"平均时间: {avg_time:.2f}秒",
                inline=False
            )
        
        # 最常用命令
        if self._command_usage:
            top_commands = sorted(
                self._command_usage.items(), 
                key=lambda x: x[1], 
                reverse=True
            )[:5]
            
            cmd_text = "\n".join(f"`/{cmd}`: {count}次" for cmd, count in top_commands)
            embed.add_field(name="最常用命令", value=cmd_text, inline=False)
        
        await interaction.response.send_message(embed=embed)
    
    def record_command_usage(self, command_name):
        """记录命令使用情况"""
        if command_name in self._command_usage:
            self._command_usage[command_name] += 1
        else:
            self._command_usage[command_name] = 1
    
    def record_search(self, successful, duration):
        """记录搜索统计"""
        self._search_stats['total_searches'] += 1
        
        if successful:
            self._search_stats['successful_searches'] += 1
        else:
            self._search_stats['failed_searches'] += 1
        
        # 更新平均时间
        total = self._search_stats['total_search_time'] + duration
        self._search_stats['total_search_time'] = total
        self._search_stats['avg_search_time'] = total / self._search_stats['total_searches']

async def setup(bot):
    await bot.add_cog(Stats(bot))
```

## 配置文件优化

创建针对大型服务器的配置：

```python
# 在config目录添加large_server.py

# 大型服务器推荐配置
MAX_MESSAGES_PER_SEARCH = 1000      # 每次搜索的最大消息数
MESSAGES_PER_PAGE = 5              # 每页展示的结果数
REACTION_TIMEOUT = 1800            # 交互按钮超时时间(30分钟)
CONCURRENT_SEARCH_LIMIT = 5        # 并发搜索限制
GUILD_CONCURRENT_SEARCHES = 3      # 每个服务器的并发搜索限制
USER_SEARCH_COOLDOWN = 60          # 用户搜索冷却时间(秒)

# 缓存设置
USE_REDIS_CACHE = True             # 是否使用Redis缓存
REDIS_URL = "redis://localhost:6379/0"
CACHE_TTL = 600                    # 缓存生存时间(10分钟)
THREAD_CACHE_SIZE = 1000           # 线程缓存大小

# 性能优化设置
USE_INCREMENTAL_LOADING = True     # 使用增量加载
USE_DATABASE_INDEX = True          # 使用数据库索引
DB_PATH = "searchdb.sqlite"        # 数据库路径
```

## 日志优化

针对大型服务器，日志策略也需要调整：

```python
# 在utils目录添加optimized_logging.py

import logging
import os
from logging.handlers import RotatingFileHandler

def setup_logging(log_dir="logs", log_level=logging.INFO):
    """设置优化的日志系统"""
    # 确保日志目录存在
    os.makedirs(log_dir, exist_ok=True)
    
    # 创建根日志记录器
    logger = logging.getLogger()
    logger.setLevel(log_level)
    
    # 移除所有现有处理器
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # 创建标准输出处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_format)
    
    # 创建文件处理器 - 常规日志
    file_handler = RotatingFileHandler(
        os.path.join(log_dir, 'discord_bot.log'),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    file_handler.setLevel(log_level)
    file_format = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_format)
    
    # 创建错误日志处理器
    error_handler = RotatingFileHandler(
        os.path.join(log_dir, 'error.log'),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(file_format)
    
    # 为搜索模块设置单独的日志文件
    search_handler = RotatingFileHandler(
        os.path.join(log_dir, 'search.log'),
        maxBytes=10 * 1024 * 1024,  # 10MB
        backupCount=5
    )
    search_logger = logging.getLogger('discord_bot.search')
    search_logger.propagate = False  # 防止日志重复
    search_logger.addHandler(search_handler)
    
    # 添加处理器
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.addHandler(error_handler)
    
    # 降低Discord库日志级别
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('discord.http').setLevel(logging.WARNING)
    
    return logger
```

## 部署建议

对于大型服务器，建议使用更强大的部署方式：

```
# 在项目根目录添加docker-compose.yml

version: '3'

services:
  discord_bot:
    build: .
    restart: unless-stopped
    volumes:
      - ./:/app
      - ./logs:/app/logs
      - ./data:/app/data
    environment:
      - DISCORD_TOKEN=${DISCORD_TOKEN}
      - PYTHONUNBUFFERED=1
      - CONFIG_MODE=large_server
    depends_on:
      - redis
    
  redis:
    image: redis:alpine
    restart: unless-stopped
    volumes:
      - redis_data:/data
    command: redis-server --appendonly yes

volumes:
  redis_data:
```

以及Dockerfile：

```
# Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
```

这些文件将帮助您优化大型服务器上的机器人性能。您可以根据需要调整参数和实现细节。 