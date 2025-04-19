import discord
from discord.ext import commands
from discord import app_commands
import psutil
import time
import platform
import asyncio
import os
import sys
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import logging
from concurrent.futures import ThreadPoolExecutor
import gc

class Stats(commands.Cog):
    """Performance statistics and monitoring system
    
    Optimized performance monitoring system for large servers, providing real-time statistics and resource usage information
    """
    
    def __init__(self, bot):
        self.bot = bot
        self._start_time = time.time()
        self._command_usage = {}
        self._guild_usage = {}
        self._search_stats = {
            'total_searches': 0,
            'successful_searches': 0,
            'failed_searches': 0,
            'avg_search_time': 0,
            'total_search_time': 0,
            'last_hour_searches': 0,
            'peak_concurrent': 0
        }
        self._logger = logging.getLogger('discord_bot.stats')
        
        # Cache statistics
        self._cache_stats = {
            'thread_cache_size': 0,
            'memory_hit_rate': 0,
            'redis_hit_rate': 0
        }
        
        # Performance metrics
        self._performance_metrics = {
            'avg_response_time': 0,
            'total_responses': 0,
            'response_times': []  # Keep the last 100 response times to calculate the average
        }
        
        # Start background task
        self.bg_task = self.bot.loop.create_task(self._background_stats_update())
        self._logger.info("Performance monitoring system initialized")
    
    @app_commands.command(
        name="bot_stats", 
        description="Show bot performance statistics"
    )
    async def bot_stats(self, interaction: discord.Interaction):
        """Show bot performance statistics"""
        await interaction.response.defer()
        
        # System information
        process = psutil.Process()
        with process.oneshot():
            memory_usage = process.memory_info().rss / (1024 * 1024)  # MB
            cpu_percent = process.cpu_percent() / psutil.cpu_count()
            uptime = timedelta(seconds=int(time.time() - self._start_time))
            thread_count = process.num_threads()
            open_files = len(process.open_files())
            
        # Statistics
        guild_count = len(self.bot.guilds)
        user_count = sum(g.member_count for g in self.bot.guilds if g.member_count is not None) # Ensure member_count is available
        
        # Create main embed
        embed = discord.Embed(
            title="Bot Performance Statistics",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # Basic information
        embed.add_field(name="Uptime", value=f"{uptime}", inline=True)
        embed.add_field(name="Server Count", value=f"{guild_count:,}", inline=True)
        embed.add_field(name="User Count", value=f"{user_count:,}", inline=True)
        
        # System resources
        embed.add_field(name="Memory Usage", value=f"{memory_usage:.2f} MB", inline=True)
        embed.add_field(name="CPU Usage", value=f"{cpu_percent:.1f}%", inline=True)
        embed.add_field(name="Threads", value=f"{thread_count}", inline=True)
        
        # Search statistics
        if self._search_stats['total_searches'] > 0:
            avg_time = self._search_stats['avg_search_time']
            success_rate = (self._search_stats['successful_searches'] / self._search_stats['total_searches']) * 100
            
            search_stats = (
                f"Total: {self._search_stats['total_searches']:,}\n"
                f"Success Rate: {success_rate:.1f}%\n"
                f"Avg Time: {avg_time:.2f}s\n"
                f"Last Hour: {self._search_stats['last_hour_searches']}\n"
                f"Peak Concurrent: {self._search_stats['peak_concurrent']}"
            )
            embed.add_field(name="Search Statistics", value=search_stats, inline=False)
        
        # Cache statistics
        cache_stats = (
            f"Thread Cache Size: {self._cache_stats['thread_cache_size']:,}\n"
            f"Memory Hit Rate: {self._cache_stats['memory_hit_rate']:.1f}%\n"
            f"Redis Hit Rate: {self._cache_stats['redis_hit_rate']:.1f}%"
        )
        embed.add_field(name="Cache Statistics", value=cache_stats, inline=False)
        
        # Performance metrics
        if self._performance_metrics['total_responses'] > 0:
            perf_stats = (
                f"Avg Response Time: {self._performance_metrics['avg_response_time']:.2f}ms\n"
                f"Requests Processed: {self._performance_metrics['total_responses']:,}"
            )
            embed.add_field(name="Performance Metrics", value=perf_stats, inline=False)
        
        # Most used commands
        if self._command_usage:
            top_commands = sorted(
                self._command_usage.items(), 
                key=lambda x: x[1], 
                reverse=True
            )[:5]
            
            cmd_text = "\n".join(f"`/{cmd}`: {count:,} times" for cmd, count in top_commands)
            embed.add_field(name="Most Used Commands", value=cmd_text, inline=False)
        
        # Most active servers
        if len(self._guild_usage) > 1:  # Only show if there are multiple servers
            top_guilds = sorted(
                self._guild_usage.items(),
                key=lambda x: x[1]['commands'],
                reverse=True
            )[:3]
            
            guild_text = ""
            for guild_id, stats in top_guilds:
                guild = self.bot.get_guild(int(guild_id))
                guild_name = guild.name if guild else f"ID:{guild_id}"
                guild_text += f"{guild_name}: {stats['commands']:,} commands\n"
            
            if guild_text:
                embed.add_field(name="Most Active Servers", value=guild_text.strip(), inline=False) # Use strip to remove trailing newline
        
        # Add system information
        sys_info = (
            f"Python: {platform.python_version()}\n"
            f"discord.py: {discord.__version__}\n"
            f"OS: {platform.system()} {platform.release()}"
        )
        embed.set_footer(text=sys_info)
        
        # Create detailed embed
        detailed_embed = discord.Embed(
            title="Detailed Performance Data",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # Memory details
        memory = psutil.virtual_memory()
        memory_details = (
            f"Process Memory: {memory_usage:.2f} MB\n"
            f"System Memory: {memory.percent:.1f}% used\n"
            f"Available Memory: {memory.available/(1024*1024*1024):.2f} GB\n"
            f"Python Objects: {len(gc.get_objects()):,}"
        )
        detailed_embed.add_field(name="Memory Details", value=memory_details, inline=False)
        
        # Network statistics
        net_io = psutil.net_io_counters()
        net_stats = (
            f"Sent: {net_io.bytes_sent/(1024*1024):.2f} MB\n"
            f"Received: {net_io.bytes_recv/(1024*1024):.2f} MB\n"
            f"Open Files: {open_files}"
        )
        detailed_embed.add_field(name="Network Statistics", value=net_stats, inline=False)
        
        # Discord connection information
        discord_stats = (
            f"Websocket Latency: {self.bot.latency*1000:.2f}ms\n"
            f"Event Handlers: {len(self.bot.extra_events):,}\n"
            f"Command Count: {len(self.bot.tree.get_commands()):,}"
        )
        detailed_embed.add_field(name="Discord Connection", value=discord_stats, inline=False)
        
        # Create view
        view = StatsDetailView(interaction.user.id, embed, detailed_embed)
        
        await interaction.followup.send(embed=embed, view=view)
    
    @app_commands.command(
        name="server_stats",
        description="Show statistics for the current server"
    )
    @app_commands.guild_only()
    async def server_stats(self, interaction: discord.Interaction):
        """Show statistics for the current server"""
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        guild = interaction.guild
        guild_id = str(guild.id)
        
        # Basic information
        member_count = guild.member_count or 0 # Handle None case
        bot_count = len([m for m in guild.members if m.bot])
        human_count = member_count - bot_count
        
        # Channel statistics
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        categories = len(guild.categories)
        forum_channels = len([c for c in guild.channels if isinstance(c, discord.ForumChannel)])
        thread_count = len(guild.threads)
        
        # Create embed
        embed = discord.Embed(
            title=f"{guild.name} Server Statistics",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        
        # Add icon
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        
        # Basic information
        embed.add_field(name="Member Count", value=f"Total: {member_count:,}\nHumans: {human_count:,}\nBots: {bot_count:,}", inline=True)
        embed.add_field(name="Created Date", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
        embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "Unknown", inline=True)
        
        # Channel statistics
        embed.add_field(
            name="Channel Statistics",
            value=f"Text Channels: {text_channels}\nVoice Channels: {voice_channels}\nCategories: {categories}\nForums: {forum_channels}\nThreads: {thread_count}",
            inline=True
        )
        
        # Roles
        embed.add_field(name="Role Count", value=str(len(guild.roles) - 1), inline=True) # -1 to exclude @everyone
        
        # Emoji statistics
        embed.add_field(
            name="Emojis",
            value=f"Standard: {len(guild.emojis)}\nAnimated: {len([e for e in guild.emojis if e.animated])}",
            inline=True
        )
        
        # Bot usage statistics
        if guild_id in self._guild_usage:
            usage = self._guild_usage[guild_id]
            usage_text = (
                f"Commands Used: {usage.get('commands', 0):,} times\n"
                f"Searches: {usage.get('searches', 0):,} times\n"
                f"Avg Response: {usage.get('avg_response', 0):.2f}ms"
            )
            embed.add_field(name="Bot Usage", value=usage_text, inline=False)
        
        # Permission check
        bot_member = guild.get_member(self.bot.user.id)
        if bot_member: # Check if bot is still in the guild
            permissions = bot_member.guild_permissions
            missing_perms = []

            required_perms = {
                "manage_webhooks": "Manage Webhooks",
                "read_message_history": "Read Message History",
                "add_reactions": "Add Reactions",
                "embed_links": "Embed Links"
            }

            for perm, name in required_perms.items():
                if not getattr(permissions, perm, False): # Check if attribute exists and is True
                    missing_perms.append(name)
            
            if missing_perms:
                embed.add_field(
                    name="⚠️ Missing Permissions",
                    value="The bot is missing the following permissions:\n" + "\n".join(f"- {p}" for p in missing_perms),
                    inline=False
                )
        else:
             embed.add_field(name="⚠️ Bot Status", value="Could not retrieve bot permissions.", inline=False)

        
        embed.set_footer(text=f"Server ID: {guild.id}")
        await interaction.followup.send(embed=embed)
    
    def record_command_usage(self, command_name: str, guild_id: Optional[str] = None):
        """Record command usage"""
        # Global command usage statistics
        if command_name in self._command_usage:
            self._command_usage[command_name] += 1
        else:
            self._command_usage[command_name] = 1
        
        # Server-level usage statistics
        if guild_id:
            if guild_id not in self._guild_usage:
                self._guild_usage[guild_id] = {'commands': 0, 'searches': 0, 'avg_response': 0}
            self._guild_usage[guild_id]['commands'] += 1
    
    def record_search(self, successful: bool, duration: float, guild_id: Optional[str] = None):
        """Record search statistics"""
        self._search_stats['total_searches'] += 1
        
        if successful:
            self._search_stats['successful_searches'] += 1
        else:
            self._search_stats['failed_searches'] += 1
        
        # Update average time
        total = self._search_stats['total_search_time'] + duration
        self._search_stats['total_search_time'] = total
        if self._search_stats['total_searches'] > 0: # Avoid division by zero
             self._search_stats['avg_search_time'] = total / self._search_stats['total_searches']
        
        # Server-level statistics
        if guild_id:
            if guild_id not in self._guild_usage:
                self._guild_usage[guild_id] = {'commands': 0, 'searches': 0, 'avg_response': 0}
            self._guild_usage[guild_id]['searches'] += 1
    
    def record_response_time(self, response_time: float, guild_id: Optional[str] = None):
        """Record command response time"""
        response_time_ms = response_time * 1000 # Convert seconds to milliseconds
        self._performance_metrics['total_responses'] += 1
        
        # Keep the last 100 response times to calculate the average
        times = self._performance_metrics['response_times']
        times.append(response_time_ms)
        if len(times) > 100:
            times.pop(0)
        
        # Recalculate average
        if times: # Avoid division by zero
             self._performance_metrics['avg_response_time'] = sum(times) / len(times)
        
        # Server-level statistics
        if guild_id:
            if guild_id in self._guild_usage:
                # Moving average update
                current = self._guild_usage[guild_id].get('avg_response', 0)
                count = self._guild_usage[guild_id].get('commands', 0)
                if count > 0:
                    # 90% weight for old value, 10% weight for new value, smooth change
                    new_avg = (current * 0.9) + (response_time_ms * 0.1)
                    self._guild_usage[guild_id]['avg_response'] = new_avg
    
    def update_concurrent_searches(self, current_count: int):
        """Update concurrent search count"""
        if current_count > self._search_stats['peak_concurrent']:
            self._search_stats['peak_concurrent'] = current_count
    
    def update_cache_stats(self, cache_stats: Dict[str, Any]):
        """Update cache statistics"""
        if 'memory_size' in cache_stats:
            self._cache_stats['thread_cache_size'] = cache_stats['memory_size']
        
        if 'hit_rate_pct' in cache_stats:
            # Moving average
            current = self._cache_stats['memory_hit_rate']
            new_rate = cache_stats['hit_rate_pct']
            self._cache_stats['memory_hit_rate'] = (current * 0.9) + (new_rate * 0.1)
        
        if 'redis_hits' in cache_stats and 'misses' in cache_stats:
            total = cache_stats['redis_hits'] + cache_stats['misses']
            if total > 0:
                redis_rate = (cache_stats['redis_hits'] / total) * 100
                current = self._cache_stats['redis_hit_rate']
                self._cache_stats['redis_hit_rate'] = (current * 0.9) + (redis_rate * 0.1)
    
    async def _background_stats_update(self):
        """Background task: update statistics"""
        try:
            await self.bot.wait_until_ready()
            self._logger.info("Performance monitoring background task started")
            
            while not self.bot.is_closed():
                # Reset counters every hour
                self._search_stats['last_hour_searches'] = 0
                
                # Update system resource usage (in thread pool to avoid blocking)
                with ThreadPoolExecutor(max_workers=1) as executor:
                    await self.bot.loop.run_in_executor(
                        executor,
                        self._update_system_metrics
                    )
                
                # Wait for 1 hour
                await asyncio.sleep(3600)
                
        except asyncio.CancelledError:
            self._logger.info("Performance monitoring background task cancelled")
        except Exception as e:
            self._logger.error(f"Performance monitoring background task error: {e}", exc_info=True)
    
    def _update_system_metrics(self):
        """Update system metrics (runs in thread pool)"""
        try:
            process = psutil.Process(os.getpid()) # Get current process
            
            # Collect system information
            with process.oneshot():
                cpu_percent = process.cpu_percent(interval=None) # Use interval=None for instant snapshot
                memory_info = process.memory_info()
                io_counters = process.io_counters()
                
            self._logger.debug(
                f"System metrics update - CPU: {cpu_percent}%, "
                f"Memory: {memory_info.rss/(1024*1024):.1f}MB, "
                f"IO Read: {io_counters.read_bytes/(1024*1024):.1f}MB, "
                f"IO Write: {io_counters.write_bytes/(1024*1024):.1f}MB"
            )
            
        except psutil.NoSuchProcess:
             self._logger.warning("Process not found during system metrics update.")
        except Exception as e:
            self._logger.error(f"Error updating system metrics: {e}")
    
    def cog_unload(self):
        """Clean up resources when Cog is unloaded"""
        if self.bg_task:
            self.bg_task.cancel()


class StatsDetailView(discord.ui.View):
    """Statistics detail view"""
    
    def __init__(self, user_id: int, basic_embed: discord.Embed, detailed_embed: discord.Embed):
        super().__init__(timeout=180)  # 3 minutes timeout
        self.user_id = user_id
        self.basic_embed = basic_embed
        self.detailed_embed = detailed_embed
        self.current_embed = "basic"
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Allow only the original user to interact"""
        if interaction.user.id != self.user_id:
             await interaction.response.send_message("Only the user who initiated the command can use these buttons.", ephemeral=True)
             return False
        return True
    
    @discord.ui.button(label="Basic Info", style=discord.ButtonStyle.primary, disabled=True)
    async def basic_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Show basic information"""
        self.current_embed = "basic"
        self.basic_button.disabled = True
        self.detail_button.disabled = False
        await interaction.response.edit_message(embed=self.basic_embed, view=self)
    
    @discord.ui.button(label="Detailed Info", style=discord.ButtonStyle.secondary)
    async def detail_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Show detailed information"""
        self.current_embed = "detailed"
        self.basic_button.disabled = False
        self.detail_button.disabled = True
        await interaction.response.edit_message(embed=self.detailed_embed, view=self)
    
    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.success)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Refresh statistics (placeholder, ideally re-runs the original command logic)"""
        # Notify user that it's refreshing
        await interaction.response.defer()
        
        # A proper refresh would re-fetch all the stats and update the embeds.
        # This is complex to implement here without access to the original command's context.
        # For now, we'll just send a message indicating refresh attempt.
        
        # Example of how it *could* work if we had access to the cog instance:
        # stats_cog = interaction.client.get_cog('Stats')
        # if stats_cog:
        #     # Re-generate embeds with fresh data (This requires the stat generation logic to be refactored)
        #     # new_basic_embed, new_detailed_embed = await stats_cog.generate_stats_embeds() 
        #     # self.basic_embed = new_basic_embed
        #     # self.detailed_embed = new_detailed_embed
        #     # current_embed_to_show = self.basic_embed if self.current_embed == "basic" else self.detailed_embed
        #     # await interaction.edit_original_response(embed=current_embed_to_show, view=self)
        #     await interaction.followup.send("Statistics refreshed (simulation).", ephemeral=True)
        # else:
        #     await interaction.followup.send("Could not refresh statistics.", ephemeral=True)
            
        await interaction.followup.send("Refreshing statistics... (This action currently simulates a refresh)", ephemeral=True)


async def setup(bot):
    await bot.add_cog(Stats(bot)) 