# Discord Forum Search Enhancement Assistant

A powerful Discord bot designed for large servers, providing advanced forum post searching and content management features.

## Features

- **Advanced Search Syntax**: Supports complex logical operators like AND, OR, NOT, and exact phrase matching (`"..."`) via `utils/search_query_parser.py`.
- **Forum Post Search**: Quickly retrieves and filters posts within specified forum channels (`cogs/search.py`).
- **Tag Filtering**: Allows searching based on included or excluded tags.
- **Author Filtering**: Filters search results by original poster or excludes specific authors.
- **Date Range Filtering**: Narrows down searches to specific time periods.
- **Result Sorting**: Sorts results by reactions, replies, post time, or last activity.
- **Paginated Results**: Displays search results in interactive paginated embeds (`utils/pagination.py`).
- **Performance Statistics**: Provides commands to view bot and server performance metrics (`cogs/stats.py`: `/bot_stats`, `/server_stats`).
- **Back to Top**: Quickly jumps to the first message of a channel or thread (`cogs/top_message.py`: `/back_to_top`).
- **Autocomplete**: Offers intelligent suggestions for forum names and tags during command input.
- **Caching**: Utilizes in-memory caching for thread statistics (`utils/thread_stats.py`) to improve performance.
- **Docker Support**: Includes `Dockerfile` and `docker-compose.yml` for containerized deployment, including Redis for potential advanced caching.

## Requirements

- Python 3.11.x
- discord.py v2.3+ (See `requirements.txt`)
- Docker and Docker Compose (Optional, for containerized deployment)
- Redis (Optional, used in the provided `docker-compose.yml`)
- Required Bot Permissions (Intents enabled in `main.py`):
  - Read Messages / View Channel
  - Send Messages
  - Embed Links
  - Add Reactions
  - Read Message History
  - Manage Threads (for tag autocomplete involving moderated tags)
  - Members Intent (for user information)
  - Message Content Intent (for reading message content for search)

## Installation

### Standard Setup

1. Clone the project repository:

   ```bash
   git clone https://github.com/yourusername/discord-forum-search-bot.git # Replace with your repo URL
   cd discord-forum-search-bot
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Create and configure the environment file (`.env`) in the project root:

   ```env
   DISCORD_TOKEN=your_bot_token_here
   ```

4. Run the bot:

   ```bash
   python main.py
   ```

### Docker Compose Setup (Recommended for Caching/Large Servers)

This method uses the provided `docker-compose.yml` to run the bot and a Redis service.

1. Ensure Docker and Docker Compose are installed.
2. Clone the project repository (if not already done).
3. Create a `.env` file in the project root:

   ```env
   DISCORD_TOKEN=your_bot_token_here
   ```

4. Build and start the services:

   ```bash
   docker-compose up --build -d
   ```

   - `--build` ensures the bot image is built.
   - `-d` runs the containers in detached mode (in the background).

5. To stop the services:

   ```bash
   docker-compose down
   ```

*Note: The `docker-compose.yml` sets the environment variable `CONFIG_MODE=large_server`. While the bot currently loads settings from `config/config.py`, this suggests an intended mechanism for loading different configurations. You may need to implement logic in `main.py` to read `CONFIG_MODE` and load settings from `config/large_server.py` accordingly if desired.*
*The compose file also includes volume mounts for `./data` and `./logs` for persistence.*

## Usage

### Main Commands

- `/forum_search [forum_name] [options...]`: Searches posts in the specified forum.
  - `query`: Keywords to search for (supports advanced syntax).
  - `order`: How to sort results (e.g., "Reactions (High to Low)").
  - `original_poster`: Filter by the user who created the post.
  - `tag1`/`tag2`/`tag3`: Include posts with these tags.
  - `exclude_word`: Keywords to exclude (comma-separated).
  - `exclude_op`: Exclude posts created by this user.
  - `exclude_tag1`/`exclude_tag2`: Exclude posts with these tags.
  - `start_date`/`end_date`: Filter by date range (YYYY-MM-DD or relative like "7d").
  - `min_reactions`/`min_replies`: Minimum number of reactions/replies.
- `/search_syntax`: Displays help for the advanced search syntax.
- `/back_to_top`: Posts a link to jump to the first message in the current channel/thread.
- `/bot_stats`: Shows overall bot performance statistics.
- `/server_stats`: Shows statistics specific to the current server.
- `/search_history`: View your recent search history (stored in memory).

### Search Syntax Guide

The `query` parameter in `/forum_search` supports:

- **Keywords**: `word1 word2` (Implicit AND - finds posts containing both words).
- **OR**: `word1 OR word2` or `word1 | word2` (Finds posts containing either word).
- **NOT**: `NOT word` or `-word` (Excludes posts containing the word).
- **Exact Phrase**: `"exact phrase"` (Finds posts containing the exact phrase).
- **Grouping**: `(word1 OR word2) AND word3` (Use parentheses for complex logic).

See `/search_syntax` command for more details.

### Pagination Controls

When viewing search results:

- ⏮️: Go to the first page.
- ◀️: Go to the previous page.
- 🔢: Enter a specific page number to jump to.
- ▶️: Go to the next page.
- ⏭️: Go to the last page.

## Configuration

- **`config/config.py`**: Defines the default bot settings (log level, default embed color, pagination settings, search limits, etc.). This is the configuration loaded by `main.py` by default.
- **`config/large_server.py`**: Provides *example* settings tuned for larger servers (e.g., higher timeouts, potentially different limits, Redis cache settings). **This file is not loaded automatically by the current `main.py`.** See the Docker Compose note above regarding the `CONFIG_MODE` environment variable for potential integration.
- **`.env`**: Used for sensitive information like the `DISCORD_TOKEN`.

## Performance & Optimization

- **Caching**: Basic thread statistics are cached in memory (`utils/thread_stats.py`). The `utils/advanced_cache.py` file and `config/large_server.py` include settings for Redis caching, which can be leveraged for larger scale deployments (especially when using the provided `docker-compose.yml`).
- **Concurrency Limiting**: `config/config.py` sets a global limit (`CONCURRENT_SEARCH_LIMIT`) on simultaneous searches.
- **Asynchronous Operations**: Uses `asyncio` for non-blocking operations.
- **Efficient Data Fetching**: Attempts to fetch thread data efficiently.
- **Docker Compose**: The provided `docker-compose.yml` simplifies deployment and includes Redis for potential caching enhancements.

## Troubleshooting

- **Bot Offline/Unresponsive**: Check the `.env` file for the correct `DISCORD_TOKEN`. Verify the bot process/container is running and check logs for errors. Ensure the required Intents are enabled in your Discord Developer Portal.
- **Docker Compose Issues**: Ensure Docker and Docker Compose are installed correctly. Check container logs using `docker-compose logs discord_bot` or `docker-compose logs redis`.
- **Commands Not Working**: Ensure the bot has the necessary permissions in the channel/server. Check logs for errors. Make sure cogs (`search`, `stats`, `top_message`) are loading correctly in `main.py`.
- **Search Results Empty**: Verify the bot can view the target forum channel and read message history. Check your search terms and filters.
- **Slow Searches**: On large servers, consider reducing `MAX_MESSAGES_PER_SEARCH` in `config/config.py` (or `large_server.py` if implemented). Check bot's resource usage (CPU/Memory).
- **Errors During Search/Pagination**: Check the bot's logs (`logs/` directory if using volume mounts, or container logs) for detailed error messages.

## License

MIT License

## Contributing

Contributions, bug reports, and suggestions are welcome. Please feel free to open an Issue or Pull Request on the project's repository.
