import logging

# Bot Configuration
COMMAND_PREFIX = "/"
LOG_LEVEL = logging.INFO

# Search Configuration
MAX_MESSAGES_PER_SEARCH = 1000  # 单次搜索最大消息数
MESSAGES_PER_PAGE = 5      # 每页显示的消息数
REACTION_TIMEOUT = 900.0   # 交互按钮超时时间（15分钟）
MAX_EMBED_FIELD_LENGTH = 1024  # Discord embed字段最大长度
CONCURRENT_SEARCH_LIMIT = 5  # 并发搜索限制

# Embed Configuration
EMBED_COLOR = 0x3498db  # Discord Blue

# Reaction Configuration
MIN_REACTIONS = 1  # 最小反应数阈值
REACTION_CACHE_TTL = 3600  # 反应缓存时间（秒）

# Search Order Options
SEARCH_ORDER_OPTIONS = [
    "最高反应降序",
    "最高反应升序",
    "总回复数降序",
    "总回复数升序",
    "发帖时间由新到旧",
    "发帖时间由旧到新",
    "最后活跃由新到旧",
    "最后活跃由旧到新"
]