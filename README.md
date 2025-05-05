# Advanced Discord Forum Search & Knowledge Management Engine

An enterprise-grade Discord bot engineered for large-scale communities, delivering unparalleled forum post search capabilities and robust content management functionalities.

## Core Capabilities

- **Sophisticated Query Engine**: Employs an advanced parser (`utils/search_query_parser.py`) supporting intricate logical operations (AND, OR, NOT), precise phrase matching (`"..."`), and complex query structures for granular information retrieval.
- **High-Performance Forum Indexing**: Rapidly indexes and retrieves posts within designated forum channels, optimized for speed and efficiency (`cogs/search.py`).
- **Multi-Dimensional Filtering**: Enables precise result refinement through:
    - **Tag-Based Filtering**: Inclusion and exclusion rules based on forum post tags.
    - **Author Filtering**: Isolates posts by original authors or excludes specific contributors.
    - **Temporal Filtering**: Narrows searches to specific date ranges using absolute or relative timeframes.
- **Configurable Result Ordering**: Sorts search results dynamically based on relevance metrics like reactions, reply count, creation time, or last activity.
- **Interactive Paginated Displays**: Presents results in user-friendly, interactive embeds with intuitive navigation controls (`utils/pagination.py`).
- **Operational Analytics**: Provides commands for monitoring bot health (`/bot_stats`) and server-specific performance metrics (`/server_stats`) (`cogs/stats.py`).
- **Content Navigation Aid**: Includes a `/back_to_top` command for instant navigation to the beginning of channels or threads (`cogs/top_message.py`).
- **Intelligent Autocompletion**: Enhances user experience with context-aware suggestions for forum names and tags during command input.
- **Multi-Tier Caching Architecture**: Implements in-memory caching (`utils/thread_stats.py`) for frequently accessed data and supports an optional Redis backend (via Docker Compose) for enhanced scalability and persistence (`utils/advanced_cache.py`, `config/large_server.py`).
- **Containerized Deployment Ready**: Ships with `Dockerfile` and `docker-compose.yml` for seamless, reproducible deployment using container technology.

## Prerequisites

- Python 3.11+
- discord.py v2.3+ (Refer to `requirements.txt`)
- Docker & Docker Compose (Recommended for production/scaled deployments)
- Redis (Optional, integrated within the provided `docker-compose.yml`)
- Essential Bot Permissions (Ensure Intents are enabled in `main.py` and the Discord Developer Portal):
    - Read Messages / View Channels
    - Send Messages
    - Embed Links
    - Add Reactions
    - Read Message History
    - Manage Threads (Required for comprehensive tag autocomplete)
    - Server Members Intent (For user data retrieval)
    - Message Content Intent (Critical for search indexing)

## Deployment Strategies

### Local Development Setup

1. Clone the repository:
   ```bash
   git clone <your_repository_url>
   cd <repository_directory>
   ```
2. Install core dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Configure the environment (`.env` file in project root):
   ```env
   DISCORD_TOKEN=your_bot_token_here # Obtain from Discord Developer Portal
   ```
4. Launch the bot instance:
   ```bash
   python main.py
   ```

### Containerized Deployment (Recommended)

Utilizes `docker-compose.yml` for orchestrated deployment, including the bot and a Redis service for enhanced caching.

1. Ensure Docker and Docker Compose are installed and operational.
2. Clone the repository (if not already present).
3. Create the `.env` file in the project root as specified above.
4. Build and initiate services:
   ```bash
   docker-compose up --build -d
   ```
   - `--build`: Constructs the Docker image.
   - `-d`: Runs containers in detached mode.
5. Terminate services:
   ```bash
   docker-compose down
   ```

*Deployment Note: The `docker-compose.yml` includes `CONFIG_MODE=large_server`. The application may require modification (e.g., in `main.py`) to dynamically load configurations from `config/large_server.py` based on this environment variable if advanced tuning (like Redis integration) is desired.*
*Persistent storage for logs and potential data is configured via volume mounts (`./data`, `./logs`) in the compose file.*

## Operational Usage

