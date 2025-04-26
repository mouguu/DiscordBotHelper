import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, List, Dict, Tuple, Any
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
    def __init__(self, ttl=300):
        self._cache, self._stats_cache, self._ttl, self._last_cleanup = {}, {}, ttl, datetime.now().timestamp()
    
    async def get_thread_stats(self, thread):
        k, t = f"stats_{thread.id}", datetime.now().timestamp()
        if k in self._stats_cache and t - self._stats_cache[k]['timestamp'] < self._ttl: return self._stats_cache[k]['data']
        try: stats = await get_thread_stats(thread); self._stats_cache[k] = {'data': stats, 'timestamp': t}; return stats
        except Exception as e: logger.error(f"[boundary:error] Thread stats fetch: {e}"); return {'reaction_count': 0, 'reply_count': 0}
    
    def store(self, tid, data): self._cache[tid] = {'data': data, 'timestamp': datetime.now().timestamp()}
    def get(self, tid): return self._cache[tid]['data'] if tid in self._cache and datetime.now().timestamp() - self._cache[tid]['timestamp'] < self._ttl else None
    
    async def cleanup(self):
        t = datetime.now().timestamp()
        if t - self._last_cleanup < 60: return 0
        self._last_cleanup = t
        expt = [k for k, v in self._cache.items() if t - v['timestamp'] > self._ttl]
        exps = [k for k, v in self._stats_cache.items() if t - v['timestamp'] > self._ttl]
        for k in expt: del self._cache[k]
        for k in exps: del self._stats_cache[k]
        c = len(expt) + len(exps)
        if c > 0: logger.debug(f"[signal] Cleaned {c} cache entries")
        return c

class SearchOrder(str, enum.Enum):
    newest, oldest = "newest", "oldest"
    most_replies, least_replies = "most_replies", "least_replies"
    most_reactions, least_reactions = "most_reactions", "least_reactions"
    alphabetical, reverse_alphabetical = "alphabetical", "reverse_alphabetical"
    @classmethod
    def _missing_(cls, value): return cls.newest

class CancelView(discord.ui.View):
    def __init__(self, cancel_event): super().__init__(timeout=300); self.cancel_event = cancel_event
    @discord.ui.button(label="Cancel Search", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction, button):
        self.cancel_event.set()
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)
    async def disable_buttons(self): 
        for item in self.children: item.disabled = True

