import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, List, Dict, Tuple, Any, Union
import logging, re, uuid, asyncio
from functools import lru_cache
from datetime import datetime, timedelta

from config.config import (
    MAX_MESSAGES_PER_SEARCH, MESSAGES_PER_PAGE,
    EMBED_COLOR, SEARCH_ORDER_OPTIONS, CONCURRENT_SEARCH_LIMIT
)
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
        
        # Return cached value if valid
        if cache_key in self._stats_cache and current_time - self._stats_cache[cache_key]['timestamp'] < self._ttl:
            return self._stats_cache[cache_key]['data']
        
        # Cache miss - fetch and store
        try:
            stats = await get_thread_stats(thread)
            self._stats_cache[cache_key] = {'data': stats, 'timestamp': current_time}
            return stats
        except Exception:
            return {'reaction_count': 0, 'reply_count': 0}
    
    def store(self, thread_id: int, data: Any) -> None:
        self._cache[thread_id] = {'data': data, 'timestamp': datetime.now().timestamp()}
    
    def get(self, thread_id: int) -> Optional[Any]:
        if thread_id in self._cache and datetime.now().timestamp() - self._cache[thread_id]['timestamp'] < self._ttl:
            return self._cache[thread_id]['data']
        return None
    
    async def cleanup(self) -> int:
        current_time = datetime.now().timestamp()
        if current_time - self._last_cleanup < 60:  # Max once per minute
            return 0
            
        self._last_cleanup = current_time
        
        # Find and remove expired entries
        expired_thread = [k for k, v in self._cache.items() if current_time - v['timestamp'] > self._ttl]
        expired_stats = [k for k, v in self._stats_cache.items() if current_time - v['timestamp'] > self._ttl]
        
        for k in expired_thread: del self._cache[k]
        for k in expired_stats: del self._stats_cache[k]
        
        return len(expired_thread) + len(expired_stats)

