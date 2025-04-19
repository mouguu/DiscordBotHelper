# Large server configuration file
# Suitable for servers with 10000+ users and a large number of posts

# ================ Basic Settings ================
COMMAND_PREFIX = "/"  # Command prefix
LOG_LEVEL = "INFO"    # Log level
LOG_DIR = "logs"      # Log directory

# ================ Search Settings ================
# Limit the maximum number of messages per search, lowering this value can reduce server load
MAX_MESSAGES_PER_SEARCH = 1000

# Number of messages displayed per page, fewer messages can reduce rendering time
MESSAGES_PER_PAGE = 5

# Sort options
SEARCH_ORDER_OPTIONS = {
    "latest": "Latest Posts", 
    "oldest": "Oldest Posts",
    "most_replies": "Most Replies",
    "most_reactions": "Most Reactions",
    "last_active": "Last Active"
}

# Search related embed settings
EMBED_COLOR = 0x3498db  # Blue
# Reduce the length of individual fields to improve rendering speed
MAX_EMBED_FIELD_LENGTH = 1000  

# ================ Performance Optimization ================
# User interaction timeout (seconds)
REACTION_TIMEOUT = 1800  # 30 minutes

# Concurrent search limit
CONCURRENT_SEARCH_LIMIT = 5  # Global concurrent search limit
GUILD_CONCURRENT_SEARCHES = 3  # Concurrent search limit per server
USER_SEARCH_COOLDOWN = 60     # User search cooldown (seconds)

# ================ Advanced Cache Settings ================
# Cache configuration
USE_REDIS_CACHE = True  # Use Redis as a secondary cache
REDIS_URL = "redis://localhost:6379/0"  # Redis connection URL
CACHE_TTL = 600  # Cache time-to-live (10 minutes)
THREAD_CACHE_SIZE = 1000  # Maximum number of items in thread cache

# ================ Database Settings ================
# Database index
USE_DATABASE_INDEX = True  # Use database index to speed up search
DB_PATH = "data/searchdb.sqlite"  # Database path
DB_CONNECTION_POOL_SIZE = 5  # Database connection pool size

# ================ Message Loading Strategy ================
USE_INCREMENTAL_LOADING = True  # Use incremental loading strategy
MESSAGE_BATCH_SIZE = 100  # Message batch loading size
MAX_ARCHIVED_THREADS = 500  # Maximum number of archived threads to search

# ================ Memory Optimization ================
OPTIMIZE_MESSAGE_CONTENT = True  # Message content optimization
MAX_CONTENT_LENGTH = 2000  # Maximum content length
MAX_ATTACHMENTS_PREVIEW = 3  # Maximum number of attachment previews
MAX_REACTION_COUNT = 10  # Maximum reaction count display

# ================ Statistics and Monitoring ================
ENABLE_PERFORMANCE_MONITORING = True  # Enable performance monitoring
STATS_UPDATE_INTERVAL = 3600  # Statistics update interval (seconds)
KEEP_STATS_HISTORY = True  # Keep statistics history
# How many time points of statistical data to save (hours)
STATS_HISTORY_LENGTH = 24  

# ================ Load Balancing ================
SEARCH_TIMEOUT = 60.0  # Search timeout (seconds)
# Automatically cancel long-running searches when the load is too high
SEARCH_AUTO_CANCEL = True  

# ================ Thread Pool Settings ================
THREAD_POOL_WORKERS = 4  # Thread pool size
IO_THREAD_POOL_WORKERS = 8  # IO thread pool size

# ================ Security Settings ================
MAX_RESULTS_PER_USER = 5000  # Maximum number of results a user can get
RATE_LIMIT_ENABLED = True  # Enable rate limiting
MAX_COMMANDS_PER_MINUTE = 20  # Maximum commands per minute 