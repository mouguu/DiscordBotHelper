import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, List, Dict, Tuple, Any, Union, AsyncGenerator
import logging
import re
import pytz
import uuid
from functools import lru_cache
from config.config import (
    MAX_MESSAGES_PER_SEARCH,
    MESSAGES_PER_PAGE,
    REACTION_TIMEOUT,
    MAX_EMBED_FIELD_LENGTH,
    EMBED_COLOR,
    SEARCH_ORDER_OPTIONS,
    CONCURRENT_SEARCH_LIMIT
)
from utils.helpers import truncate_text
from utils.pagination import MultiEmbedPaginationView
from utils.embed_helper import DiscordEmbedBuilder
from utils.attachment_helper import AttachmentProcessor
from utils.thread_stats import get_thread_stats
import asyncio
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from utils.search_query_parser import SearchQueryParser

logger = logging.getLogger('discord_bot.search')

class ThreadCache:
    """Thread data caching system with TTL and automatic cleanup"""
    
    def __init__(self, ttl: int = 300):
        self._cache = {}
        self._stats_cache = {}
        self._ttl = ttl  # TTL in seconds
        self._last_cleanup = datetime.now().timestamp()
        self._logger = logging.getLogger('discord_bot.search.cache')
        self._logger.info(f"Thread cache initialized with TTL: {ttl}s")
    
    async def get_thread_stats(self, thread: discord.Thread) -> Dict:
        """Get thread stats with caching"""
        cache_key = f"stats_{thread.id}"
        current_time = datetime.now().timestamp()
        
        # Check cache first
        if cache_key in self._stats_cache:
            cached = self._stats_cache[cache_key]
            if current_time - cached['timestamp'] < self._ttl:
                return cached['data']
        
        # Cache miss, fetch new data
        try:
            stats = await get_thread_stats(thread)
            self._stats_cache[cache_key] = {
                'data': stats,
                'timestamp': current_time
            }
            return stats
        except Exception as e:
            self._logger.error(f"Error getting stats for thread {thread.id}: {e}")
            return {'reaction_count': 0, 'reply_count': 0}
    
    def store_thread_data(self, thread_id: int, data: Any) -> None:
        """Store thread data in cache"""
        self._cache[thread_id] = {
            'data': data,
            'timestamp': datetime.now().timestamp()
        }
    
    def get_thread_data(self, thread_id: int) -> Optional[Any]:
        """Get thread data from cache if not expired"""
        if thread_id in self._cache:
            entry = self._cache[thread_id]
            if datetime.now().timestamp() - entry['timestamp'] < self._ttl:
                return entry['data']
        return None
    
    async def cleanup(self) -> int:
        """Remove expired entries from cache"""
        # Only run cleanup periodically
        current_time = datetime.now().timestamp()
        if current_time - self._last_cleanup < 60:  # Cleanup at most once per minute
            return 0
            
        self._last_cleanup = current_time
        
        # Find expired entries
        expired_thread_keys = [k for k, v in self._cache.items() 
                             if current_time - v['timestamp'] > self._ttl]
        expired_stats_keys = [k for k, v in self._stats_cache.items() 
                            if current_time - v['timestamp'] > self._ttl]
        
        # Remove expired entries
        for key in expired_thread_keys:
            del self._cache[key]
        for key in expired_stats_keys:
            del self._stats_cache[key]
        
        total_removed = len(expired_thread_keys) + len(expired_stats_keys)
        if total_removed > 0:
            self._logger.debug(f"Cache cleanup: removed {total_removed} expired entries")
        
        return total_removed