class Search(commands.Cog, name="search"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.embed_builder = DiscordEmbedBuilder(EMBED_COLOR)
        self.attachment_processor = AttachmentProcessor()
        
        # Core state
        self._thread_cache = ThreadCache(ttl=300)
        self._active_searches = {}
        self._search_history = {}
        self._query_parser = SearchQueryParser()
        self._search_semaphore = asyncio.Semaphore(CONCURRENT_SEARCH_LIMIT)
        
        # Compiled patterns
        self._url_pattern = re.compile(r'https?://\S+')
        
        # Sorting functions map
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
        # Cancel background tasks on unload
        self._cache_cleanup_task and self._cache_cleanup_task.cancel()
        self._search_cleanup_task and self._search_cleanup_task.cancel()
    
    async def _cleanup_cache_task(self):
        # Periodically clean up cache
        while not self.bot.is_closed():
            try: await self._thread_cache.cleanup()
            except Exception as e: logger.error(f"Cache cleanup error: {e}")
            await asyncio.sleep(60) 
    
    async def _cleanup_searches_task(self):
        # Clean up old searches (older than 10 mins)
        while not self.bot.is_closed():
            try:
                now = datetime.now()
                expired = [sid for sid, info in self._active_searches.items() 
                           if (now - info["start_time"]).total_seconds() > 600]
                for sid in expired: self._active_searches.pop(sid, None)
                expired and logger.debug(f"Removed {len(expired)} expired searches")
            except Exception as e: logger.error(f"Search cleanup error: {e}")
            await asyncio.sleep(300)
    
    @lru_cache(maxsize=256)
    def _check_tags(self, thread_tags: Tuple[str], search_tags: Tuple[str], exclude_tags: Tuple[str]) -> bool:
        tags_lower = {tag.lower() for tag in thread_tags}
        # Check inclusions and exclusions
        return (not search_tags or any(tag in tags_lower for tag in search_tags)) and \
               (not exclude_tags or not any(tag in tags_lower for tag in exclude_tags))
    
    def _preprocess_keywords(self, keywords: List[str]) -> List[str]:
        return [kw.strip().lower() for kw in keywords if kw and kw.strip()]
    
    def _check_keywords(self, content: str, search_query: str, exclude_keywords: List[str]) -> bool:
        if not content: return not search_query
        content_lower = content.lower()
        
        # Check exclusions first
        if exclude_keywords and any(kw in content_lower for kw in exclude_keywords): return False
        if not search_query: return True
            
        # Evaluate query tree
        tree = self._query_parser.parse_query(search_query)
        return (
            all(kw in content_lower for kw in tree["keywords"]) if tree["type"] == "simple"
            else self._query_parser.evaluate(tree["tree"], content) if tree["type"] == "advanced"
            else True if tree["type"] == "empty"
            else False # Logged unknown type handled by parser
        )
    
    async def _process_single_thread(self, thread: discord.Thread, conditions: Dict, 
                                    cancel_event=None) -> Optional[Dict]:
        if not thread or not thread.id or (cancel_event and cancel_event.is_set()): return None

        async with self._search_semaphore:
            # Pre-checks: Date range, Author, Tags
            if (cond_val := conditions.get('start_date')) and thread.created_at < cond_val: return None
            if (cond_val := conditions.get('end_date')) and thread.created_at > cond_val: return None
            
            owner = getattr(thread, 'owner', None)
            if (op := conditions.get('original_poster')) and (not owner or owner.id != op.id): return None
            if (ex_op := conditions.get('exclude_op')) and owner and owner.id == ex_op.id: return None
            
            if not self._check_tags(tuple(t.name for t in thread.applied_tags), 
                                    tuple(conditions.get('search_tags', [])), 
                                    tuple(conditions.get('exclude_tags', []))): return None
            
            # Fetch message (costly operation)
            msg = None
            for retry in range(3):
                if cancel_event and cancel_event.is_set(): return None
                try:
                    msg = await thread.fetch_message(thread.id)
                    break
                except discord.NotFound: return None
                except discord.HTTPException as e: 
                    if e.status == 429 or 500 <= e.status < 600: await asyncio.sleep(1 * (retry + 1)); continue
                    logger.warning(f"Msg fetch HTTP fail: {thread.id}, {e.status}"); return None
                except Exception as e: logger.warning(f"Msg fetch error: {thread.id}, {e}"); return None
            if not msg: return None # Failed after retries
            
            # Check content keywords
            if (query := conditions.get('search_query')) and not self._check_keywords(
                msg.content, query, conditions.get('exclude_keywords', [])): return None
            
            # Check stats (potentially cached)
            try:
                if cancel_event and cancel_event.is_set(): return None
                stats = await self._thread_cache.get_thread_stats(thread)
                if (cond_val := conditions.get('min_reactions')) is not None and stats.get('reaction_count', 0) < cond_val: return None
                if (cond_val := conditions.get('min_replies')) is not None and stats.get('reply_count', 0) < cond_val: return None
            except Exception: stats = {'reaction_count': 0, 'reply_count': 0}
            
            # Passed all filters
            return {'thread': thread, 'stats': stats, 'first_message': msg}
    
    async def _process_thread_batch(self, threads: List[discord.Thread], conditions: Dict, cancel_event=None) -> List[Dict]:
        if not threads or (cancel_event and cancel_event.is_set()): return []
        
        # Sequential for small batches
        if len(threads) <= 3:
            results = [await self._process_single_thread(t, conditions, cancel_event) for t in threads if not (cancel_event and cancel_event.is_set())]
            return [r for r in results if r]
            
        # Concurrent for larger batches
        tasks = [asyncio.create_task(self._process_single_thread(t, conditions, cancel_event)) for t in threads if not (cancel_event and cancel_event.is_set())]
        if not tasks: return []
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, dict)] # Filter out None and Exceptions
    
    async def _search_archived_threads(self, forum_channel, conditions, progress_message, search_id, max_results=1000, total_active=0):
        results, processed, last_thread, errors, batch_count = [], total_active, None, 0, 0
        cancel_event = self._active_searches.get(search_id, {}).get("cancel_event")
        start_time, last_update = datetime.now(), datetime.now()
        
        while True:
            if cancel_event and cancel_event.is_set(): 
                msg = f"✓ Processed: {processed} | 📊 Matched: {len(results)} | ⏱️ Time: {(datetime.now() - start_time).total_seconds():.1f}s"
                await progress_message.edit(embed=self.embed_builder.create_warning_embed("Search cancelled", msg))
                return results
            
            if len(results) >= max_results: 
                msg = f"🔍 Max results ({max_results}) | ✓ Processed: {processed} | ⏱️ Time: {(datetime.now() - start_time).total_seconds():.1f}s"
                await progress_message.edit(embed=self.embed_builder.create_info_embed("Search limit reached", msg))
                return results
            
            try:
                batch = [t async for t in forum_channel.archived_threads(limit=100, before=last_thread)]
                if not batch: break
                last_thread = batch[-1]
                batch_count += 1
                
                batch_results = await self._process_thread_batch(batch, conditions, cancel_event)
                results.extend(batch_results or [])
                processed += len(batch)
                
                now = datetime.now()
                if (now - last_update).total_seconds() >= 1.5:
                    elapsed = (now - start_time).total_seconds()
                    msg = f"✓ Processed: {processed} | 📊 Matched: {len(results)} | ⏱️ Time: {elapsed:.1f}s | 📦 Batches: {batch_count}"
                    await progress_message.edit(embed=self.embed_builder.create_info_embed("Searching Archives...", msg))
                    last_update = now
            
            except Exception as e:
                errors += 1
                logger.error(f"Archived thread fetch error: {e}")
                now = datetime.now()
                if (now - last_update).total_seconds() >= 2:
                    msg = f"❌ Error batch {batch_count} | ✓ Processed: {processed} | 📊 Matched: {len(results)}"
                    await progress_message.edit(embed=self.embed_builder.create_warning_embed("Search continuing...", msg))
                    last_update = now
                if errors >= 3: break
                await asyncio.sleep(2)
        
        return results
    
    def _parse_date(self, date_str: str) -> Optional[datetime]:
        if not date_str: return None
        formats = ["%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d.%m.%Y"]
        for fmt in formats: 
            try: return datetime.strptime(date_str, fmt)
            except ValueError: continue
        
        if match := re.match(r'^(\d+)([dmyw])$', date_str.lower()):
            num, unit = int(match.group(1)), match.group(2)
            delta_map = {'d': num, 'w': num * 7, 'm': num * 30, 'y': num * 365}
            return datetime.now() - timedelta(days=delta_map.get(unit, 0))
        return None

    def _store_search_history(self, user_id: int, search_info: Dict) -> None:
        history = self._search_history.setdefault(user_id, [])
        history.insert(0, {**search_info, 'timestamp': datetime.now()})
        self._search_history[user_id] = history[:10] # Keep only last 10

    @app_commands.command(name="search_syntax", description="Show advanced search syntax instructions")
    @app_commands.guild_only()
    async def search_syntax(self, interaction: discord.Interaction):
        """Show syntax guide for advanced search"""
        embed = discord.Embed(
            title="Advanced Search Syntax",
            description="Forum search supports these syntax features:",
            color=EMBED_COLOR
        )
        
        # Syntax patterns presented as a map of concepts to examples
        patterns = {
            "Basic Keywords": 
                "Multiple keywords use AND logic\n`issue solution`",
                
            "OR Operator": 
                "Match any keyword using `OR` or `|`\n`solution OR workaround`\n`topic | content | title`",
                
            "NOT Operator": 
                "Exclude words with `NOT` or `-`\n`issue NOT resolved`\n`issue -resolved`",
                
            "Exact Phrases": 
                "Use quotes for exact matching\n`\"complete phrase match\"`",
                
            "Combined": 
                "Operators can be combined\n`(topic | content) NOT \"resolved\"`"
        }
        
        # Add each pattern to the embed
        for name, description in patterns.items():
            embed.add_field(name=name, value=description, inline=False)
            
        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _build_search_conditions(self, interaction, **kwargs) -> Optional[Dict]:
        try:
            start_dt = self._parse_date(sd := kwargs.get('start_date')) if sd else None
            end_dt = self._parse_date(ed := kwargs.get('end_date')) if ed else None
            if sd and not start_dt: raise ValueError(f"Invalid start date: {sd}")
            if ed and not end_dt: raise ValueError(f"Invalid end date: {ed}")
            if end_dt: end_dt += timedelta(days=1, microseconds=-1)

            # Build and return conditions
            search_tag_keys = [f'tag{i}' for i in range(1, 4)]
            exclude_tag_keys = [f'exclude_tag{i}' for i in range(1, 3)]
            return {
                'search_tags': {kwargs.get(key).lower() for key in search_tag_keys if kwargs.get(key) is not None},
                'exclude_tags': {kwargs.get(key).lower() for key in exclude_tag_keys if kwargs.get(key) is not None},
                'search_query': kwargs.get('search_word'),
                'exclude_keywords': self._preprocess_keywords(kwargs.get('exclude_word', "").split(",")),
                'original_poster': kwargs.get('original_poster'), 'exclude_op': kwargs.get('exclude_op'),
                'start_date': start_dt, 'end_date': end_dt,
                'min_reactions': kwargs.get('min_reactions'), 'min_replies': kwargs.get('min_replies')
            }
        except ValueError as e:
            await interaction.followup.send(embed=self.embed_builder.create_error_embed("Date Error", str(e)), ephemeral=True)
            return None

    async def _execute_search(self, forum_channel, conditions, progress_message, search_id, cancel_event) -> List[Dict]:
        """Execute search on active and archived threads"""
        results = []
        processed = 0
        search_start = datetime.now()

        # Search active threads
        active_threads = forum_channel.threads
        if active_threads:
            try:
                active_results = await self._process_thread_batch(active_threads, conditions, cancel_event)
                results.extend(active_results or [])
                processed += len(active_threads)
                elapsed = (datetime.now() - search_start).total_seconds()
                await progress_message.edit(
                    embed=self.embed_builder.create_info_embed("Searching...", 
                                                              f"✓ Active: {processed} | 📊 Matches: {len(results)} | ⏱️ Time: {elapsed:.1f}s\n⏳ Searching archives...")
                )
            except Exception as e:
                logger.error(f"Active search error: {e}")
                await progress_message.edit(embed=self.embed_builder.create_warning_embed("Searching...", f"❌ Error in active posts | ⏳ Continuing..."))
        
        # Search archived threads
        if not cancel_event.is_set():
            try:
                archived_results = await self._search_archived_threads(
                    forum_channel, conditions, progress_message, search_id,
                    max_results=MAX_MESSAGES_PER_SEARCH - len(results), total_active=processed
                )
                results.extend(archived_results or [])
            except Exception as e:
                logger.error(f"Archived search error: {e}")

        return results

    async def _generate_result_embed(self, item: Dict, total_results: int, page_number: int) -> discord.Embed:
        thread, stats, msg = item['thread'], item['stats'], item['first_message']
        embed = discord.Embed(title=truncate_text(thread.name, 256), url=thread.jump_url, color=EMBED_COLOR)
        if owner := getattr(thread, 'owner', None): embed.set_author(name=owner.display_name, icon_url=owner.display_avatar.url)
        if msg and msg.content: 
            embed.description = f"**Summary:**\n{truncate_text(msg.content.strip(), 1000)}"
            if thumb := self.attachment_processor.get_first_image(msg): embed.set_thumbnail(url=thumb)
        
        # Dynamic fields
        fields = []
        if tags := getattr(thread, 'applied_tags', None): fields.append(("Tags", ", ".join(t.name for t in tags), True))
        reactions, replies = stats.get('reaction_count', 0), stats.get('reply_count', 0)
        fields.append(("Stats", f"👍 {reactions} | 💬 {replies}", True))
        last_active = getattr(thread.last_message, 'created_at', thread.created_at)
        fields.append(("Time", f"Created: {discord.utils.format_dt(thread.created_at, 'R')}\nActive: {discord.utils.format_dt(last_active, 'R')}", True))
        for name, value, inline in fields: embed.add_field(name=name, value=value, inline=inline)
        
        # Footer
        start, end = page_number * MESSAGES_PER_PAGE + 1, min((page_number + 1) * MESSAGES_PER_PAGE, total_results)
        embed.set_footer(text=f"Result {start}-{end} of {total_results}")
        return embed

    async def _present_results(self, interaction, results: List[Dict], order: str, conditions: Dict, start_time: datetime):
        total_time = (datetime.now() - start_time).total_seconds()
        if not results:
            await interaction.followup.send(embed=self.embed_builder.create_warning_embed("No Results", "No posts matched criteria"), ephemeral=True)
            return

        # Store simplified history
        hist_cond = {k: v for k, v in conditions.items() if v and k not in ['original_poster', 'exclude_op']} 
        if op := conditions.get('original_poster'): hist_cond['op_id'] = op.id
        if ex := conditions.get('exclude_op'): hist_cond['ex_op_id'] = ex.id
        self._store_search_history(interaction.user.id, {
            'forum': interaction.channel.name, 'conditions': hist_cond, 
            'results_count': len(results), 'duration': total_time
        })

        # Sort & Paginate
        sort_key, reverse = self._sort_functions.get(order, self._sort_functions["Post Time (Newest First)"])
        results.sort(key=sort_key, reverse=reverse)

        async def embed_gen(items, page):
            tasks = [self._generate_result_embed(item, len(results), page) for item in items]
            return await asyncio.gather(*tasks)

        paginator = MultiEmbedPaginationView(items=results, items_per_page=MESSAGES_PER_PAGE, generate_embeds=embed_gen)
        if initial_items := paginator.get_page_items(0):
            await paginator.start(interaction, await embed_gen(initial_items, 0))
        else:
            await interaction.followup.send(embed=self.embed_builder.create_error_embed("Error", "Could not generate results page"), ephemeral=True)

    @app_commands.command(name="forum_search", description="Search forum posts")
    @app_commands.guild_only()
    @app_commands.describe(
        forum_name="Forum", order="Sort", original_poster="OP", tag1="Tag1", tag2="Tag2", tag3="Tag3", 
        search_word="Keywords", exclude_word="ExcludeKW", exclude_op="ExcludeOP", exclude_tag1="ExTag1", 
        exclude_tag2="ExTag2", start_date="Start", end_date="End", min_reactions="MinReact", min_replies="MinReply"
    )
    @app_commands.choices(order=[app_commands.Choice(name=o, value=o) for o in SEARCH_ORDER_OPTIONS])
    async def forum_search(self, interaction: discord.Interaction, forum_name: str, order: str = "Reactions (High to Low)", **kwargs):
        """Main handler: orchestrates validation, search execution, and result presentation."""
        search_id = str(uuid.uuid4())
        cancel_event = asyncio.Event()
        self._active_searches[search_id] = {"cancel_event": cancel_event, "start_time": datetime.now()}
        start_time = datetime.now()
        
        try:
            # Initial Setup & Validation
            if not interaction.guild: raise commands.NoPrivateMessage()
            perms = interaction.channel.permissions_for(interaction.guild.me)
            if not (perms.send_messages and perms.embed_links): raise commands.BotMissingPermissions(["Send Messages", "Embed Links"])
            await interaction.response.defer(ephemeral=True)
            
            try: forum_channel = interaction.guild.get_channel(int(forum_name))
            except ValueError: raise commands.BadArgument("Invalid forum channel ID format.")
            if not isinstance(forum_channel, discord.ForumChannel): raise commands.BadArgument("Channel is not a forum.")

            conditions = await self._build_search_conditions(interaction, **kwargs)
            if conditions is None: return # Error handled in helper

            # Criteria Summary & Progress Message Setup
            criteria_map = {
                'search_tags': ("🏷️ Tags", ", ".join), 'exclude_tags': ("🚫 ExTags", ", ".join),
                'search_query': ("🔍 KW", str), 'exclude_keywords': ("❌ ExKW", ", ".join),
                'original_poster': ("👤 By", lambda u: u.display_name), 'exclude_op': ("🚷 NotBy", lambda u: u.display_name),
                'start_date': ("📅 From", lambda d: d.strftime('%Y-%m-%d')), 'end_date': ("📅 To", lambda d: d.strftime('%Y-%m-%d')),
                'min_reactions': ("👍 Min 👍", str), 'min_replies': ("💬 Min 💬", str)
            }
            criteria_text = "\n".join([f"{label}: {fmt(conditions[k])}" for k, (label, fmt) in criteria_map.items() if conditions.get(k)]) or "No specific criteria"
            
            cancel_view = discord.ui.View(timeout=300)
            cancel_button = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, custom_id=f"cancel_{search_id}")
            async def cancel_callback(intr: discord.Interaction): 
                if sid := intr.data['custom_id'].split('_')[-1]: self._active_searches.get(sid, {}).get("cancel_event").set()
                await intr.response.edit_message(content="Search cancelled.", view=None, embed=None)
            cancel_button.callback = cancel_callback
            cancel_view.add_item(cancel_button)

            progress_message = await interaction.followup.send(
                embed=self.embed_builder.create_info_embed("Searching...", f"📋 Criteria:\n{criteria_text}\n\n⏳ Processing..."),
                view=cancel_view, ephemeral=True
            )

            # Execute & Present
            results = await self._execute_search(forum_channel, conditions, progress_message, search_id, cancel_event)
            # Cleanup progress message view
            try: 
                await progress_message.edit(view=None) 
            except Exception: 
                pass # Ignore cleanup errors
            
            if not cancel_event.is_set(): await self._present_results(interaction, results, order, conditions, start_time)

        # Specific Error Handling
        except (commands.NoPrivateMessage, commands.BotMissingPermissions, commands.BadArgument) as e:
            await interaction.followup.send(embed=self.embed_builder.create_error_embed("Setup Error", str(e)), ephemeral=True)
        except discord.NotFound:
            await interaction.followup.send(embed=self.embed_builder.create_error_embed("Error", "Resource not found."), ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(embed=self.embed_builder.create_error_embed("Error", "Missing permissions."), ephemeral=True)
        except Exception as e:
            logger.error(f"Search command error: {e}", exc_info=True)
            await interaction.followup.send(embed=self.embed_builder.create_error_embed("Error", "An unexpected error occurred."), ephemeral=True)
        
        finally: self._active_searches.pop(search_id, None)

    @forum_search.autocomplete('forum_name')
    async def forum_name_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Forum name autocomplete with history prioritization"""
        if not interaction.guild:
            return []
        
        # Get recent forums from user's search history
        user_id = interaction.user.id
        recent_forums = {
            search['forum'] for search in self._search_history.get(user_id, [])
            if 'forum' in search and search['forum']
        }
        
        # Get matching forum channels
        forum_channels = [
            (channel, channel.name in recent_forums)  # (channel, is_recent)
            for channel in interaction.guild.channels
            if isinstance(channel, discord.ForumChannel) and 
               (not current or current.lower() in channel.name.lower())
        ]
        
        # Sort by recency first, then alphabetically
        forum_channels.sort(key=lambda x: (not x[1], x[0].name.lower()))
        
        # Create choices (max 25)
        return [
            app_commands.Choice(
                name=f"#{channel.name}" + (" (recent)" if is_recent else ""),
                value=str(channel.id)
            )
            for channel, is_recent in forum_channels[:25]
        ]

    @forum_search.autocomplete('tag1')
    @forum_search.autocomplete('tag2')
    @forum_search.autocomplete('tag3')
    @forum_search.autocomplete('exclude_tag1')
    @forum_search.autocomplete('exclude_tag2')
    async def tag_autocomplete(self, interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
        """Tag autocomplete with history prioritization"""
        if not interaction.guild:
            return []

        # Find selected forum_name in options
        forum_id = next((
            option["value"] for option in interaction.data.get("options", [])
            if option["name"] == "forum_name"
        ), None)
        
        if not forum_id:
            return []

        # Get forum channel
        forum_channel = interaction.guild.get_channel(int(forum_id))
        if not isinstance(forum_channel, discord.ForumChannel):
            return []

        # Get already selected tags
        selected_tags = {
            option.get("value") for option in interaction.data.get("options", [])
            if option["name"].startswith(("tag", "exclude_tag")) and option.get("value")
        }

        # Count tag usage frequency from history
        user_id = interaction.user.id
        tag_frequency = {}
        for search in self._search_history.get(user_id, []):
            if 'conditions' in search and 'search_tags' in search['conditions']:
                for tag in search['conditions']['search_tags']:
                    tag_frequency[tag] = tag_frequency.get(tag, 0) + 1

        # Filter and prioritize available tags
        filtered_tags = [
            (tag, tag_frequency.get(tag.name.lower(), 0))
            for tag in forum_channel.available_tags
            if tag.name not in selected_tags and
               (not current or current.lower() in tag.name.lower()) and
               (not tag.moderated or interaction.user.guild_permissions.manage_threads)
        ]

        # Sort by frequency (most used first) then alphabetically
        filtered_tags.sort(key=lambda x: (-x[1], x[0].name.lower()))
        
        # Return choices (max 25)
        return [
            app_commands.Choice(
                name=tag.name + (" 🔄" if freq > 0 else ""),
                value=tag.name
            )
            for tag, freq in filtered_tags[:25]
        ]

    @app_commands.command(name="search_history", description="View your search history")
    @app_commands.guild_only()
    async def search_history(self, interaction: discord.Interaction):
        """Display user's search history"""
        user_id = interaction.user.id
        history = self._search_history.get(user_id, [])
        
        if not history:
            await interaction.response.send_message(
                embed=self.embed_builder.create_info_embed("Search History", "No searches performed yet"),
                ephemeral=True
            )
            return
        
        # Create embed with search history
        embed = discord.Embed(
            title="Your Search History",
            description="Recent searches",
            color=EMBED_COLOR
        )
        
        # Add each search (limit to 5)
        for i, search in enumerate(history[:5], 1):
            forum = search.get('forum', 'Unknown Forum')
            timestamp = search.get('timestamp', datetime.now())
            results_count = search.get('results_count', 0)
            duration = search.get('duration', 0)
            
            # Build conditions summary
            conditions = search.get('conditions', {})
            summary_parts = []
            
            if conditions.get('search_tags'):
                tags = list(conditions['search_tags'])[:2]
                summary_parts.append(f"Tags: {', '.join(tags)}" + ("..." if len(conditions['search_tags']) > 2 else ""))
            
            if conditions.get('search_query'):
                summary_parts.append(f"Keywords: {conditions['search_query']}")
            
            if conditions.get('original_poster'):
                summary_parts.append(f"By: {conditions['original_poster'].display_name}")
            
            summary = " | ".join(summary_parts) or "No specific criteria"
            
            embed.add_field(
                name=f"{i}. {forum} ({discord.utils.format_dt(timestamp, 'R')})",
                value=f"{summary}\nResults: {results_count} | Time: {duration:.1f}s",
                inline=False
            )
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Search(bot))