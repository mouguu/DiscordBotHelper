import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, List, Dict, Tuple, Any, Union, Set
import logging, re, asyncio, enum
from datetime import datetime, timedelta
from functools import lru_cache

from config.config import MAX_MESSAGES_PER_SEARCH, MESSAGES_PER_PAGE, EMBED_COLOR, CONCURRENT_SEARCH_LIMIT
from utils.helpers import truncate_text
from utils.pagination import MultiEmbedPaginationView
from utils.embed_helper import DiscordEmbedBuilder
from utils.attachment_helper import AttachmentProcessor
from utils.thread_stats import get_thread_stats
from utils.search_query_parser import SearchQueryParser

logger = logging.getLogger('discord_bot.search')

class ThreadCache:
    def __init__(self, ttl: int = 300):
        self._cache = {}
        self._stats_cache = {}
        self._ttl = ttl
        self._last_cleanup = datetime.now().timestamp()
    
    async def get_thread_stats(self, thread: discord.Thread) -> Dict:
        cache_key = f"stats_{thread.id}"
        current_time = datetime.now().timestamp()
        
        if cache_key in self._stats_cache and current_time - self._stats_cache[cache_key]['timestamp'] < self._ttl:
            return self._stats_cache[cache_key]['data']
        
        try:
            stats = await get_thread_stats(thread)
            self._stats_cache[cache_key] = {'data': stats, 'timestamp': current_time}
            return stats
        except Exception as e:
            logger.error(f"[boundary:error] Thread stats fetch failed for {thread.id}: {e}")
            return {'reaction_count': 0, 'reply_count': 0}
    
    def store(self, thread_id: int, data: Any) -> None:
        self._cache[thread_id] = {'data': data, 'timestamp': datetime.now().timestamp()}
    
    def get(self, thread_id: int) -> Optional[Any]:
        if thread_id in self._cache and datetime.now().timestamp() - self._cache[thread_id]['timestamp'] < self._ttl:
            return self._cache[thread_id]['data']
        return None
    
    async def cleanup(self) -> int:
        current_time = datetime.now().timestamp()
        if current_time - self._last_cleanup < 60: return 0
        self._last_cleanup = current_time
        
        expired_thread = [k for k, v in self._cache.items() if current_time - v['timestamp'] > self._ttl]
        expired_stats = [k for k, v in self._stats_cache.items() if current_time - v['timestamp'] > self._ttl]
        
        for k in expired_thread: del self._cache[k]
        for k in expired_stats: del self._stats_cache[k]
        
        count = len(expired_thread) + len(expired_stats)
        if count > 0: logger.debug(f"[signal] Cleaned {count} cache entries")
        return count

class SearchOrder(str, enum.Enum):
    newest = "newest"
    oldest = "oldest"
    most_replies = "most_replies"
    least_replies = "least_replies"
    most_reactions = "most_reactions"
    least_reactions = "least_reactions"
    alphabetical = "alphabetical"
    reverse_alphabetical = "reverse_alphabetical"
    
    @classmethod
    def _missing_(cls, value): return cls.newest

