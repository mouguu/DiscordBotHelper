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
    """性能统计与监控系统
    
    为大型服务器优化的性能监控系统，提供实时统计数据与资源使用情况
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
        
        # 缓存统计
        self._cache_stats = {
            'thread_cache_size': 0,
            'memory_hit_rate': 0,
            'redis_hit_rate': 0
        }
        
        # 性能指标
        self._performance_metrics = {
            'avg_response_time': 0,
            'total_responses': 0,
            'response_times': []  # 保留最近100个响应时间以计算平均值
        }
        
        # 启动后台任务
        self.bg_task = self.bot.loop.create_task(self._background_stats_update())
        self._logger.info("性能监控系统已初始化")
    
    @app_commands.command(
        name="bot_stats", 
        description="显示机器人性能统计"
    )
    async def bot_stats(self, interaction: discord.Interaction):
        """显示机器人性能统计"""
        await interaction.response.defer()
        
        # 系统信息
        process = psutil.Process()
        with process.oneshot():
            memory_usage = process.memory_info().rss / (1024 * 1024)  # MB
            cpu_percent = process.cpu_percent() / psutil.cpu_count()
            uptime = timedelta(seconds=int(time.time() - self._start_time))
            thread_count = process.num_threads()
            open_files = len(process.open_files())
            
        # 统计数据
        guild_count = len(self.bot.guilds)
        user_count = sum(g.member_count for g in self.bot.guilds)
        
        # 创建主嵌入
        embed = discord.Embed(
            title="机器人性能统计",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # 基本信息
        embed.add_field(name="运行时间", value=f"{uptime}", inline=True)
        embed.add_field(name="服务器数量", value=f"{guild_count:,}", inline=True)
        embed.add_field(name="用户数量", value=f"{user_count:,}", inline=True)
        
        # 系统资源
        embed.add_field(name="内存使用", value=f"{memory_usage:.2f} MB", inline=True)
        embed.add_field(name="CPU使用", value=f"{cpu_percent:.1f}%", inline=True)
        embed.add_field(name="线程数", value=f"{thread_count}", inline=True)
        
        # 搜索统计
        if self._search_stats['total_searches'] > 0:
            avg_time = self._search_stats['avg_search_time']
            success_rate = (self._search_stats['successful_searches'] / self._search_stats['total_searches']) * 100
            
            search_stats = (
                f"总计: {self._search_stats['total_searches']:,}\n"
                f"成功率: {success_rate:.1f}%\n"
                f"平均时间: {avg_time:.2f}秒\n"
                f"最近一小时: {self._search_stats['last_hour_searches']}\n"
                f"峰值并发: {self._search_stats['peak_concurrent']}"
            )
            embed.add_field(name="搜索统计", value=search_stats, inline=False)
        
        # 缓存统计
        cache_stats = (
            f"线程缓存大小: {self._cache_stats['thread_cache_size']:,}\n"
            f"内存缓存命中率: {self._cache_stats['memory_hit_rate']:.1f}%\n"
            f"Redis缓存命中率: {self._cache_stats['redis_hit_rate']:.1f}%"
        )
        embed.add_field(name="缓存统计", value=cache_stats, inline=False)
        
        # 性能指标
        if self._performance_metrics['total_responses'] > 0:
            perf_stats = (
                f"平均响应时间: {self._performance_metrics['avg_response_time']:.2f}ms\n"
                f"处理请求数: {self._performance_metrics['total_responses']:,}"
            )
            embed.add_field(name="性能指标", value=perf_stats, inline=False)
        
        # 最常用命令
        if self._command_usage:
            top_commands = sorted(
                self._command_usage.items(), 
                key=lambda x: x[1], 
                reverse=True
            )[:5]
            
            cmd_text = "\n".join(f"`/{cmd}`: {count:,}次" for cmd, count in top_commands)
            embed.add_field(name="最常用命令", value=cmd_text, inline=False)
        
        # 最活跃服务器
        if len(self._guild_usage) > 1:  # 只在有多个服务器时显示
            top_guilds = sorted(
                self._guild_usage.items(),
                key=lambda x: x[1]['commands'],
                reverse=True
            )[:3]
            
            guild_text = ""
            for guild_id, stats in top_guilds:
                guild = self.bot.get_guild(int(guild_id))
                guild_name = guild.name if guild else f"ID:{guild_id}"
                guild_text += f"{guild_name}: {stats['commands']:,}次命令\n"
            
            if guild_text:
                embed.add_field(name="最活跃服务器", value=guild_text, inline=False)
        
        # 添加系统信息
        sys_info = (
            f"Python: {platform.python_version()}\n"
            f"discord.py: {discord.__version__}\n"
            f"系统: {platform.system()} {platform.release()}"
        )
        embed.set_footer(text=sys_info)
        
        # 创建详细嵌入
        detailed_embed = discord.Embed(
            title="详细性能数据",
            color=discord.Color.blue(),
            timestamp=datetime.now()
        )
        
        # 内存详情
        memory = psutil.virtual_memory()
        memory_details = (
            f"进程内存: {memory_usage:.2f} MB\n"
            f"系统内存: {memory.percent:.1f}% 使用中\n"
            f"可用内存: {memory.available/(1024*1024*1024):.2f} GB\n"
            f"Python对象: {len(gc.get_objects()):,}"
        )
        detailed_embed.add_field(name="内存详情", value=memory_details, inline=False)
        
        # 网络统计
        net_io = psutil.net_io_counters()
        net_stats = (
            f"发送: {net_io.bytes_sent/(1024*1024):.2f} MB\n"
            f"接收: {net_io.bytes_recv/(1024*1024):.2f} MB\n"
            f"打开文件: {open_files}"
        )
        detailed_embed.add_field(name="网络统计", value=net_stats, inline=False)
        
        # Discord连接信息
        discord_stats = (
            f"Websocket延迟: {self.bot.latency*1000:.2f}ms\n"
            f"事件处理器: {len(self.bot.extra_events):,}\n"
            f"命令数量: {len(self.bot.tree.get_commands()):,}"
        )
        detailed_embed.add_field(name="Discord连接", value=discord_stats, inline=False)
        
        # 创建视图
        view = StatsDetailView(interaction.user.id, embed, detailed_embed)
        
        await interaction.followup.send(embed=embed, view=view)
    
    @app_commands.command(
        name="server_stats",
        description="显示当前服务器的统计信息"
    )
    @app_commands.guild_only()
    async def server_stats(self, interaction: discord.Interaction):
        """显示当前服务器的统计信息"""
        if not interaction.guild:
            await interaction.response.send_message("此命令只能在服务器中使用", ephemeral=True)
            return
        
        await interaction.response.defer()
        
        guild = interaction.guild
        guild_id = str(guild.id)
        
        # 基本信息
        member_count = guild.member_count
        bot_count = len([m for m in guild.members if m.bot])
        human_count = member_count - bot_count
        
        # 频道统计
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        categories = len(guild.categories)
        forum_channels = len([c for c in guild.channels if isinstance(c, discord.ForumChannel)])
        thread_count = len(guild.threads)
        
        # 创建嵌入
        embed = discord.Embed(
            title=f"{guild.name} 服务器统计",
            color=discord.Color.green(),
            timestamp=datetime.now()
        )
        
        # 添加图标
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        
        # 基本信息
        embed.add_field(name="成员数", value=f"总计: {member_count:,}\n人类: {human_count:,}\n机器人: {bot_count:,}", inline=True)
        embed.add_field(name="创建日期", value=guild.created_at.strftime("%Y-%m-%d"), inline=True)
        embed.add_field(name="所有者", value=guild.owner.mention if guild.owner else "未知", inline=True)
        
        # 频道统计
        embed.add_field(
            name="频道统计", 
            value=f"文字频道: {text_channels}\n语音频道: {voice_channels}\n分类: {categories}\n论坛: {forum_channels}\n线程: {thread_count}", 
            inline=True
        )
        
        # 身份组
        embed.add_field(name="身份组数量", value=str(len(guild.roles) - 1), inline=True)  # -1 排除@everyone
        
        # 表情符号统计
        embed.add_field(
            name="表情符号", 
            value=f"普通: {len(guild.emojis)}\n动态: {len([e for e in guild.emojis if e.animated])}", 
            inline=True
        )
        
        # 机器人使用统计
        if guild_id in self._guild_usage:
            usage = self._guild_usage[guild_id]
            usage_text = (
                f"命令使用: {usage.get('commands', 0):,}次\n"
                f"搜索次数: {usage.get('searches', 0):,}次\n"
                f"平均响应: {usage.get('avg_response', 0):.2f}ms"
            )
            embed.add_field(name="机器人使用", value=usage_text, inline=False)
        
        # 权限检查
        permissions = guild.get_member(self.bot.user.id).guild_permissions
        missing_perms = []
        
        if not permissions.manage_webhooks:
            missing_perms.append("管理Webhooks")
        if not permissions.read_message_history:
            missing_perms.append("读取消息历史")
        if not permissions.add_reactions:
            missing_perms.append("添加反应")
        if not permissions.embed_links:
            missing_perms.append("嵌入链接")
        
        if missing_perms:
            embed.add_field(
                name="⚠️ 缺少权限",
                value="机器人缺少以下权限:\n" + "\n".join(f"- {p}" for p in missing_perms),
                inline=False
            )
        
        embed.set_footer(text=f"服务器ID: {guild.id}")
        await interaction.followup.send(embed=embed)
    
    def record_command_usage(self, command_name: str, guild_id: Optional[str] = None):
        """记录命令使用情况"""
        # 全局命令使用统计
        if command_name in self._command_usage:
            self._command_usage[command_name] += 1
        else:
            self._command_usage[command_name] = 1
        
        # 服务器级别使用统计
        if guild_id:
            if guild_id not in self._guild_usage:
                self._guild_usage[guild_id] = {'commands': 0, 'searches': 0, 'avg_response': 0}
            
            self._guild_usage[guild_id]['commands'] += 1
    
    def record_search(self, successful: bool, duration: float, guild_id: Optional[str] = None):
        """记录搜索统计"""
        self._search_stats['total_searches'] += 1
        
        if successful:
            self._search_stats['successful_searches'] += 1
        else:
            self._search_stats['failed_searches'] += 1
        
        # 更新平均时间
        total = self._search_stats['total_search_time'] + duration
        self._search_stats['total_search_time'] = total
        self._search_stats['avg_search_time'] = total / self._search_stats['total_searches']
        
        # 服务器级别统计
        if guild_id:
            if guild_id not in self._guild_usage:
                self._guild_usage[guild_id] = {'commands': 0, 'searches': 0, 'avg_response': 0}
            
            self._guild_usage[guild_id]['searches'] += 1
    
    def record_response_time(self, response_time: float, guild_id: Optional[str] = None):
        """记录命令响应时间"""
        self._performance_metrics['total_responses'] += 1
        
        # 保持最近100个响应时间用于计算平均值
        times = self._performance_metrics['response_times']
        times.append(response_time)
        if len(times) > 100:
            times.pop(0)
        
        # 重新计算平均值
        self._performance_metrics['avg_response_time'] = sum(times) / len(times)
        
        # 服务器级别统计
        if guild_id:
            if guild_id in self._guild_usage:
                # 移动平均更新
                current = self._guild_usage[guild_id].get('avg_response', 0)
                count = self._guild_usage[guild_id].get('commands', 0)
                if count > 0:
                    # 90%旧值权重，10%新值权重，平滑变化
                    new_avg = (current * 0.9) + (response_time * 0.1)
                    self._guild_usage[guild_id]['avg_response'] = new_avg
    
    def update_concurrent_searches(self, current_count: int):
        """更新并发搜索计数"""
        if current_count > self._search_stats['peak_concurrent']:
            self._search_stats['peak_concurrent'] = current_count
    
    def update_cache_stats(self, cache_stats: Dict[str, Any]):
        """更新缓存统计信息"""
        if 'memory_size' in cache_stats:
            self._cache_stats['thread_cache_size'] = cache_stats['memory_size']
        
        if 'hit_rate_pct' in cache_stats:
            # 移动平均
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
        """后台任务：更新统计数据"""
        try:
            await self.bot.wait_until_ready()
            self._logger.info("性能监控后台任务已启动")
            
            while not self.bot.is_closed():
                # 每小时重置计数器
                self._search_stats['last_hour_searches'] = 0
                
                # 更新系统资源使用情况
                with ThreadPoolExecutor(max_workers=1) as executor:
                    await self.bot.loop.run_in_executor(
                        executor,
                        self._update_system_metrics
                    )
                
                # 等待1小时
                await asyncio.sleep(3600)
                
        except asyncio.CancelledError:
            self._logger.info("性能监控后台任务已取消")
        except Exception as e:
            self._logger.error(f"性能监控后台任务错误: {e}", exc_info=True)
    
    def _update_system_metrics(self):
        """更新系统指标(在线程池中运行)"""
        try:
            process = psutil.Process()
            
            # 收集系统信息
            with process.oneshot():
                cpu_percent = process.cpu_percent()
                memory_info = process.memory_info()
                io_counters = process.io_counters()
                
            self._logger.debug(
                f"系统指标更新 - CPU: {cpu_percent}%, "
                f"内存: {memory_info.rss/(1024*1024):.1f}MB, "
                f"IO读取: {io_counters.read_bytes/(1024*1024):.1f}MB, "
                f"IO写入: {io_counters.write_bytes/(1024*1024):.1f}MB"
            )
            
        except Exception as e:
            self._logger.error(f"更新系统指标时出错: {e}")
    
    def cog_unload(self):
        """当Cog被卸载时清理资源"""
        if self.bg_task:
            self.bg_task.cancel()


class StatsDetailView(discord.ui.View):
    """统计信息详情视图"""
    
    def __init__(self, user_id: int, basic_embed: discord.Embed, detailed_embed: discord.Embed):
        super().__init__(timeout=180)  # 3分钟超时
        self.user_id = user_id
        self.basic_embed = basic_embed
        self.detailed_embed = detailed_embed
        self.current_embed = "basic"
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """只允许原始用户交互"""
        return interaction.user.id == self.user_id
    
    @discord.ui.button(label="基本信息", style=discord.ButtonStyle.primary, disabled=True)
    async def basic_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """显示基本信息"""
        self.current_embed = "basic"
        self.basic_button.disabled = True
        self.detail_button.disabled = False
        await interaction.response.edit_message(embed=self.basic_embed, view=self)
    
    @discord.ui.button(label="详细信息", style=discord.ButtonStyle.secondary)
    async def detail_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """显示详细信息"""
        self.current_embed = "detailed"
        self.basic_button.disabled = False
        self.detail_button.disabled = True
        await interaction.response.edit_message(embed=self.detailed_embed, view=self)
    
    @discord.ui.button(label="刷新", style=discord.ButtonStyle.success)
    async def refresh_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """刷新统计信息"""
        # 通知用户正在刷新
        await interaction.response.defer()
        
        # 重新调用统计命令
        await interaction.followup.send("正在刷新统计信息...", ephemeral=True)
        await interaction.client.tree.get_command("bot_stats").callback(self, interaction)


async def setup(bot):
    await bot.add_cog(Stats(bot)) 