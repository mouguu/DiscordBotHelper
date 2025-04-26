import discord
from discord.ext import commands
from discord import app_commands
import psutil, time, platform, asyncio, os, sys, gc, logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger('discord_bot.stats')

class Stats(commands.Cog):
    """Performance statistics and monitoring"""
    
    def __init__(self, bot):
        self.bot = bot
        self._start_time = time.time()
        self._command_usage = defaultdict(int) # Use defaultdict for counters
        self._guild_usage = defaultdict(lambda: {'commands': 0, 'searches': 0, 'avg_response': 0.0})
        self._search_stats = defaultdict(float, { # Use float defaultdict for averages/totals
            'total_searches': 0, # Keep int for counts
            'successful_searches': 0,
            'failed_searches': 0,
            'peak_concurrent': 0
        })
        self._cache_stats = defaultdict(float)
        self._performance_metrics = {
            'avg_response_time': 0.0,
            'total_responses': 0,
            'response_times': [] # Cap maintained later
        }
        
        # Start background task
        self.bg_task = self.bot.loop.create_task(self._background_stats_update())
    
    @app_commands.command(
        name="bot_stats", 
        description="Show bot performance statistics"
    )
    async def bot_stats(self, interaction: discord.Interaction):
        """Show bot performance statistics"""
        await interaction.response.defer()
        
        # Gather System & Bot Info
        process = psutil.Process()
        with process.oneshot():
            mem_info = process.memory_info()
            cpu_percent = process.cpu_percent() / psutil.cpu_count()
            threads = process.num_threads()
            open_files = len(process.open_files())
        memory_usage_mb = mem_info.rss / (1024 * 1024)
        uptime = timedelta(seconds=int(time.time() - self._start_time))
        guild_count = len(self.bot.guilds)
        user_count = sum(g.member_count for g in self.bot.guilds if g.member_count is not None)

        # Build Basic Embed
        embed = discord.Embed(title="Bot Performance Statistics", color=discord.Color.blue(), timestamp=datetime.now())
        embed.add_field(name="Uptime", value=str(uptime), inline=True)
        embed.add_field(name="Servers", value=f"{guild_count:,}", inline=True)
        embed.add_field(name="Users", value=f"{user_count:,}", inline=True)
        embed.add_field(name="Memory", value=f"{memory_usage_mb:.2f} MB", inline=True)
        embed.add_field(name="CPU", value=f"{cpu_percent:.1f}%", inline=True)
        embed.add_field(name="Threads", value=str(threads), inline=True)

        # Add Search Stats if available
        total_searches = int(self._search_stats['total_searches'])
        if total_searches > 0:
            successful_searches = int(self._search_stats['successful_searches'])
            success_rate = (successful_searches / total_searches) * 100
            avg_time = self._search_stats['avg_search_time']
            embed.add_field(name="Search Stats", 
                            value=f"Total: {total_searches:,} | Success: {success_rate:.1f}% | Avg Time: {avg_time:.2f}s\n" + 
                                  f"Last Hour: {int(self._search_stats['last_hour_searches'])} | Peak Concurrent: {int(self._search_stats['peak_concurrent'])}", 
                            inline=False)

        # Add Cache Stats
        embed.add_field(name="Cache Stats", 
                        value=f"Thread Cache: {int(self._cache_stats['thread_cache_size']):,} | Mem Hit: {self._cache_stats['memory_hit_rate']:.1f}% | Redis Hit: {self._cache_stats['redis_hit_rate']:.1f}%", 
                        inline=False)

        # Add Performance Metrics if available
        total_responses = self._performance_metrics['total_responses']
        if total_responses > 0:
            avg_resp_time = self._performance_metrics['avg_response_time']
            embed.add_field(name="Performance", value=f"Avg Response: {avg_resp_time:.2f}ms | Requests: {total_responses:,}", inline=False)

        # Add Top Commands if available
        if self._command_usage:
            top_commands = sorted(self._command_usage.items(), key=lambda x: x[1], reverse=True)[:5]
            embed.add_field(name="Top Commands", value="\n".join(f"`/{cmd}`: {count:,}" for cmd, count in top_commands), inline=False)

        # Add Top Guilds if applicable
        if len(self._guild_usage) > 1:
            top_guilds = sorted(self._guild_usage.items(), key=lambda x: x[1]['commands'], reverse=True)[:3]
            guild_texts = [f"{(self.bot.get_guild(int(gid)) or f'ID:{gid}').name}: {stats['commands']:,}" for gid, stats in top_guilds]
            embed.add_field(name="Top Servers", value="\n".join(guild_texts), inline=False)

        # System Info Footer
        embed.set_footer(text=f"Python: {platform.python_version()} | discord.py: {discord.__version__} | OS: {platform.system()}")

        # Build Detailed Embed
        detailed = discord.Embed(title="Detailed Performance Data", color=discord.Color.blue(), timestamp=datetime.now())
        memory = psutil.virtual_memory()
        net_io = psutil.net_io_counters()
        detailed.add_field(name="Memory", value=f"Process: {memory_usage_mb:.2f}MB | System: {memory.percent:.1f}% | Avail: {memory.available/(1024**3):.2f}GB | Objects: {len(gc.get_objects()):,}", inline=False)
        detailed.add_field(name="Network/IO", value=f"Sent: {net_io.bytes_sent/(1024**2):.2f}MB | Recv: {net_io.bytes_recv/(1024**2):.2f}MB | Files: {open_files}", inline=False)
        detailed.add_field(name="Discord", value=f"Latency: {self.bot.latency*1000:.2f}ms | Events: {len(self.bot.extra_events):,} | Cmds: {len(self.bot.tree.get_commands()):,}", inline=False)

        # Send with View
        view = StatsDetailView(interaction.user.id, embed, detailed)
        await interaction.followup.send(embed=embed, view=view)
    
    @app_commands.command(
        name="server_stats",
        description="Show statistics for the current server"
    )
    @app_commands.guild_only()
    async def server_stats(self, interaction: discord.Interaction):
        # Show statistics for the current server
        await interaction.response.defer()
        guild = interaction.guild
        
        # Gather Guild Info
        member_count = guild.member_count or 0
        bot_count = sum(1 for m in guild.members if m.bot)
        owner = guild.owner.mention if guild.owner else "Unknown"
        created = guild.created_at.strftime("%Y-%m-%d")
        text_channels, voice_channels, categories = len(guild.text_channels), len(guild.voice_channels), len(guild.categories)
        forum_channels = sum(1 for c in guild.channels if isinstance(c, discord.ForumChannel))
        thread_count = len(guild.threads)
        role_count = len(guild.roles) - 1
        emoji_count = len(guild.emojis)
        animated_emoji_count = sum(1 for e in guild.emojis if e.animated)

        # Build Embed
        embed = discord.Embed(title=f"{guild.name} Statistics", color=discord.Color.green(), timestamp=datetime.now())
        if guild.icon: embed.set_thumbnail(url=guild.icon.url)
        
        embed.add_field(name="Members", value=f"Total: {member_count:,} | Humans: {member_count - bot_count:,} | Bots: {bot_count:,}", inline=False)
        embed.add_field(name="Info", value=f"Created: {created} | Owner: {owner}", inline=False)
        embed.add_field(name="Channels", value=f"Text: {text_channels} | Voice: {voice_channels} | Cat: {categories} | Forum: {forum_channels} | Threads: {thread_count}", inline=False)
        embed.add_field(name="Misc", value=f"Roles: {role_count} | Emojis: {emoji_count} (Animated: {animated_emoji_count})", inline=False)

        # Add Bot Usage if available
        if usage := self._guild_usage.get(str(guild.id)):
            embed.add_field(name="Bot Usage", value=f"Cmds: {usage['commands']:,} | Searches: {usage['searches']:,} | Avg Resp: {usage['avg_response']:.2f}ms", inline=False)
        
        # Check and Add Missing Permissions
        if bot_member := guild.get_member(self.bot.user.id):
            required = {"manage_webhooks", "read_message_history", "add_reactions", "embed_links"}
            missing = [p.replace("_", " ").title() for p in required if not getattr(bot_member.guild_permissions, p, False)]
            if missing: embed.add_field(name="⚠️ Missing Perms", value=", ".join(missing), inline=False)
        else:
            embed.add_field(name="⚠️ Bot Status", value="Could not retrieve bot perms.", inline=False)

        embed.set_footer(text=f"Server ID: {guild.id}")
        await interaction.followup.send(embed=embed)
    
    # Record command usage (global and guild)
    def record_command_usage(self, command_name: str, guild_id: Optional[str] = None):
        self._command_usage[command_name] += 1
        if guild_id:
            self._guild_usage[guild_id]['commands'] += 1
    
    # Record search stats (global and guild)
    def record_search(self, successful: bool, duration: float, guild_id: Optional[str] = None):
        self._search_stats['total_searches'] += 1
        self._search_stats['successful_searches' if successful else 'failed_searches'] += 1
        self._search_stats['last_hour_searches'] += 1 # Reset hourly by background task
        
        # Update average time
        n = self._search_stats['total_searches']
        self._search_stats['total_search_time'] += duration
        self._search_stats['avg_search_time'] = self._search_stats['total_search_time'] / n if n > 0 else 0
        
        if guild_id:
            self._guild_usage[guild_id]['searches'] += 1
    
    # Record response time, update global avg and guild avg (moving)
    def record_response_time(self, response_time: float, guild_id: Optional[str] = None):
        response_time_ms = response_time * 1000
        self._performance_metrics['total_responses'] += 1
        
        # Update global avg (simple moving average over last 100)
        times = self._performance_metrics['response_times']
        times.append(response_time_ms)
        if len(times) > 100: times.pop(0)
        self._performance_metrics['avg_response_time'] = sum(times) / len(times) if times else 0
        
        # Update guild avg (exponential moving average)
        if guild_id:
            current_avg = self._guild_usage[guild_id]['avg_response']
            self._guild_usage[guild_id]['avg_response'] = (current_avg * 0.9) + (response_time_ms * 0.1)
    
    # Update peak concurrent search count
    def update_concurrent_searches(self, current_count: int):
        self._search_stats['peak_concurrent'] = max(self._search_stats['peak_concurrent'], current_count)
    
    # Update cache stats using exponential moving average for rates
    def update_cache_stats(self, cache_stats: Dict[str, Any]):
        if 'memory_size' in cache_stats:
            self._cache_stats['thread_cache_size'] = cache_stats['memory_size']
        
        # Memory hit rate EMA
        if 'hit_rate_pct' in cache_stats:
            current = self._cache_stats['memory_hit_rate']
            self._cache_stats['memory_hit_rate'] = (current * 0.9) + (cache_stats['hit_rate_pct'] * 0.1)
        
        # Redis hit rate EMA
        if 'redis_hits' in cache_stats and 'misses' in cache_stats:
            total = cache_stats['redis_hits'] + cache_stats['misses']
            if total > 0:
                redis_rate = (cache_stats['redis_hits'] / total) * 100
                current = self._cache_stats['redis_hit_rate']
                self._cache_stats['redis_hit_rate'] = (current * 0.9) + (redis_rate * 0.1)
    
    # Background task: hourly resets and system metrics update
    async def _background_stats_update(self):
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            try:
                # Hourly resets
                self._search_stats['last_hour_searches'] = 0
                
                # Update system metrics (non-blocking)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    await self.bot.loop.run_in_executor(executor, self._update_system_metrics)
                
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                break # Exit loop cleanly on cancellation
            except Exception as e:
                logger.error(f"Stats background task error: {e}", exc_info=True)
                await asyncio.sleep(60) # Wait a bit before retrying after error
    
    # Update system metrics (runs in thread pool)
    def _update_system_metrics(self):
        try:
            process = psutil.Process(os.getpid())
            with process.oneshot(): # Optimize psutil calls
                _ = process.cpu_percent(interval=None) # Capture stats internally
                _ = process.memory_info()
                _ = process.io_counters()
            # Minimal logging unless debugging needed
        except psutil.NoSuchProcess:
             logger.warning("Stats process disappeared during metrics update.")
        except Exception as e:
            logger.error(f"System metrics update error: {e}")
    
    def cog_unload(self):
        self.bg_task and self.bg_task.cancel()

# View to toggle between basic and detailed stats embeds
class StatsDetailView(discord.ui.View):
    
    def __init__(self, user_id: int, basic_embed: discord.Embed, detailed_embed: discord.Embed):
        super().__init__(timeout=180)
        self.user_id = user_id
        self.basic_embed = basic_embed
        self.detailed_embed = detailed_embed
        # Initial state: basic shown, detail button enabled
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
             await interaction.response.send_message("Only command initiator can use buttons.", ephemeral=True)
             return False
        return True
    
    @discord.ui.button(label="Basic Info", style=discord.ButtonStyle.primary, disabled=True)
    async def basic_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        self.detail_button.disabled = False
        await interaction.response.edit_message(embed=self.basic_embed, view=self)
    
    @discord.ui.button(label="Detailed Info", style=discord.ButtonStyle.secondary)
    async def detail_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        button.disabled = True
        self.basic_button.disabled = False
        await interaction.response.edit_message(embed=self.detailed_embed, view=self)
    
    # Refresh button removed as per Zen-Minimalism (avoid non-functional elements)

async def setup(bot):
    await bot.add_cog(Stats(bot))