class CancelView(discord.ui.View):
    def __init__(self, cancel_event: asyncio.Event):
        super().__init__(timeout=300)
        self.cancel_event = cancel_event
        
    @discord.ui.button(label="Cancel Search", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.cancel_event.set()
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
        
    async def disable_buttons(self):
        for item in self.children: item.disabled = True

class Search(commands.Cog, name="search"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.embed_builder = DiscordEmbedBuilder(EMBED_COLOR)
        self.attachment_processor = AttachmentProcessor()
        self._thread_cache = ThreadCache(ttl=300)
        self._active_searches = {}
        self._search_history = {}
        self._query_parser = SearchQueryParser()
        self._search_semaphore = asyncio.Semaphore(CONCURRENT_SEARCH_LIMIT)
        self._url_pattern = re.compile(r'https?://\S+')
        
        # Background tasks
        self._cache_cleanup_task = bot.loop.create_task(self._cleanup_cache_task())
        self._search_cleanup_task = bot.loop.create_task(self._cleanup_searches_task())
        
        # Config
        self.config = bot.config.get('search', {})
        self.cache = bot.cache
        self.stats = None
        self.max_history = self.config.get('history_size', 10)
        
        logger.info("[init] Search module initialized")
    
    async def cog_load(self): self.bot.tree.on_error = self.on_app_command_error
    
    async def on_ready(self): 
        self.stats = self.bot.get_cog('Stats')
        if not self.stats: logger.warning("[boundary:error] Stats cog not found")
    
    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            await interaction.response.send_message(f"⏳ This command is on cooldown. Try again in {error.retry_after:.1f}s", ephemeral=True)
        elif isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message("⚠️ You don't have permission to use this command.", ephemeral=True)
        else:
            logger.error(f"[boundary:error] Error in command {interaction.command.name if interaction.command else 'unknown'}: {str(error)}", exc_info=error)
            if not interaction.response.is_done():
                await interaction.response.send_message("⚠️ An error occurred while processing your command.", ephemeral=True)
    
    async def cog_unload(self):
        if self._cache_cleanup_task: self._cache_cleanup_task.cancel()
        if self._search_cleanup_task: self._search_cleanup_task.cancel()
    
    async def _cleanup_cache_task(self):
        while not self.bot.is_closed():
            try: await self._thread_cache.cleanup()
            except Exception as e: logger.error(f"[boundary:error] Cache cleanup failed: {e}")
            await asyncio.sleep(60) 
    
    async def _cleanup_searches_task(self):
        while not self.bot.is_closed():
            try:
                now = datetime.now()
                expired = [sid for sid, info in self._active_searches.items() if (now - info["start_time"]).total_seconds() > 600]
                if expired:
                    for sid in expired: self._active_searches.pop(sid, None)
                    logger.debug(f"[signal] Removed {len(expired)} expired searches")
            except Exception as e: logger.error(f"[boundary:error] Search cleanup failed: {e}")
            await asyncio.sleep(300)
    
    @lru_cache(maxsize=256)
    def _check_tags(self, thread_tags: Tuple[str], search_tags: Tuple[str], exclude_tags: Tuple[str]) -> bool:
        tags_lower = {tag.lower() for tag in thread_tags}
        return (not search_tags or any(tag in tags_lower for tag in search_tags)) and \
               (not exclude_tags or not any(tag in tags_lower for tag in exclude_tags))
    
    def _preprocess_keywords(self, keywords: List[str]) -> List[str]:
        return [kw.strip().lower() for kw in keywords if kw and kw.strip()]
    
    def _check_keywords(self, content: str, search_query: str, exclude_keywords: List[str]) -> bool:
        if not content: return not search_query
        content_lower = content.lower()
        
        if exclude_keywords and any(kw in content_lower for kw in exclude_keywords): return False
        if not search_query: return True
        
        tree = self._query_parser.parse_query(search_query)
        
        if tree["type"] == "simple": return all(kw in content_lower for kw in tree["keywords"])
        elif tree["type"] == "advanced": return self._query_parser.evaluate(tree["tree"], content)
        elif tree["type"] == "empty": return True
        
        return False
    
    async def _process_thread(self, thread: discord.Thread, conditions: Dict, cancel_event=None) -> Optional[Dict]:
        if not thread or not thread.id or (cancel_event and cancel_event.is_set()): return None
            
        async with self._search_semaphore:
            # Fast pre-checks
            if ((conditions.get('start_date') and thread.created_at < conditions['start_date']) or 
                (conditions.get('end_date') and thread.created_at > conditions['end_date'])): return None
            # Author check
            owner = getattr(thread, 'owner', None)
            if ((conditions.get('original_poster') and (not owner or owner.id != conditions['original_poster'].id)) or
                (conditions.get('exclude_op') and owner and owner.id == conditions['exclude_op'].id)): return None
            
            # Tag filtering
            thread_tags = tuple(tag.name for tag in getattr(thread, 'applied_tags', []))
            search_tags = tuple(conditions.get('search_tags', []))
            exclude_tags = tuple(conditions.get('exclude_tags', []))
            
            if not self._check_tags(thread_tags, search_tags, exclude_tags): return None
            # Cache check
            cached_thread = self._thread_cache.get(thread.id)
            if cached_thread:
                if self._check_keywords(cached_thread.get('content', ''), conditions.get('search_query', ''), 
                                       conditions.get('exclude_keywords', [])):
                    return cached_thread
                return None
                
            # Process thread content
            try:
                thread_data = {
                    'thread': thread,
                    'thread_id': thread.id,
                    'title': thread.name,
                    'author': owner,
                    'created_at': thread.created_at,
                    'is_archived': thread.archived,
                    'tags': thread_tags,
                    'stats': await self._thread_cache.get_thread_stats(thread),
                    'jump_url': thread.jump_url
                }
                
                # Get first message
                content = ""
                async for message in thread.history(limit=1, oldest_first=True):
                    content = message.content
                    thread_data['first_message'] = message
                    thread_data['first_message_id'] = message.id
                    break
                
                thread_data['content'] = content
                
                # Keyword check
                if not self._check_keywords(content, conditions.get('search_query', ''), 
                                          conditions.get('exclude_keywords', [])): return None
                
                # Additional filters
                if ((conditions.get('min_reactions') and thread_data['stats']['reaction_count'] < conditions['min_reactions']) or 
                    (conditions.get('min_replies') and thread_data['stats']['reply_count'] < conditions['min_replies'])): return None
                
                self._thread_cache.store(thread.id, thread_data)
                return thread_data
                    
            except Exception as e:
                logger.error(f"[boundary:error] Error processing thread {thread.id}: {e}", exc_info=True)
            return None
    
    async def _process_thread_batch(self, threads: List[discord.Thread], conditions: Dict, cancel_event=None) -> List[Dict]:
        if not threads or (cancel_event and cancel_event.is_set()): return []
        
        tasks = [self._process_thread(thread, conditions, cancel_event) for thread in threads]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        return [r for r in results if r and not isinstance(r, Exception)]
    
    async def _search_threads(self, forum, conditions, cancel_event, batch_size=50):
        results = []
        
        # Active threads
        active_threads = await forum.active_threads()
        if active_threads and not cancel_event.is_set():
            results.extend(await self._process_thread_batch(active_threads, conditions, cancel_event))
        
        # Archived threads
        if not cancel_event.is_set():
            try:
                archived_threads = []
                async for thread in forum.archived_threads():
                    if cancel_event.is_set(): break
                    
                    archived_threads.append(thread)
                    if len(archived_threads) >= batch_size:
                        results.extend(await self._process_thread_batch(archived_threads, conditions, cancel_event))
                        archived_threads = []
                
                if archived_threads and not cancel_event.is_set():
                    results.extend(await self._process_thread_batch(archived_threads, conditions, cancel_event))
                    
            except Exception as e: logger.error(f"[boundary:error] Error searching archived: {e}")
        
        # Sort results
        if cancel_event.is_set(): return []
        return self._sort_results(results, conditions.get('order', 'newest'))
    
    def _sort_results(self, threads, order):
        if not threads: return []
        
        if order == "newest": threads.sort(key=lambda t: t['created_at'], reverse=True)
        elif order == "oldest": threads.sort(key=lambda t: t['created_at'])
        elif order == "most_replies": threads.sort(key=lambda t: t['stats'].get('reply_count', 0), reverse=True)
        elif order == "least_replies": threads.sort(key=lambda t: t['stats'].get('reply_count', 0))
        elif order == "most_reactions": threads.sort(key=lambda t: t['stats'].get('reaction_count', 0), reverse=True)
        elif order == "least_reactions": threads.sort(key=lambda t: t['stats'].get('reaction_count', 0))
        elif order == "alphabetical": threads.sort(key=lambda t: t['title'].lower())
        elif order == "reverse_alphabetical": threads.sort(key=lambda t: t['title'].lower(), reverse=True)
            
        return threads
    
    def _parse_date(self, date_str: str) -> Optional[datetime]:
        if not date_str: return None
        
        date_str = date_str.strip().lower()
        now = datetime.now()
        
        try: return datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError: pass
        
        if date_str == "today": return now.replace(hour=0, minute=0, second=0, microsecond=0)
        if date_str == "yesterday": return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        
        if days_match := re.match(r"^(\d+)d$", date_str):
            return (now - timedelta(days=int(days_match.group(1)))).replace(hour=0, minute=0, second=0, microsecond=0)
        
        if months_match := re.match(r"^(\d+)m$", date_str):
            months = int(months_match.group(1))
            year, month = now.year, now.month - months
            while month <= 0: month, year = month + 12, year - 1
            return datetime(year, month, 1)
        
        return None
        
    def _store_search_history(self, user_id: int, search_word=None, forum_id=None):
        if user_id not in self._search_history: self._search_history[user_id] = []
        
        entry = {'timestamp': datetime.now(), 'search_word': search_word}
        if forum_id is not None: entry['forum_id'] = forum_id
        
        self._search_history[user_id].insert(0, entry)
        self._search_history[user_id] = self._search_history[user_id][:self.max_history]
    
    async def _build_search_conditions(self, interaction, **kwargs):
        try:
            # Parse dates
            start_dt = end_dt = None
            if sd := kwargs.get('start_date'):
                if not (start_dt := self._parse_date(sd)):
                    raise ValueError(f"Invalid start date: {sd}")
                    
            if ed := kwargs.get('end_date'):
                if not (end_dt := self._parse_date(ed)):
                    raise ValueError(f"Invalid end date: {ed}")
                if end_dt: end_dt += timedelta(days=1, microseconds=-1)
            
            # Build tag sets
            search_tags, exclude_tags = set(), set()
            for i in range(1, 4):
                if tag := kwargs.get(f'tag{i}'): search_tags.add(tag.lower())
            for i in range(1, 3):
                if tag := kwargs.get(f'exclude_tag{i}'): exclude_tags.add(tag.lower())
                
            # Return complete condition set
            return {
                'search_tags': search_tags,
                'exclude_tags': exclude_tags,
                'search_query': kwargs.get('search_word'),
                'exclude_keywords': self._preprocess_keywords(kwargs.get('exclude_word', "").split(",")),
                'original_poster': kwargs.get('original_poster'), 
                'exclude_op': kwargs.get('exclude_op'),
                'start_date': start_dt, 
                'end_date': end_dt,
                'min_reactions': kwargs.get('min_reactions'), 
                'min_replies': kwargs.get('min_replies'),
                'order': kwargs.get('order')
            }
            
        except ValueError as e:
            await interaction.followup.send(
                embed=self.embed_builder.create_error_embed("Date Error", str(e)), 
                ephemeral=True
            )
            return None
    
    async def _generate_result_embed(self, item: Dict, total_results: int, page_number: int) -> discord.Embed:
        thread, stats = item['thread'], item['stats']
        
        embed = discord.Embed(
            title=truncate_text(thread.name, 256), 
            url=thread.jump_url, 
            color=EMBED_COLOR
        )
        
        # Add author if available
        if owner := getattr(thread, 'owner', None): 
            embed.set_author(name=owner.display_name, icon_url=owner.display_avatar.url)
        
        # Add content and thumbnail
        if msg := item.get('first_message'):
            embed.description = f"**Summary:**\n{truncate_text(msg.content.strip(), 1000)}"
            if thumb := self.attachment_processor.get_first_image(msg):
                embed.set_thumbnail(url=thumb)
        
        # Add fields
        if tags := getattr(thread, 'applied_tags', None):
            embed.add_field(name="Tags", value=", ".join(t.name for t in tags), inline=True)
        
        embed.add_field(
            name="Stats", 
            value=f"👍 {stats.get('reaction_count', 0)} | 💬 {stats.get('reply_count', 0)}", 
            inline=True
        )
        
        last_active = getattr(thread.last_message, 'created_at', thread.created_at)
        embed.add_field(
            name="Time", 
            value=f"Created: {discord.utils.format_dt(thread.created_at, 'R')}\n"
                 f"Active: {discord.utils.format_dt(last_active, 'R')}", 
            inline=True
        )
        
        # Add pagination info
        start = page_number * MESSAGES_PER_PAGE + 1
        end = min((page_number + 1) * MESSAGES_PER_PAGE, total_results)
        embed.set_footer(text=f"Result {start}-{end} of {total_results}")
        
        return embed
    
    async def _present_results(self, interaction, forum, results, conditions, progress_message, order_value):
        if not results:
            await progress_message.edit(
                embed=self.embed_builder.create_info_embed(
                    "No Results", f"No matching threads found in {forum.mention}."
                ),
                view=None
            )
            return

        # Create summary embed
        summary = discord.Embed(
            title=f"Search Results: {forum.name}",
            description=f"Found {len(results)} matching threads",
            color=EMBED_COLOR
        )
        
        # Add search criteria
        criteria = []
        if conditions.get('search_tags'): criteria.append(f"🏷️ Tags: {', '.join(conditions['search_tags'])}")
        if conditions.get('exclude_tags'): criteria.append(f"🚫 ExTags: {', '.join(conditions['exclude_tags'])}")
        if conditions.get('search_query'): criteria.append(f"🔍 Keywords: {conditions['search_query']}")
        if conditions.get('exclude_keywords'): criteria.append(f"❌ ExWords: {', '.join(conditions['exclude_keywords'])}")
        if op := conditions.get('original_poster'): criteria.append(f"👤 By: {op.display_name}")
        if ex := conditions.get('exclude_op'): criteria.append(f"🚷 Not By: {ex.display_name}")
        if sd := conditions.get('start_date'): criteria.append(f"📅 From: {sd.strftime('%Y-%m-%d')}")
        if ed := conditions.get('end_date'): 
            criteria.append(f"📅 To: {(ed - timedelta(microseconds=1)).strftime('%Y-%m-%d')}")
        if mr := conditions.get('min_reactions'): criteria.append(f"👍 Min Reactions: {mr}")
        if mp := conditions.get('min_replies'): criteria.append(f"💬 Min Replies: {mp}")
            
        if criteria: summary.add_field(name="Search Criteria", value="\n".join(criteria), inline=False)
        
        # Create pagination view
        embeds = await asyncio.gather(*[
            self._generate_result_embed(result, len(results), 0) 
            for result in results[:MESSAGES_PER_PAGE]
        ])
        
        paginator = MultiEmbedPaginationView(
            items=results,
            items_per_page=MESSAGES_PER_PAGE,
            generate_embeds=lambda items, page: asyncio.gather(
                *[self._generate_result_embed(item, len(results), page) for item in items]
            )
        )
        
        # Update message and start pagination
        await progress_message.edit(embed=summary, view=None)
        await paginator.start(interaction, embeds)

    @app_commands.command(name="forum_search", description="Search forum posts")
    @app_commands.describe(
        forum="Forum channel to search in",
        order="Order of results (default: newest)",
        original_poster="Filter by original poster",
        exclude_op="Exclude posts by this user",
        tag1="Include posts with this tag", tag2="Include posts with this tag", tag3="Include posts with this tag",
        exclude_tag1="Exclude posts with this tag", exclude_tag2="Exclude posts with this tag",
        search_word="Search for words in titles and content",
        exclude_word="Exclude posts with these words (comma-separated)",
        start_date="Include posts after this date (YYYY-MM-DD or Nd/Nm)",
        end_date="Include posts before this date (YYYY-MM-DD or Nd/Nm)",
        min_reactions="Minimum number of reactions",
        min_replies="Minimum number of replies"
    )
    async def forum_search(
        self, interaction: discord.Interaction,
        forum: discord.ForumChannel,
        order: Optional[SearchOrder] = SearchOrder.newest,
        original_poster: Optional[discord.Member] = None,
        exclude_op: Optional[discord.Member] = None,
        tag1: Optional[str] = None, tag2: Optional[str] = None, tag3: Optional[str] = None,
        exclude_tag1: Optional[str] = None, exclude_tag2: Optional[str] = None,
        search_word: Optional[str] = None, exclude_word: Optional[str] = None,
        start_date: Optional[str] = None, end_date: Optional[str] = None,
        min_reactions: Optional[int] = None, min_replies: Optional[int] = None
    ):
        # Validation
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server", ephemeral=True)
            return

        perms = forum.permissions_for(interaction.guild.me)
        if not (perms.read_messages and perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                f"I need read/send/embed permissions in {forum.mention}", ephemeral=True
            )
            return

        if not any([original_poster, tag1, tag2, tag3, search_word, start_date, end_date]):
            await interaction.response.send_message(
                "Please provide at least one search criteria", ephemeral=True
            )
            return

        # Begin search
        await interaction.response.defer(thinking=True)
        self._store_search_history(interaction.user.id, search_word, forum.id)
        
        # Build conditions
        conditions = await self._build_search_conditions(
            interaction, original_poster=original_poster, exclude_op=exclude_op,
            tag1=tag1, tag2=tag2, tag3=tag3, exclude_tag1=exclude_tag1, exclude_tag2=exclude_tag2,
            search_word=search_word, exclude_word=exclude_word, start_date=start_date, end_date=end_date,
            min_reactions=min_reactions, min_replies=min_replies, order=order.value
        )
        if not conditions: return
        
        # Search with progress updates
        cancel_event = asyncio.Event()
        search_task = asyncio.create_task(self._search_threads(forum, conditions, cancel_event))
        
        cancel_view = CancelView(cancel_event)
        progress_message = await interaction.followup.send(
            embed=self.embed_builder.create_info_embed(
                "Searching...", f"Looking for matches in {forum.mention}..."
            ),
            view=cancel_view
        )
        
        search_task.add_done_callback(lambda _: asyncio.create_task(cancel_view.disable_buttons()))
        
        try:
            results = await search_task
            
            if cancel_event.is_set():
                await progress_message.edit(
                    embed=self.embed_builder.create_info_embed("Cancelled", "Search was cancelled"),
                    view=None
                )
                return

            await self._present_results(interaction, forum, results, conditions, progress_message, order.value)
        except Exception as e:
            logger.exception(f"Search error: {e}")
            await progress_message.edit(
                embed=self.embed_builder.create_error_embed("Error", f"An error occurred: {str(e)}"),
                view=None
            )
    
    @forum_search.autocomplete('forum')
    async def forum_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        if not interaction.guild: return []
        
        forums = [ch for ch in interaction.guild.channels 
                 if isinstance(ch, discord.ForumChannel) and 
                 (not current or current.lower() in ch.name.lower())]
        
        forums.sort(key=lambda ch: ch.name.lower())
        return [app_commands.Choice(name=f"#{ch.name}", value=ch.id) for ch in forums[:25]]

    @forum_search.autocomplete('tag1')
    @forum_search.autocomplete('tag2')
    @forum_search.autocomplete('tag3')
    @forum_search.autocomplete('exclude_tag1')
    @forum_search.autocomplete('exclude_tag2')
    async def tag_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        if not interaction.guild: return []

        forum_id = None
        for option in interaction.data.get("options", []):
            if option["name"] == "forum" and "value" in option:
                forum_id = option["value"]
                break

        if not forum_id: return []
        forum = interaction.guild.get_channel(int(forum_id))
        if not isinstance(forum, discord.ForumChannel): return []

        selected_tags = set()
        for option in interaction.data.get("options", []):
            if option["name"].startswith(("tag", "exclude_tag")) and "value" in option:
                selected_tags.add(option["value"].lower())

        available_tags = [
            tag for tag in forum.available_tags
            if tag.name.lower() not in selected_tags and
               (not current or current.lower() in tag.name.lower())
        ]
        
        available_tags.sort(key=lambda tag: tag.name.lower())
        return [app_commands.Choice(name=tag.name, value=tag.name) for tag in available_tags[:25]]
        
    @forum_search.autocomplete('start_date')
    @forum_search.autocomplete('end_date')
    async def date_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        today = datetime.now()
        
        suggestions = [
            ("Today", today.strftime("%Y-%m-%d")),
            ("Yesterday", (today - timedelta(days=1)).strftime("%Y-%m-%d")),
            ("Last Week", (today - timedelta(days=7)).strftime("%Y-%m-%d")),
            ("Last Month", (today - timedelta(days=30)).strftime("%Y-%m-%d")),
            ("Last 3 Months", (today - timedelta(days=90)).strftime("%Y-%m-%d")),
            ("Last 6 Months", (today - timedelta(days=180)).strftime("%Y-%m-%d")),
            ("Last Year", (today - timedelta(days=365)).strftime("%Y-%m-%d")),
        ]
        
        filtered = [
            (name, value) for name, value in suggestions
            if not current or current.lower() in name.lower() or current.lower() in value.lower()
        ]
            
        return [app_commands.Choice(name=f"{name} ({value})", value=value) 
                for name, value in filtered[:25]]
    
    @app_commands.command(name="search_syntax", description="Show search syntax help")
    async def search_syntax(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="Search Syntax", 
            description="Forum search supports these syntax features:",
            color=EMBED_COLOR
        )
        
        embed.add_field(name="Basic Keywords", value="Multiple keywords use AND logic\n`issue solution`", inline=False)
        embed.add_field(name="OR Operator", value="Match any keyword using `OR` or `|`\n`solution OR workaround`", inline=False)
        embed.add_field(name="NOT Operator", value="Exclude words with `NOT` or `-`\n`issue -resolved`", inline=False)
        embed.add_field(name="Exact Phrases", value="Use quotes for exact matching\n`\"complete phrase match\"`", inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="search_history", description="View your recent searches")
    async def search_history(self, interaction: discord.Interaction):
        history = self._search_history.get(interaction.user.id, [])
        
        if not history:
            await interaction.response.send_message("No search history found", ephemeral=True)
            return
            
        embed = discord.Embed(
            title="Your Recent Searches", 
            description=f"Last {len(history)} searches",
            color=EMBED_COLOR
        )
        
        for i, search in enumerate(history[:10], 1):
            timestamp = search.get('timestamp', datetime.now())
            search_term = search.get('search_word', 'No terms')
            
            forum_text = "Unknown forum"
            if forum_id := search.get('forum_id'):
                if forum := interaction.guild.get_channel(forum_id):
                    forum_text = f"#{forum.name}"
                
                embed.add_field(
                    name=f"{i}. {discord.utils.format_dt(timestamp, 'R')}",
                    value=f"Forum: {forum_text}\nQuery: {search_term or 'N/A'}",
                    inline=False
                )
            
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Search(bot))