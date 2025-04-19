import logging

# Bot Configuration
COMMAND_PREFIX = "/"
LOG_LEVEL = logging.INFO

# Search Configuration
MAX_MESSAGES_PER_SEARCH = 1000  # Maximum number of messages per search
MESSAGES_PER_PAGE = 5      # Number of messages displayed per page
REACTION_TIMEOUT = 900.0   # Interaction button timeout (15 minutes)
MAX_EMBED_FIELD_LENGTH = 1024  # Maximum length of Discord embed field
CONCURRENT_SEARCH_LIMIT = 5  # Concurrent search limit

# Embed Configuration
EMBED_COLOR = 0x3498db  # Discord Blue

# Reaction Configuration
MIN_REACTIONS = 1  # Minimum reaction count threshold
REACTION_CACHE_TTL = 3600  # Reaction cache time (seconds)

# Search Order Options
SEARCH_ORDER_OPTIONS = [
    # Removed Chinese options, keeping only English ones
    "Reactions (High to Low)",
    "Reactions (Low to High)",
    "Replies (High to Low)",
    "Replies (Low to High)",
    "Post Time (Newest First)",
    "Post Time (Oldest First)",
    "Last Active (Newest First)",
    "Last Active (Oldest First)"
]