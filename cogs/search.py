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
    def __init__(self, ttl=300): self._cache, self._stats_cache, self._ttl, self._last_cleanup = {}, {}, ttl, datetime.now().timestamp()
    async def get_thread_stats(self, thread):
        k, t = f"stats_{thread.id}", datetime.now().timestamp()
        if k in self._stats_cache and t - self._stats_cache[k]['timestamp'] < self._ttl: return self._stats_cache[k]['data']
        try: stats = await get_thread_stats(thread); self._stats_cache[k] = {'data': stats, 'timestamp': t}; return stats
        except Exception as e: logger.error(f"[boundary:error] Stats fetch: {e}"); return {'reaction_count': 0, 'reply_count': 0}
    def store(self, tid, data): self._cache[tid] = {'data': data, 'timestamp': datetime.now().timestamp()}
    def get(self, tid): return self._cache[tid]['data'] if tid in self._cache and datetime.now().timestamp() - self._cache[tid]['timestamp'] < self._ttl else None
    async def cleanup(self):
        t = datetime.now().timestamp();
        if t - self._last_cleanup < 60: return 0
        self._last_cleanup = t
        expt = [k for k, v in self._cache.items() if t - v['timestamp'] > self._ttl]; exps = [k for k, v in self._stats_cache.items() if t - v['timestamp'] > self._ttl]
        for k in expt: del self._cache[k]
        for k in exps: del self._stats_cache[k]
        c = len(expt) + len(exps); logger.debug(f"[signal] Cleaned {c} cache entries") if c > 0 else None; return c

class SearchOrder(str, enum.Enum):
    newest, oldest, most_replies, least_replies, most_reactions, least_reactions, alphabetical, reverse_alphabetical = "newest", "oldest", "most_replies", "least_replies", "most_reactions", "least_reactions", "alphabetical", "reverse_alphabetical"
    @classmethod
    def _missing_(cls, value): return cls.newest

class CancelView(discord.ui.View):
    def __init__(self, cancel_event): super().__init__(timeout=300); self.ce = cancel_event
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, intr, btn): self.ce.set(); [setattr(i, 'disabled', True) for i in self.children]; await intr.response.edit_message(view=self)
    async def disable_buttons(self): [setattr(i, 'disabled', True) for i in self.children]