class Search(commands.Cog, name="search"):
    def __init__(self, bot):
        self.bot, self.ebd, self.atp = bot, DiscordEmbedBuilder(EMBED_COLOR), AttachmentProcessor()
        self._tcache, self._asearches, self._shistory = ThreadCache(ttl=300), {}, {}
        self._qp, self._ssem = SearchQueryParser(), asyncio.Semaphore(CONCURRENT_SEARCH_LIMIT)
        self._url_pat = re.compile(r'https?://\S+')
        self._cct = bot.loop.create_task(self._cleanup_cache_task())
        self._sct = bot.loop.create_task(self._cleanup_searches_task())
        self.config, self.cache, self.stats = bot.config.get('search', {}), bot.cache, None
        self.max_history = self.config.get('history_size', 10)
        logger.info("[init] Search module initialized")
    
    async def cog_load(self): self.bot.tree.on_error = self.on_app_command_error
    async def on_ready(self): 
        self.stats = self.bot.get_cog('Stats')
        if not self.stats: logger.warning("[boundary:error] Stats cog not found")
    
    async def on_app_command_error(self, interaction, error):
        if isinstance(error, app_commands.CommandOnCooldown): await interaction.response.send_message(f"⏳ Cooldown. Try in {error.retry_after:.1f}s", ephemeral=True)
        elif isinstance(error, app_commands.CheckFailure): await interaction.response.send_message("⚠️ No permission.", ephemeral=True)
        else:
            logger.error(f"[boundary:error] Cmd error: {error}", exc_info=error)
            if not interaction.response.is_done(): await interaction.response.send_message("⚠️ Error occurred.", ephemeral=True)
    
    async def cog_unload(self):
        if self._cct: self._cct.cancel()
        if self._sct: self._sct.cancel()
    
    async def _cleanup_cache_task(self):
        while not self.bot.is_closed():
            try: await self._tcache.cleanup()
            except Exception as e: logger.error(f"[boundary:error] Cache cleanup: {e}")
            await asyncio.sleep(60) 
    
    async def _cleanup_searches_task(self):
        while not self.bot.is_closed():
            try:
                n, exp = datetime.now(), [s for s, i in self._asearches.items() if (n - i["start_time"]).total_seconds() > 600]
                if exp: 
                    for s in exp: self._asearches.pop(s, None)
                    logger.debug(f"[signal] Removed {len(exp)} expired searches")
            except Exception as e: logger.error(f"[boundary:error] Search cleanup: {e}")
            await asyncio.sleep(300)
    
    @lru_cache(maxsize=256)
    def _check_tags(self, tt, st, et): 
        tl = {t.lower() for t in tt}
        return (not st or any(t in tl for t in st)) and (not et or not any(t in tl for t in et))
    
    def _preprocess_keywords(self, kws): return [k.strip().lower() for k in kws if k and k.strip()]
    
    def _check_keywords(self, c, sq, ek):
        if not c: return not sq
        cl = c.lower()
        if ek and any(k in cl for k in ek): return False
        if not sq: return True
        t = self._qp.parse_query(sq)
        return all(k in cl for k in t["keywords"]) if t["type"] == "simple" else self._qp.evaluate(t["tree"], c) if t["type"] == "advanced" else True
    
    async def _process_thread(self, th, cond, ce=None):
        if not th or not th.id or (ce and ce.is_set()): return None
        async with self._ssem:
            if ((cond.get('start_date') and th.created_at < cond['start_date']) or 
                (cond.get('end_date') and th.created_at > cond['end_date'])): return None
            o = getattr(th, 'owner', None)
            if ((cond.get('original_poster') and (not o or o.id != cond['original_poster'].id)) or
                (cond.get('exclude_op') and o and o.id == cond['exclude_op'].id)): return None
            
            tt = tuple(t.name for t in getattr(th, 'applied_tags', []))
            st, et = tuple(cond.get('search_tags', [])), tuple(cond.get('exclude_tags', []))
            if not self._check_tags(tt, st, et): return None
            
            ct = self._tcache.get(th.id)
            if ct and self._check_keywords(ct.get('content', ''), cond.get('search_query', ''), cond.get('exclude_keywords', [])): return ct
            if ct: return None
            
            try:
                td = {'thread': th, 'thread_id': th.id, 'title': th.name, 'author': o, 'created_at': th.created_at, 
                     'is_archived': th.archived, 'tags': tt, 'stats': await self._tcache.get_thread_stats(th), 'jump_url': th.jump_url}
                cn = ""
                async for m in th.history(limit=1, oldest_first=True): cn, td['first_message'], td['first_message_id'] = m.content, m, m.id; break
                td['content'] = cn
                if not self._check_keywords(cn, cond.get('search_query', ''), cond.get('exclude_keywords', [])): return None
                if ((cond.get('min_reactions') and td['stats']['reaction_count'] < cond['min_reactions']) or 
                    (cond.get('min_replies') and td['stats']['reply_count'] < cond['min_replies'])): return None
                self._tcache.store(th.id, td)
                return td
            except Exception as e: logger.error(f"[boundary:error] Thread process: {e}", exc_info=True)
            return None
    
    async def _process_thread_batch(self, threads, cond, ce=None):
        if not threads or (ce and ce.is_set()): return []
        tasks = [self._process_thread(t, cond, ce) for t in threads]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if r and not isinstance(r, Exception)]
    
    async def _search_threads(self, forum, cond, ce, bs=50):
        res = []
        at = await forum.active_threads()
        if at and not ce.is_set(): res.extend(await self._process_thread_batch(at, cond, ce))
        
        if not ce.is_set():
            try:
                arct = []
                async for t in forum.archived_threads():
                    if ce.is_set(): break
                    arct.append(t)
                    if len(arct) >= bs: res.extend(await self._process_thread_batch(arct, cond, ce)); arct = []
                if arct and not ce.is_set(): res.extend(await self._process_thread_batch(arct, cond, ce))
            except Exception as e: logger.error(f"[boundary:error] Archive search: {e}")
        
        return [] if ce.is_set() else self._sort_results(res, cond.get('order', 'newest'))
    
    def _sort_results(self, threads, order):
        if not threads: return []
        sk, rv = None, False
        if order == "newest": sk, rv = lambda t: t['created_at'], True
        elif order == "oldest": sk = lambda t: t['created_at']
        elif order == "most_replies": sk, rv = lambda t: t['stats'].get('reply_count', 0), True
        elif order == "least_replies": sk = lambda t: t['stats'].get('reply_count', 0)
        elif order == "most_reactions": sk, rv = lambda t: t['stats'].get('reaction_count', 0), True
        elif order == "least_reactions": sk = lambda t: t['stats'].get('reaction_count', 0)
        elif order == "alphabetical": sk = lambda t: t['title'].lower()
        elif order == "reverse_alphabetical": sk, rv = lambda t: t['title'].lower(), True
        threads.sort(key=sk, reverse=rv)
        return threads
    
    def _parse_date(self, ds):
        if not ds: return None
        ds, n = ds.strip().lower(), datetime.now()
        try: return datetime.strptime(ds, "%Y-%m-%d")
        except ValueError: pass
        if ds == "today": return n.replace(hour=0, minute=0, second=0, microsecond=0)
        if ds == "yesterday": return (n - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        if dm := re.match(r"^(\d+)d$", ds): return (n - timedelta(days=int(dm.group(1)))).replace(hour=0, minute=0, second=0, microsecond=0)
        if mm := re.match(r"^(\d+)m$", ds):
            m = int(mm.group(1)); y, mo = n.year, n.month - m
            while mo <= 0: mo, y = mo + 12, y - 1
            return datetime(y, mo, 1)
        return None
        
    def _store_search_history(self, uid, sw=None, fid=None):
        if uid not in self._shistory: self._shistory[uid] = []
        e = {'timestamp': datetime.now(), 'search_word': sw}
        if fid is not None: e['forum_id'] = fid
        self._shistory[uid].insert(0, e)
        self._shistory[uid] = self._shistory[uid][:self.max_history]
    
    async def _build_search_conditions(self, interaction, **kwargs):
        try:
            sd = ed = None
            if s := kwargs.get('start_date'):
                if not (sd := self._parse_date(s)): raise ValueError(f"Invalid start date: {s}")
            if e := kwargs.get('end_date'):
                if not (ed := self._parse_date(e)): raise ValueError(f"Invalid end date: {e}")
                if ed: ed += timedelta(days=1, microseconds=-1)
            
            stags, etags = set(), set()
            for i in range(1, 4): 
                if t := kwargs.get(f'tag{i}'): stags.add(t.lower())
            for i in range(1, 3): 
                if t := kwargs.get(f'exclude_tag{i}'): etags.add(t.lower())
                
            return {'search_tags': stags, 'exclude_tags': etags, 'search_query': kwargs.get('search_word'),
                   'exclude_keywords': self._preprocess_keywords(kwargs.get('exclude_word', "").split(",")),
                   'original_poster': kwargs.get('original_poster'), 'exclude_op': kwargs.get('exclude_op'),
                   'start_date': sd, 'end_date': ed, 'min_reactions': kwargs.get('min_reactions'), 
                   'min_replies': kwargs.get('min_replies'), 'order': kwargs.get('order')}
        except ValueError as e:
            await interaction.followup.send(embed=self.ebd.create_error_embed("Date Error", str(e)), ephemeral=True)
            return None
    
    async def _generate_result_embed(self, item, tr, pn):
        t, s = item['thread'], item['stats']
        e = discord.Embed(title=truncate_text(t.name, 256), url=t.jump_url, color=EMBED_COLOR)
        if o := getattr(t, 'owner', None): e.set_author(name=o.display_name, icon_url=o.display_avatar.url)
        if m := item.get('first_message'):
            e.description = f"**Summary:**\n{truncate_text(m.content.strip(), 1000)}"
            if th := self.atp.get_first_image(m): e.set_thumbnail(url=th)
        if tags := getattr(t, 'applied_tags', None): e.add_field(name="Tags", value=", ".join(tg.name for tg in tags), inline=True)
        e.add_field(name="Stats", value=f"👍 {s.get('reaction_count', 0)} | 💬 {s.get('reply_count', 0)}", inline=True)
        la = getattr(t.last_message, 'created_at', t.created_at)
        e.add_field(name="Time", value=f"Created: {discord.utils.format_dt(t.created_at, 'R')}\nActive: {discord.utils.format_dt(la, 'R')}", inline=True)
        st, en = pn * MESSAGES_PER_PAGE + 1, min((pn + 1) * MESSAGES_PER_PAGE, tr)
        e.set_footer(text=f"Result {st}-{en} of {tr}")
        return e
    
    async def _present_results(self, intr, forum, results, cond, pm, ov):
        if not results: await pm.edit(embed=self.ebd.create_info_embed("No Results", f"No matches in {forum.mention}."), view=None); return

        s = discord.Embed(title=f"Search Results: {forum.name}", description=f"Found {len(results)} matching threads", color=EMBED_COLOR)
        c = []
        if cond.get('search_tags'): c.append(f"🏷️ Tags: {', '.join(cond['search_tags'])}")
        if cond.get('exclude_tags'): c.append(f"🚫 ExTags: {', '.join(cond['exclude_tags'])}")
        if cond.get('search_query'): c.append(f"🔍 Keywords: {cond['search_query']}")
        if cond.get('exclude_keywords'): c.append(f"❌ ExWords: {', '.join(cond['exclude_keywords'])}")
        if op := cond.get('original_poster'): c.append(f"👤 By: {op.display_name}")
        if ex := cond.get('exclude_op'): c.append(f"🚷 Not By: {ex.display_name}")
        if sd := cond.get('start_date'): c.append(f"📅 From: {sd.strftime('%Y-%m-%d')}")
        if ed := cond.get('end_date'): c.append(f"📅 To: {(ed - timedelta(microseconds=1)).strftime('%Y-%m-%d')}")
        if mr := cond.get('min_reactions'): c.append(f"👍 Min Reactions: {mr}")
        if mp := cond.get('min_replies'): c.append(f"💬 Min Replies: {mp}")
        if c: s.add_field(name="Search Criteria", value="\n".join(c), inline=False)

        embs = await asyncio.gather(*[self._generate_result_embed(r, len(results), 0) for r in results[:MESSAGES_PER_PAGE]])
        pag = MultiEmbedPaginationView(items=results, items_per_page=MESSAGES_PER_PAGE,
            generate_embeds=lambda items, page: asyncio.gather(*[self._generate_result_embed(i, len(results), page) for i in items]))
        await pm.edit(embed=s, view=None)
        await pag.start(intr, embs)

    @app_commands.command(name="forum_search", description="Search forum posts")
    @app_commands.describe(
        forum="Forum channel to search in", order="Order of results (default: newest)",
        original_poster="Filter by original poster", exclude_op="Exclude posts by this user",
        tag1="Include posts with this tag", tag2="Include posts with this tag", tag3="Include posts with this tag",
        exclude_tag1="Exclude posts with this tag", exclude_tag2="Exclude posts with this tag",
        search_word="Search for words in titles and content", exclude_word="Exclude posts with these words (comma-separated)",
        start_date="Include posts after this date (YYYY-MM-DD or Nd/Nm)", end_date="Include posts before this date (YYYY-MM-DD or Nd/Nm)",
        min_reactions="Minimum number of reactions", min_replies="Minimum number of replies")
    async def forum_search(self, intr, forum: discord.ForumChannel, order: Optional[SearchOrder] = SearchOrder.newest, 
                          original_poster: Optional[discord.Member] = None, exclude_op: Optional[discord.Member] = None, 
                          tag1: Optional[str] = None, tag2: Optional[str] = None, tag3: Optional[str] = None, 
                          exclude_tag1: Optional[str] = None, exclude_tag2: Optional[str] = None,
                          search_word: Optional[str] = None, exclude_word: Optional[str] = None, 
                          start_date: Optional[str] = None, end_date: Optional[str] = None, 
                          min_reactions: Optional[int] = None, min_replies: Optional[int] = None):
        if not intr.guild: await intr.response.send_message("Server only command", ephemeral=True); return
        p = forum.permissions_for(intr.guild.me)
        if not (p.read_messages and p.send_messages and p.embed_links):
            await intr.response.send_message(f"Need read/send/embed perms in {forum.mention}", ephemeral=True); return
        if not any([original_poster, tag1, tag2, tag3, search_word, start_date, end_date]):
            await intr.response.send_message("Provide at least one criteria", ephemeral=True); return

        await intr.response.defer(thinking=True)
        self._store_search_history(intr.user.id, search_word, forum.id)
        conds = await self._build_search_conditions(intr, original_poster=original_poster, exclude_op=exclude_op,
            tag1=tag1, tag2=tag2, tag3=tag3, exclude_tag1=exclude_tag1, exclude_tag2=exclude_tag2, search_word=search_word, 
            exclude_word=exclude_word, start_date=start_date, end_date=end_date, min_reactions=min_reactions, 
            min_replies=min_replies, order=order.value)
        if not conds: return
        
        ce = asyncio.Event()
        st = asyncio.create_task(self._search_threads(forum, conds, ce))
        cv = CancelView(ce)
        pm = await intr.followup.send(embed=self.ebd.create_info_embed("Searching...", f"Looking in {forum.mention}..."), view=cv)
        
        st.add_done_callback(lambda _: asyncio.create_task(cv.disable_buttons()))
        try:
            r = await st
            if ce.is_set(): await pm.edit(embed=self.ebd.create_info_embed("Cancelled", "Search cancelled"), view=None); return
            await self._present_results(intr, forum, r, conds, pm, order.value)
        except Exception as e:
            logger.exception(f"Search error: {e}")
            await pm.edit(embed=self.ebd.create_error_embed("Error", f"Error: {str(e)}"), view=None)
    
    @forum_search.autocomplete('forum')
    async def forum_autocomplete(self, intr, current):
        if not intr.guild: return []
        forums = [ch for ch in intr.guild.channels if isinstance(ch, discord.ForumChannel) and 
                 (not current or current.lower() in ch.name.lower())]
        forums.sort(key=lambda ch: ch.name.lower())
        return [app_commands.Choice(name=f"#{ch.name}", value=ch.id) for ch in forums[:25]]

    @forum_search.autocomplete('tag1')
    @forum_search.autocomplete('tag2')
    @forum_search.autocomplete('tag3')
    @forum_search.autocomplete('exclude_tag1')
    @forum_search.autocomplete('exclude_tag2')
    async def tag_autocomplete(self, intr, current):
        if not intr.guild: return []
        fid = None
        for opt in intr.data.get("options", []):
            if opt["name"] == "forum" and "value" in opt: fid = opt["value"]; break
        if not fid: return []
        forum = intr.guild.get_channel(int(fid))
        if not isinstance(forum, discord.ForumChannel): return []
        stags = set()
        for opt in intr.data.get("options", []):
            if opt["name"].startswith(("tag", "exclude_tag")) and "value" in opt: stags.add(opt["value"].lower())
        atags = [t for t in forum.available_tags if t.name.lower() not in stags and
                (not current or current.lower() in t.name.lower())]
        atags.sort(key=lambda t: t.name.lower())
        return [app_commands.Choice(name=t.name, value=t.name) for t in atags[:25]]
        
    @forum_search.autocomplete('start_date')
    @forum_search.autocomplete('end_date')
    async def date_autocomplete(self, intr, current):
        today = datetime.now()
        sugs = [
            ("Today", today.strftime("%Y-%m-%d")),
            ("Yesterday", (today - timedelta(days=1)).strftime("%Y-%m-%d")),
            ("Last Week", (today - timedelta(days=7)).strftime("%Y-%m-%d")),
            ("Last Month", (today - timedelta(days=30)).strftime("%Y-%m-%d")),
            ("Last 3 Months", (today - timedelta(days=90)).strftime("%Y-%m-%d")),
            ("Last 6 Months", (today - timedelta(days=180)).strftime("%Y-%m-%d")),
            ("Last Year", (today - timedelta(days=365)).strftime("%Y-%m-%d")),
        ]
        flt = [(n, v) for n, v in sugs if not current or current.lower() in n.lower() or current.lower() in v.lower()]
        return [app_commands.Choice(name=f"{n} ({v})", value=v) for n, v in flt[:25]]
    
    @app_commands.command(name="search_syntax", description="Show search syntax help")
    async def search_syntax(self, intr):
        e = discord.Embed(title="Search Syntax", description="Forum search supports these syntax:", color=EMBED_COLOR)
        e.add_field(name="Basic Keywords", value="AND logic\n`issue solution`", inline=False)
        e.add_field(name="OR Operator", value="Any keyword\n`solution OR workaround`", inline=False)
        e.add_field(name="NOT Operator", value="Exclude words\n`issue -resolved`", inline=False)
        e.add_field(name="Exact Phrases", value="Quote matching\n`\"complete phrase match\"`", inline=False)
        await intr.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="search_history", description="View your recent searches")
    async def search_history(self, intr):
        h = self._shistory.get(intr.user.id, [])
        if not h: await intr.response.send_message("No history", ephemeral=True); return
        e = discord.Embed(title="Recent Searches", description=f"Last {len(h)} searches", color=EMBED_COLOR)
        for i, s in enumerate(h[:10], 1):
            ts, st = s.get('timestamp', datetime.now()), s.get('search_word', 'No terms')
            ft = "Unknown"
            if fid := s.get('forum_id'):
                if f := intr.guild.get_channel(fid): ft = f"#{f.name}"
                e.add_field(name=f"{i}. {discord.utils.format_dt(ts, 'R')}", value=f"Forum: {ft}\nQuery: {st or 'N/A'}", inline=False)
        await intr.response.send_message(embed=e, ephemeral=True)

async def setup(bot): await bot.add_cog(Search(bot))