### Primary Interface Commands

- `/forum_search [forum_name] [options...]`: The core search command.
    - `query`: Search keywords employing the advanced syntax.
    - `order`: Result sorting criteria (e.g., "Reactions (High to Low)").
    - `original_poster`: Filter by post creator.
    - `tag1`/`tag2`/`tag3`: Require specific tags.
    - `exclude_word`: Exclude posts containing specific terms (comma-separated).
    - `exclude_op`: Exclude posts by a specific creator.
    - `exclude_tag1`/`exclude_tag2`: Exclude specific tags.
    - `start_date`/`end_date`: Define the search time window (YYYY-MM-DD or relative, e.g., "7d").
    - `min_reactions`/`min_replies`: Set minimum engagement thresholds.
- `/search_syntax`: Displays a comprehensive guide to the advanced query syntax.
- `/back_to_top`: Generates a link to the first message in the context channel/thread.
- `/bot_stats`: Retrieves global bot performance indicators.
- `/server_stats`: Retrieves performance metrics specific to the current server.
- `/search_history`: Accesses your recent search queries (session-based).

### Advanced Query Syntax Reference

The `query` parameter supports:

- **Keywords**: `term1 term2` (Implicit AND).
- **OR Logic**: `term1 OR term2`, `term1 | term2`.
- **Negation**: `NOT term`, `-term`.
- **Phrase Matching**: `"search this exact phrase"`.
- **Logical Grouping**: `(term1 OR term2) AND required_term`.

Consult the `/search_syntax` command for exhaustive details.

### Result Navigation

Utilize the reaction controls on paginated embeds for efficient browsing: ⏮️ (First), ◀️ (Previous), 🔢 (Jump to Page), ▶️ (Next), ⏭️ (Last).

## System Configuration

- **`config/config.py`**: Default operational parameters (logging level, embed aesthetics, pagination defaults, search constraints). Loaded by default.
- **`config/large_server.py`**: *Example* configuration profile optimized for high-traffic environments (potentially different timeouts, limits, Redis settings). **Requires explicit loading logic based on `CONFIG_MODE` or similar mechanism.**
- **`.env`**: Secure storage for sensitive credentials (`DISCORD_TOKEN`).

## Performance Engineering & Optimization

- **Asynchronous Architecture**: Built upon `asyncio` for non-blocking I/O and enhanced concurrency.
- **Optimized Data Retrieval**: Implements efficient strategies for fetching Discord forum and thread data.
- **Concurrency Control**: Global limits (`CONCURRENT_SEARCH_LIMIT` in config) prevent resource exhaustion during peak loads.
- **Multi-Tier Caching**: Leverages in-memory caching for immediate access and optional Redis integration (via Docker) for persistent, scalable caching, significantly reducing API calls and latency.
- **Containerization Benefits**: Docker simplifies deployment, ensures environmental consistency, and facilitates scaling.

## Troubleshooting Guide

- **Service Unavailability**: Validate `DISCORD_TOKEN` in `.env`. Confirm process/container health and inspect logs. Verify required Intents are enabled in the Discord Developer Portal.
- **Docker Compose Failures**: Ensure Docker/Compose installation integrity. Analyze container logs (`docker-compose logs <service_name>`).
- **Command Malfunctions**: Verify bot permissions within the target channel/server. Review logs for specific errors. Ensure all cogs (`search`, `stats`, `top_message`) are loaded successfully (`main.py`).
- **Empty Search Results**: Confirm bot access to the forum channel and message history permissions. Re-evaluate search query and filter parameters.
- **Search Latency**: For large servers, consider tuning `MAX_MESSAGES_PER_SEARCH` in the active configuration. Monitor host system resource utilization (CPU/Memory). Review Redis performance if applicable.
- **Operational Errors**: Examine bot logs (`logs/` directory with volume mounts, or container logs) for detailed stack traces and error messages.

## Licensing

This project is distributed under the MIT License.

## Contribution

Contributions, bug reports, and feature suggestions are highly encouraged. Please utilize GitHub Issues or Pull Requests for engagement.
