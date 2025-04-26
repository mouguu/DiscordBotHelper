import discord
from discord.ext import commands
from discord import app_commands
import psutil, asyncio, os, platform, logging
from datetime import datetime
from typing import Dict, Optional
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger('discord_bot.stats')

class Stats(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._cmd_usage = {}  # cmd_name -> count
        self._guild_data = {} # guild_id -> {cmds, searches, resp_time}
        
        self._metrics = {
            'search': {'total': 0, 'success': 0, 'fail': 0, 'last_hour': 0, 
                      'total_time': 0, 'avg_time': 0, 'peak': 0},
            'perf': {'total': 0, 'times': [], 'avg': 0},
            'cache': {'size': 0, 'mem_hit': 0, 'redis_hit': 0}
        }
        
        self.bg_task = bot.loop.create_task(self._bg_stats_update())
        logger.info("[init] Stats module initialized")
    
    @app_commands.command(name="bot_stats", description="Show bot performance statistics")
    async def bot_stats(self, interaction: discord.Interaction):
        await interaction.response.defer()
        
        # Core metrics
        uptime = datetime.now() - self.bot.start_time
        uptime_str = f"{uptime.days}d {uptime.seconds//3600}h {(uptime.seconds//60)%60}m"
        process = psutil.Process(os.getpid())
        mem_mb = process.memory_info().rss / (1024 * 1024)
        cpu_pct = process.cpu_percent(interval=0.1)
        
        # Build embed
        embed = discord.Embed(title="Bot Stats", color=discord.Color.blue(), timestamp=datetime.now())
        embed.add_field(name="General", 
                       value=f"⏱️ {uptime_str} | 🏠 {len(self.bot.guilds):,} servers | 👤 {sum(g.member_count for g in self.bot.guilds):,} users", 
                       inline=False)
        
        embed.add_field(name="System", 
                       value=f"🖥️ {cpu_pct:.1f}% CPU | 🧠 {mem_mb:.1f}MB RAM | 📋 {len(process.open_files())} files", 
                       inline=False)
        
        embed.add_field(name="Performance", 
                       value=f"⚡ {self._metrics['perf']['avg']:.2f}ms response | 🔄 {self.bot.latency*1000:.2f}ms latency", 
                       inline=False)
        
        # Search stats if available
        if self._metrics['search']['total'] > 0:
            s = self._metrics['search']
            embed.add_field(name="Search", 
                         value=f"📊 {s['total']:,} total | ✅ {s['success']:,} success | ❌ {s['fail']:,} fail\n"
                               f"⏰ {s['last_hour']:,} last hour | ⚡ {s['avg_time']:.2f}s avg | 🔄 {s['peak']} peak",
                         inline=False)
        
        # Cache stats if available
        if self._metrics['cache']['size'] > 0:
            c = self._metrics['cache']
            embed.add_field(name="Cache", 
                         value=f"💾 {c['size']:,} bytes | 🎯 {c['mem_hit']:.1f}% mem hit | 📦 {c['redis_hit']:.1f}% redis hit",
                         inline=False)
        
        # Top commands if available
        if self._cmd_usage:
            top_cmds = sorted(self._cmd_usage.items(), key=lambda x: x[1], reverse=True)[:5]
            embed.add_field(name="Top Commands",
                         value="\n".join(f"`/{cmd}`: {count:,}" for cmd, count in top_cmds),
                         inline=False)
        
        # System footer
        embed.set_footer(text=f"Python: {platform.python_version()} | discord.py: {discord.__version__}")
        
        # Build detailed version with view
        detailed = self._build_detailed_embed(process)
        view = StatsView(interaction.user.id, embed, detailed)
        
        await interaction.followup.send(embed=embed, view=view)
    
    def _build_detailed_embed(self, process):
        mem = psutil.virtual_memory()
        net = psutil.net_io_counters()
        
        embed = discord.Embed(title="Detailed Stats", color=discord.Color.blue(), timestamp=datetime.now())
        embed.add_field(name="Memory", 
                      value=f"Process: {process.memory_info().rss/(1024*1024):.2f}MB | Sys: {mem.percent:.1f}%", 
                      inline=False)
        
        embed.add_field(name="Network", 
                      value=f"Sent: {net.bytes_sent/(1024**2):.2f}MB | Recv: {net.bytes_recv/(1024**2):.2f}MB", 
                      inline=False)
        
        embed.add_field(name="Discord", 
                      value=f"Latency: {self.bot.latency*1000:.2f}ms | Commands: {len(self.bot.tree.get_commands()):,}", 
                      inline=False)
        
        return embed
    
    @app_commands.command(name="server_stats", description="Show server statistics")
    @app_commands.guild_only()
    async def server_stats(self, interaction: discord.Interaction):
        await interaction.response.defer()
        guild = interaction.guild
        
        # Basic counts
        humans = sum(1 for m in guild.members if not m.bot)
        bots = guild.member_count - humans if guild.member_count else 0
        
        # Build embed
        embed = discord.Embed(title=f"{guild.name} Stats", color=discord.Color.green())
        if guild.icon: embed.set_thumbnail(url=guild.icon.url)
        
        embed.add_field(name="Members", 
                       value=f"👥 {guild.member_count:,} total | 👤 {humans:,} humans | 🤖 {bots:,} bots", 
                       inline=False)
        
        embed.add_field(name="Channels", 
                       value=f"💬 {len(guild.text_channels)} text | 🔊 {len(guild.voice_channels)} voice | "
                             f"📂 {len(guild.categories)} categories | 📋 {len(guild.threads)} threads", 
                       inline=False)
        
        if guild_data := self._guild_data.get(str(guild.id)):
            embed.add_field(name="Bot Usage", 
                         value=f"⌨️ {guild_data['cmds']:,} commands | 🔍 {guild_data['searches']:,} searches", 
                         inline=False)
        
        # Check missing perms
        if bot_member := guild.get_member(self.bot.user.id):
            needed = {"manage_webhooks", "read_message_history", "add_reactions", "embed_links"}
            missing = [p.replace("_", " ").title() for p in needed 
                      if not getattr(bot_member.guild_permissions, p, False)]
            if missing: 
                embed.add_field(name="⚠️ Missing Permissions", value=", ".join(missing), inline=False)
        
        embed.set_footer(text=f"Server ID: {guild.id} • Created: {guild.created_at.strftime('%Y-%m-%d')}")
        await interaction.followup.send(embed=embed)
    
    def record_command(self, cmd: str, guild_id: Optional[str] = None):
        self._cmd_usage[cmd] = self._cmd_usage.get(cmd, 0) + 1
        if guild_id:
            self._guild_data.setdefault(guild_id, {'cmds': 0, 'searches': 0, 'resp_time': 0})
            self._guild_data[guild_id]['cmds'] += 1
    
    def record_search(self, success: bool, duration: float, guild_id: Optional[str] = None):
        m = self._metrics['search']
        m['total'] += 1
        m['success' if success else 'fail'] += 1
        m['last_hour'] += 1
        m['total_time'] += duration
        m['avg_time'] = m['total_time'] / m['total'] if m['total'] > 0 else 0
        
        if guild_id:
            self._guild_data.setdefault(guild_id, {'cmds': 0, 'searches': 0, 'resp_time': 0})
            self._guild_data[guild_id]['searches'] += 1
    
    def record_response(self, time: float, guild_id: Optional[str] = None):
        time_ms = time * 1000
        m = self._metrics['perf']
        m['total'] += 1
        m['times'].append(time_ms)
        if len(m['times']) > 100: m['times'].pop(0)
        m['avg'] = sum(m['times']) / len(m['times']) if m['times'] else 0
        
        if guild_id:
            self._guild_data.setdefault(guild_id, {'cmds': 0, 'searches': 0, 'resp_time': 0})
            gd = self._guild_data[guild_id]
            gd['resp_time'] = (gd['resp_time'] * 0.9) + (time_ms * 0.1)
    
    def update_concurrent_searches(self, count: int):
        self._metrics['search']['peak'] = max(self._metrics['search']['peak'], count)
    
    def update_cache_stats(self, stats: Dict):
        c = self._metrics['cache']
        if 'memory_size' in stats: c['size'] = stats['memory_size']
        if 'hit_rate_pct' in stats: c['mem_hit'] = (c['mem_hit'] * 0.9) + (stats['hit_rate_pct'] * 0.1)
        if 'redis_hits' in stats and 'misses' in stats:
            total = stats['redis_hits'] + stats['misses']
            if total > 0:
                hit_rate = (stats['redis_hits'] / total) * 100
                c['redis_hit'] = (c['redis_hit'] * 0.9) + (hit_rate * 0.1)
    
    async def _bg_stats_update(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                self._metrics['search']['last_hour'] = 0
                with ThreadPoolExecutor(max_workers=1) as pool:
                    await self.bot.loop.run_in_executor(pool, self._update_sys_metrics)
                await asyncio.sleep(3600)  # hourly
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[boundary:error] Stats error: {e}")
                await asyncio.sleep(60)
    
    def _update_sys_metrics(self):
        try:
            process = psutil.Process(os.getpid())
            with process.oneshot():
                _ = process.cpu_percent()
                _ = process.memory_info()
        except Exception as e:
            logger.error(f"[boundary:error] Metrics error: {e}")
    
    def cog_unload(self):
        if hasattr(self, 'bg_task'): self.bg_task.cancel()

class StatsView(discord.ui.View):
    def __init__(self, user_id, basic, detailed):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.basic = basic
        self.detailed = detailed
    
    async def interaction_check(self, interaction):
        return interaction.user.id == self.user_id
    
    @discord.ui.button(label="Basic", style=discord.ButtonStyle.primary, disabled=True)
    async def basic_btn(self, interaction, button):
        button.disabled = True
        self.detailed_btn.disabled = False
        await interaction.response.edit_message(embed=self.basic, view=self)
    
    @discord.ui.button(label="Detailed", style=discord.ButtonStyle.secondary)
    async def detailed_btn(self, interaction, button):
        button.disabled = True
        self.basic_btn.disabled = False
        await interaction.response.edit_message(embed=self.detailed, view=self)

async def setup(bot):
    await bot.add_cog(Stats(bot))