class Search(commands.Cog, name="search"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.embed_builder = DiscordEmbedBuilder(EMBED_COLOR)
        self.attachment_processor = AttachmentProcessor()
        self._logger = logger
        self._logger.info("Search cog initialized")
        
        # Enhanced caching
        self._thread_cache = ThreadCache(ttl=300)  # 5 minutes cache
        
        # Active searches tracking for cancellation
        self._active_searches = {}
        
        # User search history
        self._search_history = {}
        
        # Add query parser
        self._query_parser = SearchQueryParser()
        
        # Concurrency control with dynamic adjustment
        self._max_concurrency = CONCURRENT_SEARCH_LIMIT
        self._search_semaphore = asyncio.Semaphore(self._max_concurrency)
        
        # Compile common regex patterns
        self._url_pattern = re.compile(r'https?://\S+')
        
        # Sorting functions mapping
        self._sort_functions = {
            "Reactions (High to Low)": (lambda x: x['stats']['reaction_count'], True),
            "Reactions (Low to High)": (lambda x: x['stats']['reaction_count'], False),
            "Replies (High to Low)": (lambda x: x['stats']['reply_count'], True),
            "Replies (Low to High)": (lambda x: x['stats']['reply_count'], False),
            "Post Time (Newest First)": (lambda x: x['thread'].created_at, True),
            "Post Time (Oldest First)": (lambda x: x['thread'].created_at, False),
            "Last Active (Newest First)": (lambda x: x['thread'].last_message.created_at if x['thread'].last_message else x['thread'].created_at, True),
            "Last Active (Oldest First)": (lambda x: x['thread'].last_message.created_at if x['thread'].last_message else x['thread'].created_at, False)
        }
        
        # Background tasks
        self._cache_cleanup_task = bot.loop.create_task(self._cleanup_cache_task())
        self._search_cleanup_task = bot.loop.create_task(self._cleanup_searches_task())
    
    async def cog_unload(self):
        """Clean up when cog is unloaded"""
        if self._cache_cleanup_task:
            self._cache_cleanup_task.cancel()
        if self._search_cleanup_task:
            self._search_cleanup_task.cancel()
    
    async def _cleanup_cache_task(self):
        """Periodically clean up cache"""
        while not self.bot.is_closed():
            try:
                await self._thread_cache.cleanup()
            except Exception as e:
                self._logger.error(f"Error in cache cleanup: {e}")
            await asyncio.sleep(60)  # Run every minute
    
    async def _cleanup_searches_task(self):
        """Clean up old searches"""
        while not self.bot.is_closed():
            try:
                current_time = datetime.now()
                expired_searches = []
                
                # Find searches older than 10 minutes
                for search_id, search_info in self._active_searches.items():
                    if (current_time - search_info["start_time"]).total_seconds() > 600:
                        expired_searches.append(search_id)
                
                # Remove expired searches
                for search_id in expired_searches:
                    if search_id in self._active_searches:
                        del self._active_searches[search_id]
                
                if expired_searches:
                    self._logger.debug(f"Search cleanup: removed {len(expired_searches)} expired searches")
            except Exception as e:
                self._logger.error(f"Error in search cleanup: {e}")
            await asyncio.sleep(300)  # Run every 5 minutes
    
    @lru_cache(maxsize=256)
    def _check_tags(self, thread_tags: Tuple[str], search_tags: Tuple[str], exclude_tags: Tuple[str]) -> bool:
        """Check if thread tags match search conditions (with caching)"""
        thread_tags_lower = tuple(tag.lower() for tag in thread_tags)
        
        # Check if search tags match (any required tag must be present)
        if search_tags and not any(tag in thread_tags_lower for tag in search_tags):
            return False
        
        # Check if any excluded tag is present
        if exclude_tags and any(tag in thread_tags_lower for tag in exclude_tags):
            return False
        
        return True
    
    def _preprocess_keywords(self, keywords: List[str]) -> List[str]:
        """Preprocess search keywords for better matching"""
        if not keywords:
            return []
        
        # Clean and normalize keywords
        processed = []
        for keyword in keywords:
            if not keyword or not keyword.strip():
                continue
            # Convert to lowercase and strip whitespace
            cleaned = keyword.strip().lower()
            if cleaned:
                processed.append(cleaned)
        
        return processed
    
    def _check_keywords(self, content: str, search_query: str, exclude_keywords: List[str]) -> bool:
        """Check if content matches using advanced search syntax"""
        if not content:
            return not search_query
        
        content_lower = content.lower()
        
        # First, check excluded keywords
        if exclude_keywords and any(keyword in content_lower for keyword in exclude_keywords):
            return False
        
        # If there is no search query, match successfully
        if not search_query:
            return True
        
        # Parse and evaluate the search query
        query_tree = self._query_parser.parse_query(search_query)
        
        # Use existing logic for simple queries
        if query_tree["type"] == "simple":
            return all(keyword in content_lower for keyword in query_tree["keywords"])
        
        # Use syntax tree evaluation for advanced queries
        if query_tree["type"] == "advanced":
            return self._query_parser.evaluate(query_tree["tree"], content)
        
        # Empty query always matches
        if query_tree["type"] == "empty":
            return True
        
        # Unknown query type
        self._logger.warning(f"Unknown query type: {query_tree['type']}")
        return False
    
    async def _process_single_thread(self, thread: discord.Thread, conditions: Dict, 
                                    cancel_event=None) -> Optional[Dict]:
        """Process a single thread for search (optimized version)"""
        try:
            # Check cancellation
            if cancel_event and cancel_event.is_set():
                return None
            
            async with self._search_semaphore:
                # Quick validation
                if not thread or not thread.id:
                    return None
                
                # First check date range (if specified)
                if conditions.get('start_date') and thread.created_at < conditions['start_date']:
                    return None
                
                if conditions.get('end_date') and thread.created_at > conditions['end_date']:
                    return None
                
                # Then check author conditions (cheap operation)
                if conditions['original_poster'] and thread.owner and thread.owner.id != conditions['original_poster'].id:
                    return None
                
                if conditions['exclude_op'] and thread.owner and thread.owner.id == conditions['exclude_op'].id:
                    return None
                
                # Check tags (cached function for efficiency)
                thread_tag_names = tuple(tag.name for tag in thread.applied_tags)
                search_tags = tuple(conditions.get('search_tags', []))
                exclude_tags = tuple(conditions.get('exclude_tags', []))
                
                if not self._check_tags(thread_tag_names, search_tags, exclude_tags):
                    return None
                
                # If we've passed all the above checks, now fetch the first message
                first_message = None
                retry_count = 0
                max_retries = 2
                
                while retry_count <= max_retries:
                    try:
                        # Check cancellation again
                        if cancel_event and cancel_event.is_set():
                            return None
                        
                        # Fetch first message
                        first_message = await thread.fetch_message(thread.id)
                        break
                    except discord.NotFound:
                        return None
                    except discord.HTTPException as e:
                        if e.status == 429:  # Rate limited
                            retry_after = e.retry_after or (1 * (retry_count + 1))
                            self._logger.warning(f"Rate limited, waiting {retry_after}s")
                            await asyncio.sleep(retry_after)
                            retry_count += 1
                        elif 500 <= e.status < 600:  # Server error, retry
                            await asyncio.sleep(1 * (retry_count + 1))
                            retry_count += 1
                        else:
                            self._logger.warning(f"Failed to get first message for thread {thread.id}: {e}")
                            return None
                    except Exception as e:
                        self._logger.warning(f"Error fetching message for thread {thread.id}: {e}")
                        return None
                
                if not first_message:
                    return None
                
                # Check content keywords
                if first_message.content:
                    # Efficiently check keywords
                    if not self._check_keywords(
                        first_message.content,
                        conditions.get('search_query', ''),
                        conditions.get('exclude_keywords', [])
                    ):
                        return None
                elif conditions.get('search_query'):
                    # If there are search keywords but no content, it's a mismatch
                    return None
                
                # Get thread statistics
                try:
                    # Check cancellation
                    if cancel_event and cancel_event.is_set():
                        return None
                    
                    stats = await self._thread_cache.get_thread_stats(thread)
                    
                    # Additional numeric filters
                    if conditions.get('min_reactions') is not None and stats.get('reaction_count', 0) < conditions['min_reactions']:
                        return None
                    
                    if conditions.get('min_replies') is not None and stats.get('reply_count', 0) < conditions['min_replies']:
                        return None
                    
                except Exception as e:
                    self._logger.error(f"Error getting stats for thread {thread.id}: {e}")
                    stats = {'reaction_count': 0, 'reply_count': 0}
                
                # All checks passed, return the result
                return {
                    'thread': thread,
                    'stats': stats,
                    'first_message': first_message
                }
        
        except asyncio.CancelledError:
            raise
        except Exception as e:
            thread_name = getattr(thread, 'name', 'unknown')
            thread_id = getattr(thread, 'id', 'unknown')
            self._logger.error(f"Error processing thread {thread_name} ({thread_id}): {e}", exc_info=True)
            return None
    
    async def _process_thread_batch(self, threads: List[discord.Thread], search_conditions: Dict, 
                                   cancel_event=None) -> List[Dict]:
        """Process a batch of threads with smart concurrency control"""
        if not threads:
            return []
        
        # For small batches, process sequentially to avoid overhead
        if len(threads) <= 3:
            results = []
            for thread in threads:
                if cancel_event and cancel_event.is_set():
                    break
                result = await self._process_single_thread(thread, search_conditions, cancel_event)
                if result:
                    results.append(result)
            return results
        
        # For larger batches, process concurrently
        tasks = []
        for thread in threads:
            if cancel_event and cancel_event.is_set():
                break
            
            task = asyncio.create_task(
                self._process_single_thread(thread, search_conditions, cancel_event)
            )
            tasks.append(task)
        
        if not tasks:
            return []
        
        # Gather results, ignoring exceptions
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if r is not None and not isinstance(r, Exception)]
    
    async def _search_archived_threads(self, forum_channel, search_conditions, progress_message, 
                                      search_id, max_results=1000, total_active=0):
        """Search archived threads with progress updates and cancellation support"""
        filtered_results = []
        processed_count = total_active
        batch_size = 100  # Discord API limit
        batch_count = 0
        last_thread = None
        error_count = 0
        cancel_event = self._active_searches.get(search_id, {}).get("cancel_event")
        
        start_time = datetime.now()
        last_update_time = start_time
        
        while True:
            # Check cancellation
            if cancel_event and cancel_event.is_set():
                await progress_message.edit(
                    embed=self.embed_builder.create_warning_embed(
                        "Search cancelled",
                        f"✓ Processed: {processed_count} posts\n"
                        f"📊 Matched results: {len(filtered_results)}\n"
                        f"⏱️ Time taken: {(datetime.now() - start_time).total_seconds():.1f} s"
                    )
                )
                return filtered_results
            
            # Stop if we reached the maximum results
            if len(filtered_results) >= max_results:
                await progress_message.edit(
                    embed=self.embed_builder.create_info_embed(
                        "Search exceeded limit",
                        f"🔍 Reached maximum results ({max_results})\n"
                        f"✓ Processed: {processed_count} posts\n"
                        f"⏱️ Time taken: {(datetime.now() - start_time).total_seconds():.1f} s"
                    )
                )
                return filtered_results
            
            try:
                # Fetch a batch of archived threads
                archived_threads = []
                async for thread in forum_channel.archived_threads(limit=batch_size, before=last_thread):
                    archived_threads.append(thread)
                    last_thread = thread
                
                if not archived_threads:
                    break  # No more threads to process
                
                batch_count += 1
                
                # Process this batch
                batch_results = await self._process_thread_batch(
                    archived_threads, search_conditions, cancel_event
                )
                
                if batch_results:
                    filtered_results.extend(batch_results)
                
                processed_count += len(archived_threads)
                current_time = datetime.now()
                elapsed_time = (current_time - start_time).total_seconds()
                
                # Update progress message (not too frequently to avoid rate limits)
                if (current_time - last_update_time).total_seconds() >= 1.5:
                    await progress_message.edit(
                        embed=self.embed_builder.create_info_embed(
                            "Search in progress",
                            f"✓ Processed: {processed_count} posts\n"
                            f"📊 Matched results: {len(filtered_results)}\n"
                            f"⏱️ Time taken: {elapsed_time:.1f} s\n"
                            f"📦 Processed {batch_count} batches of archived posts"
                        )
                    )
                    last_update_time = current_time
            
            except asyncio.CancelledError:
                raise
            except Exception as e:
                error_count += 1
                self._logger.error(f"Error retrieving archived threads: {e}")
                
                # Update with error info
                current_time = datetime.now()
                if (current_time - last_update_time).total_seconds() >= 2:
                    await progress_message.edit(
                        embed=self.embed_builder.create_warning_embed(
                            "Search in progress",
                            f"❌ Error occurred in batch {batch_count}\n"
                            f"✓ Processed: {processed_count} posts\n"
                            f"📊 Current results: {len(filtered_results)}\n"
                            f"⏳ Trying to continue search..."
                        )
                    )
                    last_update_time = current_time
                
                if error_count >= 3:  # Stop after too many consecutive errors
                    break
                
                # Add delay before retry
                await asyncio.sleep(2)
        
        return filtered_results
    
    def _parse_date(self, date_str: str) -> Optional[datetime]:
        """Parse date string with multiple formats support"""
        if not date_str:
            return None
        
        # Try various date formats
        formats = [
            "%Y-%m-%d",  # 2023-01-15
            "%Y/%m/%d",  # 2023/01/15
            "%m/%d/%Y",  # 01/15/2023
            "%d.%m.%Y"   # 15.01.2023
        ]
        
        # Try each format
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        
        # Try relative date expressions like "7d" (7 days), "3m" (3 months), etc.
        match = re.match(r'^(\d+)([dmyw])$', date_str.lower())
        if match:
            num, unit = match.groups()
            num = int(num)
            now = datetime.now()
            
            if unit == 'd':  # days
                return now - timedelta(days=num)
            elif unit == 'w':  # weeks
                return now - timedelta(weeks=num)
            elif unit == 'm':  # months (approximate)
                return now - timedelta(days=num*30)
            elif unit == 'y':  # years (approximate)
                return now - timedelta(days=num*365)
        
        return None
    
    def _store_search_history(self, user_id: int, search_info: Dict) -> None:
        """Store search in user history"""
        if user_id not in self._search_history:
            self._search_history[user_id] = []
        
        # Add current search
        self._search_history[user_id].insert(0, {
            **search_info,
            'timestamp': datetime.now()
        })
        
        # Keep only recent searches
        self._search_history[user_id] = self._search_history[user_id][:10]
    
    @app_commands.command(name="search_syntax", description="Show advanced search syntax instructions")
    @app_commands.guild_only()
    async def search_syntax(self, interaction: discord.Interaction):
        """Show advanced search syntax help"""
        embed = discord.Embed(
            title="Advanced Search Syntax Guide",
            description="Forum search supports the following advanced syntax features:",
            color=EMBED_COLOR
        )
        
        embed.add_field(
            name="Basic Keywords",
            value="Entering multiple keywords will match posts containing all keywords (AND logic)\n"
                  "Example: `issue solution`",
            inline=False
        )
        
        embed.add_field(
            name="OR Operator",
            value="Use `OR` or `|` to match any keyword\n"
                  "Example: `solution OR workaround`\n"
                  "Example: `topic | content | title`",
            inline=False
        )
        
        embed.add_field(
            name="NOT Operator",
            value="Use `NOT` or `-` to exclude posts containing a specific keyword\n"
                  "Example: `issue NOT resolved`\n"
                  "Example: `issue -resolved`",
            inline=False
        )
        
        embed.add_field(
            name="Exact Phrase Matching",
            value="Use quotes `\"...\"` for exact phrase matching\n"
                  "Example: `\"complete phrase match\"`",
            inline=False
        )
        
        embed.add_field(
            name="Combined Usage",
            value="Multiple operators can be used in combination\n"
                  "Example: `(topic | content) NOT \"resolved\"`",
            inline=False
        )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="forum_search", description="Search forum posts")
    @app_commands.guild_only()
    @app_commands.describe(
        forum_name="Select the forum channel to search",
        order="Result sorting method",
        original_poster="Specified original poster (select member)",
        tag1="Select the first tag to search for",
        tag2="Select the second tag to search for",
        tag3="Select the third tag to search for",
        search_word="Search keywords (supports advanced syntax: OR, AND, NOT, \"exact phrase\")",
        exclude_word="Exclude keywords (comma-separated)",
        exclude_op="Excluded author (select member)",
        exclude_tag1="Select the first tag to exclude",
        exclude_tag2="Select the second tag to exclude",
        start_date="Start date (YYYY-MM-DD or 7d for the last 7 days)",
        end_date="End date (YYYY-MM-DD)",
        min_reactions="Minimum reactions",
        min_replies="Minimum replies"
    )
    @app_commands.choices(order=[
        app_commands.Choice(name=option, value=option)
        for option in SEARCH_ORDER_OPTIONS
    ])
    async def forum_search(
        self,
        interaction: discord.Interaction,
        forum_name: str,
        order: str = "Reactions (High to Low)",
        original_poster: Optional[discord.User] = None,
        tag1: Optional[str] = None,
        tag2: Optional[str] = None,
        tag3: Optional[str] = None,
        search_word: Optional[str] = None,
        exclude_word: Optional[str] = None,
        exclude_op: Optional[discord.User] = None,
        exclude_tag1: Optional[str] = None,
        exclude_tag2: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        min_reactions: Optional[int] = None,
        min_replies: Optional[int] = None
    ):
        """Command implementation for searching forum posts (optimized version)"""
        try:
            self._logger.info(f"Search command invoked - User: {interaction.user}")
            
            # Permission check
            if not interaction.guild:
                await interaction.response.send_message(
                    embed=self.embed_builder.create_error_embed("Command Error", "This command can only be used in a server"),
                    ephemeral=True
                )
                return

            permissions = interaction.channel.permissions_for(interaction.guild.me)
            if not (permissions.send_messages and permissions.embed_links):
                await interaction.response.send_message(
                    embed=self.embed_builder.create_error_embed("Permission Error", "Bot lacks necessary permissions: Send Messages, Embed Links"),
                    ephemeral=True
                )
                return

            # Create search ID and setup cancellation tracking
            search_id = str(uuid.uuid4())
            cancel_event = asyncio.Event()
            self._active_searches[search_id] = {
                "cancel_event": cancel_event,
                "start_time": datetime.now()
            }
            
            # Defer response to give more processing time
            await interaction.response.defer(ephemeral=True)
            
            # Get forum channel
            forum_channel = interaction.guild.get_channel(int(forum_name))
            if not isinstance(forum_channel, discord.ForumChannel):
                await interaction.followup.send(
                    embed=self.embed_builder.create_error_embed("Search Error", "Specified forum channel not found"),
                    ephemeral=True
                )
                return

            # Parse date ranges if provided
            start_datetime = None
            end_datetime = None
            date_error = None
            
            if start_date:
                start_datetime = self._parse_date(start_date)
                if not start_datetime:
                    date_error = f"Could not parse start date: {start_date}"
            
            if end_date:
                end_datetime = self._parse_date(end_date)
                if not end_datetime:
                    date_error = f"Could not parse end date: {end_date}"
                else:
                    # Include the entire end date
                    end_datetime = end_datetime + timedelta(days=1, microseconds=-1)
            
            if date_error:
                await interaction.followup.send(
                    embed=self.embed_builder.create_error_embed("Date Format Error", date_error + "\nPlease use YYYY-MM-DD format or relative dates (e.g., 7d for the last 7 days)"),
                    ephemeral=True
                )
                return

            # Preprocess search conditions
            search_conditions = {
                'search_tags': set(tag.lower() for tag in [tag1, tag2, tag3] if tag),
                'exclude_tags': set(tag.lower() for tag in [exclude_tag1, exclude_tag2] if tag),
                'search_query': search_word,
                'exclude_keywords': self._preprocess_keywords(exclude_word.split(",") if exclude_word else []),
                'original_poster': original_poster,
                'exclude_op': exclude_op,
                'start_date': start_datetime,
                'end_date': end_datetime,
                'min_reactions': min_reactions,
                'min_replies': min_replies
            }

            # Generate search condition summary
            condition_summary = []
            if search_conditions['search_tags']:
                condition_summary.append(f"🏷️ Included tags: {', '.join(search_conditions['search_tags'])}")
            if search_conditions['exclude_tags']:
                condition_summary.append(f"🚫 Excluded tags: {', '.join(search_conditions['exclude_tags'])}")
            if search_conditions['search_query']:
                condition_summary.append(f"🔍 Keywords: {search_conditions['search_query']}")
            if search_conditions['exclude_keywords']:
                condition_summary.append(f"❌ Excluded words: {', '.join(search_conditions['exclude_keywords'])}")
            if original_poster:
                condition_summary.append(f"👤 Original poster: {original_poster.display_name}")
            if exclude_op:
                condition_summary.append(f"🚷 Excluded poster: {exclude_op.display_name}")
            if start_datetime:
                condition_summary.append(f"📅 Start date: {start_datetime.strftime('%Y-%m-%d')}")
            if end_datetime:
                condition_summary.append(f"📅 End date: {end_datetime.strftime('%Y-%m-%d')}")
            if min_reactions is not None:
                condition_summary.append(f"👍 Minimum reactions: {min_reactions}")
            if min_replies is not None:
                condition_summary.append(f"💬 Minimum replies: {min_replies}")
            
            conditions_text = "\n".join(condition_summary) if condition_summary else "No specific search criteria"

            # Create cancel button for progress message
            cancel_button = discord.ui.Button(label="Cancel Search", style=discord.ButtonStyle.danger)
            
            async def cancel_callback(btn_interaction):
                if search_id in self._active_searches:
                    self._active_searches[search_id]["cancel_event"].set()
                    await btn_interaction.response.send_message("Search cancelled", ephemeral=True)
            
            cancel_button.callback = cancel_callback
            cancel_view = discord.ui.View(timeout=300)
            cancel_view.add_item(cancel_button)

            # Send initial progress message
            progress_message = await interaction.followup.send(
                embed=self.embed_builder.create_info_embed(
                    "Search in progress", 
                    f"📋 Search criteria:\n{conditions_text}\n\n💫 Searching active posts..."
                ),
                view=cancel_view,
                ephemeral=True
            )

            filtered_results = []
            processed_count = 0
            start_time = datetime.now()
            
            # Process active threads
            active_threads = forum_channel.threads
            active_count = len(active_threads)
            
            if active_count > 0:
                try:
                    active_results = await self._process_thread_batch(active_threads, search_conditions, cancel_event)
                    if active_results:
                        filtered_results.extend(active_results)
                    processed_count += active_count
                    
                    # Update progress
                    elapsed_time = (datetime.now() - start_time).total_seconds()
                    await progress_message.edit(
                        embed=self.embed_builder.create_info_embed(
                            "Search in progress",
                            f"✓ Processed active posts: {processed_count}\n"
                            f"📊 Matching results: {len(filtered_results)}\n"
                            f"⏱️ Time taken: {elapsed_time:.1f} s\n"
                            f"⏳ Searching archived posts..."
                        )
                    )
                except Exception as e:
                    self._logger.error(f"Error processing active threads: {e}")
                    await progress_message.edit(
                        embed=self.embed_builder.create_warning_embed(
                            "Search in progress",
                            f"❌ Error occurred while processing active posts\n"
                            f"📊 Current results: {len(filtered_results)}\n"
                            f"⏳ Continuing to search archived posts..."
                        )
                    )

            # Process archived threads
            if not cancel_event.is_set():
                try:
                    archived_results = await self._search_archived_threads(
                        forum_channel, 
                        search_conditions, 
                        progress_message, 
                        search_id,
                        max_results=MAX_MESSAGES_PER_SEARCH - len(filtered_results),
                        total_active=processed_count
                    )
                    
                    if archived_results:
                        filtered_results.extend(archived_results)
                except Exception as e:
                    self._logger.error(f"Error searching archived threads: {e}")
            
            # Calculate total search time
            total_time = (datetime.now() - start_time).total_seconds()
            
            # Check if search was cancelled
            if cancel_event.is_set():
                if search_id in self._active_searches:
                    del self._active_searches[search_id]
                return
            
            # Update final progress status
            await progress_message.edit(
                embed=self.embed_builder.create_info_embed(
                    "Search complete",
                    f"📋 Search criteria:\n{conditions_text}\n\n"
                    f"✅ Processed {processed_count} posts in total\n"
                    f"📊 Found {len(filtered_results)} matching results\n"
                    f"⏱️ Total time taken: {total_time:.1f} s\n"
                    f"💫 Generating result pages..."
                ),
                view=None
            )
            
            # Store search in history
            self._store_search_history(interaction.user.id, {
                'forum': forum_channel.name,
                'conditions': search_conditions,
                'results_count': len(filtered_results),
                'processed_count': processed_count,
                'duration': total_time
            })

            # Sort results based on selected order
            if order in self._sort_functions:
                sort_key, reverse = self._sort_functions[order]
                filtered_results.sort(key=sort_key, reverse=reverse)
            else:
                # Default sort by newest first
                sort_key, reverse = self._sort_functions["Post Time (Newest First)"]
                filtered_results.sort(key=sort_key, reverse=reverse)

            # Clean up active search
            if search_id in self._active_searches:
                del self._active_searches[search_id]

            if not filtered_results:
                await interaction.followup.send(
                    embed=self.embed_builder.create_warning_embed("No Search Results", "No posts found matching the criteria"),
                    ephemeral=True
                )
                return

            # Create paginated display
            async def generate_embeds(page_items, page_number):
                """Generate embeds for result pages"""
                embeds = []
                for item in page_items:
                    thread = item['thread']
                    stats = item['stats']
                    first_message = item['first_message']

                    embed = discord.Embed(
                        title=truncate_text(thread.name, 256),
                        url=thread.jump_url,
                        color=EMBED_COLOR
                    )

                    if thread.owner:
                        embed.set_author(
                            name=thread.owner.display_name,
                            icon_url=thread.owner.display_avatar.url
                        )

                    if first_message and first_message.content:
                        # Highlight search keywords in content
                        summary = truncate_text(first_message.content.strip(), 1000)
                        embed.description = f"**Post Summary:**\n{summary}"

                        # Add thumbnail from first image in message if available
                        thumbnail_url = self.attachment_processor.get_first_image(first_message)
                        if thumbnail_url:
                            embed.set_thumbnail(url=thumbnail_url)

                    if thread.applied_tags:
                        tag_names = [tag.name for tag in thread.applied_tags]
                        embed.add_field(name="Tags", value=", ".join(tag_names), inline=True)

                    # Add statistics
                    reaction_count = stats.get('reaction_count', 0) or 0
                    reply_count = stats.get('reply_count', 0) or 0
                    embed.add_field(
                        name="Statistics", 
                        value=f"👍 {reaction_count} | 💬 {reply_count}", 
                        inline=True
                    )

                    # Add timestamps
                    embed.add_field(
                        name="Time",
                        value=f"Created: {discord.utils.format_dt(thread.created_at, 'R')}\n"
                              f"Last Active: {discord.utils.format_dt(thread.last_message.created_at if thread.last_message else thread.created_at, 'R')}",
                        inline=True
                    )

                    # Add pagination info in footer
                    total_items = len(filtered_results)
                    start_idx = page_number * MESSAGES_PER_PAGE + 1
                    end_idx = min((page_number + 1) * MESSAGES_PER_PAGE, total_items)
                    embed.set_footer(text=f"Result {start_idx}-{end_idx} of {total_items}")

                    embeds.append(embed)
                return embeds

            # Use existing pagination view
            paginator = MultiEmbedPaginationView(
                items=filtered_results,
                items_per_page=MESSAGES_PER_PAGE,
                generate_embeds=generate_embeds
            )

            # Generate and send initial page embeds
            initial_page_items = paginator.get_page_items(0)
            if initial_page_items:
                initial_embeds = await generate_embeds(initial_page_items, 0)
                if initial_embeds:
                    await paginator.start(interaction, initial_embeds)
                else:
                    await interaction.followup.send(
                        embed=self.embed_builder.create_error_embed("Error", "Could not generate search result page"),
                        ephemeral=True
                    )
            else:
                await interaction.followup.send(
                    embed=self.embed_builder.create_warning_embed("No Search Results", "No posts found matching the criteria"),
                    ephemeral=True
                )

        except Exception as e:
            self._logger.error(f"Search Error: {str(e)}", exc_info=True)
            await interaction.followup.send(
                embed=self.embed_builder.create_error_embed("Search Error", f"An error occurred during the search: {str(e)}\nPlease try again later"),
                ephemeral=True
            )

    @forum_search.autocomplete('forum_name')
    async def forum_name_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Enhanced forum name autocomplete with history prioritization"""
        try:
            if not interaction.guild:
                return []
            
            choices = []
            user_id = interaction.user.id
            
            # Get forums from user's search history first
            recent_forums = set()
            if user_id in self._search_history:
                for search in self._search_history[user_id]:
                    if 'forum' in search and search['forum']:
                        recent_forums.add(search['forum'])
            
            # Get all forum channels
            forum_channels = []
            for channel in interaction.guild.channels:
                if isinstance(channel, discord.ForumChannel):
                    # Skip if doesn't match current input
                    if current and current.lower() not in channel.name.lower():
                        continue
                    
                    if channel.name and channel.id:
                        # Prioritize recently used forums
                        is_recent = channel.name in recent_forums
                        forum_channels.append((channel, is_recent))
            
            # Sort by recency (recent first) then alphabetically
            forum_channels.sort(key=lambda x: (not x[1], x[0].name.lower()))
            
            # Create choices
            choices = [
                app_commands.Choice(
                    name=f"#{channel.name}" + (" (recent)" if is_recent else ""),
                    value=str(channel.id)
                )
                for channel, is_recent in forum_channels[:25]
            ]
            
            return choices
            
        except Exception as e:
            self._logger.error(f"Forum name autocomplete error: {str(e)}", exc_info=True)
            return []

    @forum_search.autocomplete('tag1')
    @forum_search.autocomplete('tag2')
    @forum_search.autocomplete('tag3')
    @forum_search.autocomplete('exclude_tag1')
    @forum_search.autocomplete('exclude_tag2')
    async def tag_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """Enhanced tag autocomplete with history prioritization"""
        try:
            if not interaction.guild:
                return []

            # Get selected forum ID
            forum_name = None
            for option in interaction.data.get("options", []):
                if option["name"] == "forum_name":
                    forum_name = option["value"]
                    break

            if not forum_name:
                return []

            # Get forum channel
            forum_channel = interaction.guild.get_channel(int(forum_name))
            if not isinstance(forum_channel, discord.ForumChannel):
                return []

            # Get all available tags
            available_tags = forum_channel.available_tags
            
            # Get currently selected tags in the command
            selected_tags = set()
            for option in interaction.data.get("options", []):
                if option["name"].startswith("tag") and option.get("value"):
                    selected_tags.add(option["value"])
                if option["name"].startswith("exclude_tag") and option.get("value"):
                    selected_tags.add(option["value"])

            # Get user's frequently used tags from history
            user_id = interaction.user.id
            tag_frequency = {}
            if user_id in self._search_history:
                for search in self._search_history[user_id]:
                    if 'conditions' in search and 'search_tags' in search['conditions']:
                        for tag in search['conditions']['search_tags']:
                            tag_frequency[tag] = tag_frequency.get(tag, 0) + 1

            # Filter and prioritize tags
            filtered_tags = []
            for tag in available_tags:
                # Skip already selected tags
                if tag.name in selected_tags:
                    continue
                
                # Skip tags that don't match current input
                if current and current.lower() not in tag.name.lower():
                    continue
                
                # Skip moderated tags for non-moderators
                if tag.moderated and not interaction.user.guild_permissions.manage_threads:
                    continue
                
                # Calculate priority (frequency of use)
                frequency = tag_frequency.get(tag.name.lower(), 0)
                filtered_tags.append((tag, frequency))

            # Sort tags by frequency (most used first) then alphabetically
            filtered_tags.sort(key=lambda x: (-x[1], x[0].name.lower()))
            
            # Create choices
            choices = [
                app_commands.Choice(
                    name=tag.name + (" 🔄" if freq > 0 else ""),  # Indicator for frequently used tags
                    value=tag.name
                )
                for tag, freq in filtered_tags[:25]
            ]
            
            return choices
            
        except Exception as e:
            self._logger.error(f"Tag autocomplete error: {str(e)}", exc_info=True)
            return []

    @app_commands.command(name="search_history", description="View your search history")
    @app_commands.guild_only()
    async def search_history(self, interaction: discord.Interaction):
        """Display user's search history"""
        try:
            user_id = interaction.user.id
            
            if user_id not in self._search_history or not self._search_history[user_id]:
                await interaction.response.send_message(
                    embed=self.embed_builder.create_info_embed("Search History", "You haven't performed any searches yet"),
                    ephemeral=True
                )
                return
            
            # Create embed with search history
            embed = discord.Embed(
                title="Your Search History",
                description="Here are your recent search records",
                color=EMBED_COLOR
            )
            
            for i, search in enumerate(self._search_history[user_id][:5], 1):
                forum_name = search.get('forum', 'Unknown Forum')
                timestamp = search.get('timestamp', datetime.now())
                results_count = search.get('results_count', 0)
                duration = search.get('duration', 0)
                
                # Build conditions summary
                conditions = search.get('conditions', {})
                condition_parts = []
                
                if conditions.get('search_tags'):
                    condition_parts.append(f"Tags: {', '.join(conditions['search_tags'][:2])}" + 
                                         ("..." if len(conditions['search_tags']) > 2 else ""))
                
                if conditions.get('search_query'):
                    condition_parts.append(f"Keywords: {conditions['search_query']}")
                
                if conditions.get('original_poster'):
                    condition_parts.append(f"Original Poster: {conditions['original_poster'].display_name}")
                
                conditions_text = " | ".join(condition_parts) if condition_parts else "No specific criteria"
                
                embed.add_field(
                    name=f"{i}. {forum_name} ({discord.utils.format_dt(timestamp, 'R')})",
                    value=f"Criteria: {conditions_text}\nResults: {results_count} items | Time taken: {duration:.1f} s",
                    inline=False
                )
            
            await interaction.response.send_message(embed=embed, ephemeral=True)
            
        except Exception as e:
            self._logger.error(f"Search history command error: {str(e)}", exc_info=True)
            await interaction.response.send_message(
                embed=self.embed_builder.create_error_embed("Error", "Failed to retrieve search history"),
                ephemeral=True
            )

async def setup(bot: commands.Bot):
    await bot.add_cog(Search(bot))