class Search(commands.Cog, name="search"):
    def __init__(self, bot):
        self.bot, self.ebd, self.atp = bot, DiscordEmbedBuilder(EMBED_COLOR), AttachmentProcessor()
        self._tc, self._asc, self._sh = ThreadCache(ttl=300), {}, {}
        self._qp, self._ssem = SearchQueryParser(), asyncio.Semaphore(CONCURRENT_SEARCH_LIMIT)
        self._url_pat = re.compile(r'https?://\S+')
        self._cct = bot.loop.create_task(self._cln_cache_task())
        self._sct = bot.loop.create_task(self._cln_search_task())
        self.cfg, self.cache, self.stats = bot.config.get('search', {}), bot.cache, None
        self.max_hist = self.cfg.get('history_size', 10); logger.info("[init] Search cog")
    
    async def cog_load(self): self.bot.tree.on_error = self.on_app_cmd_err
    async def on_ready(self): self.stats = self.bot.get_cog('Stats'); logger.warning("[boundary:error] Stats cog missing") if not self.stats else None
    async def on_app_cmd_err(self, intr, err):
        if isinstance(err, app_commands.CommandOnCooldown): await intr.response.send_message(f"⏳ CD {err.retry_after:.1f}s", ephemeral=True)
        elif isinstance(err, app_commands.CheckFailure): await intr.response.send_message("⚠️ No perm.", ephemeral=True)
        else: logger.error(f"[boundary:error] Cmd err: {err}", exc_info=err); await intr.response.send_message("⚠️ Error.", ephemeral=True) if not intr.response.is_done() else None
    async def cog_unload(self): self._cct.cancel() if self._cct else None; self._sct.cancel() if self._sct else None
    
    async def _cln_cache_task(self):
        while not self.bot.is_closed():
            try: await self._tc.cleanup()
            except Exception as e: logger.error(f"[boundary:error] Cache cleanup: {e}")
            await asyncio.sleep(60)
    async def _cln_search_task(self):
        while not self.bot.is_closed():
            try:
                n, exp = datetime.now(), [s for s, i in self._asc.items() if (n - i["start_time"]).total_seconds() > 600]
                if exp: [self._asc.pop(s, None) for s in exp]; logger.debug(f"[signal] Removed {len(exp)} expired searches")
            except Exception as e: logger.error(f"[boundary:error] Search cleanup: {e}")
            await asyncio.sleep(300)

    @lru_cache(maxsize=256)
    def _chk_tags(self, tt, st, et): tl = {t.lower() for t in tt}; return (not st or any(t in tl for t in st)) and (not et or not any(t in tl for t in et))
    def _prep_kws(self, kws): return [k.strip().lower() for k in kws if k and k.strip()]
    def _chk_kws(self, c, sq, ek):
        if not c: return not sq
        cl = c.lower();
        if ek and any(k in cl for k in ek): return False
        if not sq: return True
        t = self._qp.parse_query(sq)
        return all(k in cl for k in t["keywords"]) if t["type"] == "simple" else self._qp.evaluate(t["tree"], c) if t["type"] == "advanced" else True

    async def _proc_th(self, th, cond, ce=None):
        if not th or not th.id or (ce and ce.is_set()): return None
        async with self._ssem:
            if (cond.get('sd') and th.created_at < cond['sd']) or (cond.get('ed') and th.created_at > cond['ed']): return None
            o = getattr(th, 'owner', None)
            if (cond.get('op') and (not o or o.id != cond['op'].id)) or (cond.get('ex_op') and o and o.id == cond['ex_op'].id): return None
            tt = tuple(t.name for t in getattr(th, 'applied_tags', []))
            st, et = tuple(cond.get('stags', [])), tuple(cond.get('etags', []))
            if not self._chk_tags(tt, st, et): return None
            ct = self._tc.get(th.id)
            if ct and self._chk_kws(ct.get('c', ''), cond.get('sq', ''), cond.get('ek', [])): return ct
            if ct: return None
            try:
                td = {'t': th, 'tid': th.id, 'ttl': th.name, 'a': o, 'ca': th.created_at, 'ia': th.archived, 'tags': tt,
                      's': await self._tc.get_thread_stats(th), 'url': th.jump_url}
                cn = ""; msg_id = None
                async for m in th.history(limit=1, oldest_first=True): cn, td['fm'], msg_id = m.content, m, m.id; break
                td['c'] = cn; td['fmid'] = msg_id
                if not self._chk_kws(cn, cond.get('sq', ''), cond.get('ek', [])): return None
                if (cond.get('mr') and td['s'].get('reaction_count', 0) < cond['mr']) or (cond.get('mp') and td['s'].get('reply_count', 0) < cond['mp']): return None
                self._tc.store(th.id, td); return td
            except Exception as e: logger.error(f"[boundary:error] Thread process: {e}", exc_info=True); return None

    async def _proc_th_batch(self, ths, cond, ce=None):
        if not ths or (ce and ce.is_set()): return []
        tasks = [self._proc_th(t, cond, ce) for t in ths]
        res = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in res if r and not isinstance(r, Exception)]

    async def _search_ths(self, frm, cond, ce, bs=50):
        res = []
        at = await frm.active_threads(); res.extend(await self._proc_th_batch(at, cond, ce)) if at and not ce.is_set() else None
        if not ce.is_set():
            try:
                arct = [];
                async for t in frm.archived_threads():
                    if ce.is_set(): break
                    arct.append(t)
                    if len(arct) >= bs: res.extend(await self._proc_th_batch(arct, cond, ce)); arct = []
                if arct and not ce.is_set(): res.extend(await self._proc_th_batch(arct, cond, ce))
            except Exception as e: logger.error(f"[boundary:error] Archive search: {e}")
        return [] if ce.is_set() else self._sort_res(res, cond.get('order', 'newest'))

    def _sort_res(self, ths, order):
        if not ths: return []
        sk, rv = None, False
        if order == "newest": sk, rv = lambda t: t['ca'], True
        elif order == "oldest": sk = lambda t: t['ca']
        elif order == "most_replies": sk, rv = lambda t: t['s'].get('reply_count', 0), True
        elif order == "least_replies": sk = lambda t: t['s'].get('reply_count', 0)
        elif order == "most_reactions": sk, rv = lambda t: t['s'].get('reaction_count', 0), True
        elif order == "least_reactions": sk = lambda t: t['s'].get('reaction_count', 0)
        elif order == "alphabetical": sk = lambda t: t['ttl'].lower()
        elif order == "reverse_alphabetical": sk, rv = lambda t: t['ttl'].lower(), True
        ths.sort(key=sk, reverse=rv) if sk else None; return ths

    def _parse_dt(self, ds):
        if not ds: return None
        ds, n = ds.strip().lower(), datetime.now()
        try: return datetime.strptime(ds, "%Y-%m-%d")
        except ValueError: pass
        if ds == "today": return n.replace(hour=0, min=0, sec=0, micro=0)
        if ds == "yesterday": return (n - timedelta(days=1)).replace(hour=0, min=0, sec=0, micro=0)
        if dm := re.match(r"^(\d+)d$", ds): return (n - timedelta(days=int(dm.group(1)))).replace(hour=0, min=0, sec=0, micro=0)
        if mm := re.match(r"^(\d+)m$", ds): 
            m = int(mm.group(1)); y, mo = n.year, n.month - m
            while mo <= 0: mo, y = mo + 12, y - 1
            return datetime(y, mo, 1)
        return None

    def _store_sh(self, uid, sw=None, fid=None):
        if uid not in self._sh: self._sh[uid] = []
        e = {'ts': datetime.now(), 'sw': sw}; e['fid'] = fid if fid is not None else None
        self._sh[uid].insert(0, e); self._sh[uid] = self._sh[uid][:self.max_hist]

    async def _build_conds(self, intr, **kw):
        try:
            sd = ed = None
            if s := kw.get('start_date'):
                if not (sd := self._parse_dt(s)): raise ValueError(f"Bad start date: {s}")
            if e := kw.get('end_date'):
                if not (ed := self._parse_dt(e)): raise ValueError(f"Bad end date: {e}")
                if ed: ed += timedelta(days=1, microseconds=-1)
            stags, etags = set(), set()
            for i in range(1, 4):
                if t := kw.get(f'tag{i}'): 
                    stags.add(t.lower())
            for i in range(1, 3):
                if t := kw.get(f'exclude_tag{i}'): 
                    etags.add(t.lower())
            return {'stags': stags, 'etags': etags, 'sq': kw.get('search_word'), 'ek': self._prep_kws(kw.get('exclude_word', "").split(",")),
                    'op': kw.get('original_poster'), 'ex_op': kw.get('exclude_op'), 'sd': sd, 'ed': ed,
                    'mr': kw.get('min_reactions'), 'mp': kw.get('min_replies'), 'order': kw.get('order')}
        except ValueError as e: await intr.followup.send(embed=self.ebd.create_error_embed("Date Error", str(e)), ephemeral=True); return None

    async def _gen_res_ebd(self, item, tr, pn):
        t, s = item['t'], item['s']
        e = discord.Embed(title=truncate_text(t.name, 256), url=item['url'], color=EMBED_COLOR)
        if o := item['a']: e.set_author(name=o.display_name, icon_url=o.display_avatar.url)
        if m := item.get('fm'): e.description = f"**Sum:**\n{truncate_text(m.content.strip(), 1000)}"; (e.set_thumbnail(url=th) if (th := self.atp.get_first_image(m)) else None)
        if tags := item['tags']: e.add_field(name="Tags", value=", ".join(tags), inline=True)
        e.add_field(name="Stats", value=f"👍 {s.get('reaction_count', 0)} | 💬 {s.get('reply_count', 0)}", inline=True)
        la = getattr(getattr(t, 'last_message', None), 'created_at', t.created_at)
        e.add_field(name="Time", value=f"Cr: {discord.utils.format_dt(t.created_at, 'R')}\nAct: {discord.utils.format_dt(la, 'R')}", inline=True)
        st, en = pn * MESSAGES_PER_PAGE + 1, min((pn + 1) * MESSAGES_PER_PAGE, tr)
        e.set_footer(text=f"Res {st}-{en} of {tr}"); return e

    async def _pres_res(self, intr, frm, res, cond, pm, ov):
        if not res: await pm.edit(embed=self.ebd.create_info_embed("No Results", f"No matches in {frm.mention}."), view=None); return
        s = discord.Embed(title=f"Results: {frm.name}", description=f"{len(res)} found", color=EMBED_COLOR)
        c = [];
        if cond.get('stags'): c.append(f"🏷️: {', '.join(cond['stags'])}")
        if cond.get('etags'): c.append(f"🚫🏷️: {', '.join(cond['etags'])}")
        if cond.get('sq'): c.append(f"🔍: {cond['sq']}")
        if cond.get('ek'): c.append(f"❌: {', '.join(cond['ek'])}")
        if op := cond.get('op'): c.append(f"👤: {op.display_name}")
        if ex := cond.get('ex_op'): c.append(f"🚷: {ex.display_name}")
        if sd := cond.get('sd'): c.append(f"📅>: {sd.strftime('%y-%m-%d')}")
        if ed := cond.get('ed'): c.append(f"📅<: {(ed - timedelta(microseconds=1)).strftime('%y-%m-%d')}")
        if mr := cond.get('mr'): c.append(f"👍≥: {mr}")
        if mp := cond.get('mp'): c.append(f"💬≥: {mp}")
        if c: s.add_field(name="Criteria", value=" | ".join(c), inline=False)
        embs = await asyncio.gather(*[self._gen_res_ebd(r, len(res), 0) for r in res[:MESSAGES_PER_PAGE]])
        pag = MultiEmbedPaginationView(items=res, items_per_page=MESSAGES_PER_PAGE,
            generate_embeds=lambda items, page: asyncio.gather(*[self._gen_res_ebd(i, len(res), page) for i in items]))
        await pm.edit(embed=s, view=None); await pag.start(intr, embs)

    @app_commands.command(name="forum_search", description="Search forum posts")
    @app_commands.describe(forum="Forum", order="Order", op="OP", ex_op="Exclude OP", tag1="Tag1", tag2="Tag2", tag3="Tag3",
                          ex_tag1="ExTag1", ex_tag2="ExTag2", sw="Keywords", ex_w="Exclude KW", sd="Start Date", ed="End Date", mr="Min Reacts", mp="Min Replies")
    async def forum_search(self, intr, forum: discord.ForumChannel, order: Optional[SearchOrder] = SearchOrder.newest, op: Optional[discord.Member] = None, ex_op: Optional[discord.Member] = None,
                           tag1: Optional[str] = None, tag2: Optional[str] = None, tag3: Optional[str] = None, ex_tag1: Optional[str] = None, ex_tag2: Optional[str] = None,
                           sw: Optional[str] = None, ex_w: Optional[str] = None, sd: Optional[str] = None, ed: Optional[str] = None, mr: Optional[int] = None, mp: Optional[int] = None):
        if not intr.guild: await intr.response.send_message("Server only", ephemeral=True); return
        p = forum.permissions_for(intr.guild.me); 
        if not (p.read_messages and p.send_messages and p.embed_links): await intr.response.send_message(f"Need RSE perms in {forum.mention}", ephemeral=True); return
        if not any([op, tag1, tag2, tag3, sw, sd, ed]): await intr.response.send_message("Need criteria", ephemeral=True); return
        await intr.response.defer(thinking=True); self._store_sh(intr.user.id, sw, forum.id)
        conds = await self._build_conds(intr, original_poster=op, exclude_op=ex_op, tag1=tag1, tag2=tag2, tag3=tag3, exclude_tag1=ex_tag1, exclude_tag2=ex_tag2,
                                     search_word=sw, exclude_word=ex_w, start_date=sd, end_date=ed, min_reactions=mr, min_replies=mp, order=order.value)
        if not conds: return
        ce = asyncio.Event(); st = asyncio.create_task(self._search_ths(forum, conds, ce)); cv = CancelView(ce)
        pm = await intr.followup.send(embed=self.ebd.create_info_embed("Searching...", f"In {forum.mention}..."), view=cv)
        st.add_done_callback(lambda _: asyncio.create_task(cv.disable_buttons()))
        try: r = await st; await (pm.edit(embed=self.ebd.create_info_embed("Cancelled", "Search cancelled"), view=None) if ce.is_set() else self._pres_res(intr, forum, r, conds, pm, order.value))
        except Exception as e: logger.exception(f"Search err: {e}"); await pm.edit(embed=self.ebd.create_error_embed("Error", f"Err: {str(e)}"), view=None)

    @forum_search.autocomplete('forum')
    async def forum_ac(self, intr, cur):
        if not intr.guild: return []
        frms = [ch for ch in intr.guild.channels if isinstance(ch, discord.ForumChannel) and (not cur or cur.lower() in ch.name.lower())]
        frms.sort(key=lambda ch: ch.name.lower()); return [app_commands.Choice(name=f"#{ch.name}", value=ch.id) for ch in frms[:25]]
    @forum_search.autocomplete('tag1')
    @forum_search.autocomplete('tag2')
    @forum_search.autocomplete('tag3')
    @forum_search.autocomplete('ex_tag1')
    @forum_search.autocomplete('ex_tag2')
    async def tag_ac(self, intr, cur):
        if not intr.guild: return []
        fid = None; [fid := opt["value"] for opt in intr.data.get("options", []) if opt["name"] == "forum" and "value" in opt]; 
        if not fid: return []
        frm = intr.guild.get_channel(int(fid)); 
        if not isinstance(frm, discord.ForumChannel): return []
        stags = set(); [stags.add(opt["value"].lower()) for opt in intr.data.get("options", []) if opt["name"].startswith(("tag", "ex_tag")) and "value" in opt]
        atags = [t for t in frm.available_tags if t.name.lower() not in stags and (not cur or cur.lower() in t.name.lower())]
        atags.sort(key=lambda t: t.name.lower()); return [app_commands.Choice(name=t.name, value=t.name) for t in atags[:25]]
    @forum_search.autocomplete('sd')
    @forum_search.autocomplete('ed')
    async def date_ac(self, intr, cur):
        tdy = datetime.now(); sugs = [("Today", tdy.strftime("%y-%m-%d")), ("Yesterday", (tdy - timedelta(days=1)).strftime("%y-%m-%d")), ("Last Wk", (tdy - timedelta(days=7)).strftime("%y-%m-%d")), ("Last Mo", (tdy - timedelta(days=30)).strftime("%y-%m-%d")), ("Last 3Mo", (tdy - timedelta(days=90)).strftime("%y-%m-%d")), ("Last 6Mo", (tdy - timedelta(days=180)).strftime("%y-%m-%d")), ("Last Yr", (tdy - timedelta(days=365)).strftime("%y-%m-%d"))]
        flt = [(n, v) for n, v in sugs if not cur or cur.lower() in n.lower() or cur.lower() in v.lower()]; return [app_commands.Choice(name=f"{n} ({v})", value=v) for n, v in flt[:25]]

    @app_commands.command(name="search_syntax", description="Show search syntax help")
    async def search_syntax(self, intr): e = discord.Embed(title="Search Syntax", description="Syntax:", color=EMBED_COLOR); e.add_field(name="Keywords", value="AND: `a b`", inline=False); e.add_field(name="OR", value="`a OR b`", inline=False); e.add_field(name="NOT", value="`-a`", inline=False); e.add_field(name="Phrase", value="`\"a b\"`", inline=False); await intr.response.send_message(embed=e, ephemeral=True)
    @app_commands.command(name="search_history", description="View your recent searches")
    async def search_history(self, intr):
        h = self._sh.get(intr.user.id, []); 
        if not h: await intr.response.send_message("No history", ephemeral=True); return
        e = discord.Embed(title="Recent Searches", description=f"{len(h)} found", color=EMBED_COLOR)
        for i, s in enumerate(h[:10], 1): ts, st = s.get('ts', datetime.now()), s.get('sw', 'N/A'); ft = "? Forum"; (ft := f"#{f.name}") if (f := intr.guild.get_channel(s.get('fid'))) else None; e.add_field(name=f"{i}. {discord.utils.format_dt(ts, 'R')}", value=f"F: {ft} | Q: {st}", inline=False)
        await intr.response.send_message(embed=e, ephemeral=True)

async def setup(bot): await bot.add_cog(Search(bot))