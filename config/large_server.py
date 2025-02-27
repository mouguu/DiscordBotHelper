# 大型服务器配置文件
# 适用于10000+用户和大量帖子的服务器环境

# ================ 基本设置 ================
COMMAND_PREFIX = "/"  # 命令前缀
LOG_LEVEL = "INFO"    # 日志级别
LOG_DIR = "logs"      # 日志目录

# ================ 搜索设置 ================
# 限制每次搜索的最大消息数，降低此值可减轻服务器负载
MAX_MESSAGES_PER_SEARCH = 1000

# 每页显示的消息数量，较少的消息可以减少渲染时间
MESSAGES_PER_PAGE = 5

# 排序选项
SEARCH_ORDER_OPTIONS = {
    "latest": "最新发布", 
    "oldest": "最早发布",
    "most_replies": "最多回复",
    "most_reactions": "最多反应",
    "last_active": "最近活跃"
}

# 搜索相关的嵌入设置
EMBED_COLOR = 0x3498db  # 蓝色
MAX_EMBED_FIELD_LENGTH = 1000  # 减少单个字段长度，提高渲染速度

# ================ 性能优化 ================
# 用户交互超时时间（秒）
REACTION_TIMEOUT = 1800  # 30分钟

# 并发搜索限制
CONCURRENT_SEARCH_LIMIT = 5  # 全局并发搜索限制
GUILD_CONCURRENT_SEARCHES = 3  # 每个服务器的并发搜索限制
USER_SEARCH_COOLDOWN = 60     # 用户搜索冷却时间(秒)

# ================ 高级缓存设置 ================
# 缓存配置
USE_REDIS_CACHE = True  # 使用Redis作为二级缓存
REDIS_URL = "redis://localhost:6379/0"  # Redis连接URL
CACHE_TTL = 600  # 缓存生存时间(10分钟)
THREAD_CACHE_SIZE = 1000  # 线程缓存最大项数

# ================ 数据库设置 ================
# 数据库索引
USE_DATABASE_INDEX = True  # 使用数据库索引来加速搜索
DB_PATH = "data/searchdb.sqlite"  # 数据库路径
DB_CONNECTION_POOL_SIZE = 5  # 数据库连接池大小

# ================ 消息加载策略 ================
USE_INCREMENTAL_LOADING = True  # 使用增量加载策略
MESSAGE_BATCH_SIZE = 100  # 消息批量加载大小
MAX_ARCHIVED_THREADS = 500  # 最多搜索的归档线程数

# ================ 内存优化 ================
OPTIMIZE_MESSAGE_CONTENT = True  # 消息内容优化
MAX_CONTENT_LENGTH = 2000  # 最大内容长度
MAX_ATTACHMENTS_PREVIEW = 3  # 最大附件预览数
MAX_REACTION_COUNT = 10  # 最大反应数量显示

# ================ 统计和监控 ================
ENABLE_PERFORMANCE_MONITORING = True  # 启用性能监控
STATS_UPDATE_INTERVAL = 3600  # 统计更新间隔(秒)
KEEP_STATS_HISTORY = True  # 保存统计历史
STATS_HISTORY_LENGTH = 24  # 保存多少个时间点的统计数据(小时)

# ================ 负载均衡 ================
SEARCH_TIMEOUT = 60.0  # 搜索超时时间(秒)
SEARCH_AUTO_CANCEL = True  # 当负载过高时自动取消长时间运行的搜索

# ================ 线程池设置 ================
THREAD_POOL_WORKERS = 4  # 线程池大小
IO_THREAD_POOL_WORKERS = 8  # IO线程池大小

# ================ 安全设置 ================
MAX_RESULTS_PER_USER = 5000  # 每个用户可以获取的最大结果数
RATE_LIMIT_ENABLED = True  # 启用速率限制
MAX_COMMANDS_PER_MINUTE = 20  # 每分钟最大命令数 