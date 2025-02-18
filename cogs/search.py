import discord
from discord.ext import commands
from discord import app_commands
from typing import Optional, List, Dict, Tuple
import logging
import re
import pytz
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
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger('discord_bot.search')

class Search(commands.Cog, name="search"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.embed_builder = DiscordEmbedBuilder(EMBED_COLOR)
        self.attachment_processor = AttachmentProcessor()
        self._logger = logger
        self._logger.info("Search cog initialized")
        self._thread_cache = {}
        self._cache_ttl = 300  # 5分钟缓存
        self._search_semaphore = asyncio.Semaphore(CONCURRENT_SEARCH_LIMIT)

    async def _process_thread_batch(self, threads: List[discord.Thread], search_conditions: Dict) -> List[Dict]:
        """并发处理一批线程"""
        tasks = []
        for thread in threads:
            task = asyncio.create_task(self._process_single_thread(thread, search_conditions))
            tasks.append(task)
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if r is not None and not isinstance(r, Exception)]

    async def _process_single_thread(self, thread: discord.Thread, conditions: Dict) -> Optional[Dict]:
        """处理单个线程的搜索"""
        try:
            async with self._search_semaphore:
                # 检查标签条件
                if conditions['search_tags'] and not any(tag.name.lower() in conditions['search_tags'] for tag in thread.applied_tags):
                    return None
                
                if conditions['exclude_tags'] and any(tag.name.lower() in conditions['exclude_tags'] for tag in thread.applied_tags):
                    return None

                # 检查作者条件
                if conditions['original_poster'] and thread.owner.id != conditions['original_poster'].id:
                    return None
                    
                if conditions['exclude_op'] and thread.owner.id == conditions['exclude_op'].id:
                    return None

                # 获取第一条消息
                try:
                    first_message = await thread.fetch_message(thread.id)
                except discord.NotFound:
                    return None
                except Exception as e:
                    self._logger.warning(f"获取线程 {thread.id} 首条消息失败: {e}")
                    return None

                if first_message:
                    # 检查关键词条件
                    content = first_message.content.lower()
                    if conditions['search_keywords'] and not any(keyword in content for keyword in conditions['search_keywords']):
                        return None
                        
                    if conditions['exclude_keywords'] and any(keyword in content for keyword in conditions['exclude_keywords']):
                        return None

                # 获取帖子统计信息
                try:
                    stats = await get_thread_stats(thread)
                except Exception as e:
                    self._logger.error(f"获取线程 {thread.id} 统计信息失败: {e}")
                    stats = {'reaction_count': 0, 'reply_count': 0}

                return {
                    'thread': thread,
                    'stats': stats,
                    'first_message': first_message
                }

        except Exception as e:
            self._logger.error(f"处理线程 {thread.name} ({thread.id}) 时出错: {e}", exc_info=True)
            return None

    @app_commands.command(name="forum_search", description="搜索论坛帖子")
    @app_commands.guild_only()
    @app_commands.describe(
        forum_name="选择要搜索的论坛分区",
        order="结果排序方式",
        original_poster="指定的发帖人（选择成员）",
        tag1="选择要搜索的第一个标签",
        tag2="选择要搜索的第二个标签",
        tag3="选择要搜索的第三个标签",
        search_word="搜索关键词（用逗号分隔）",
        exclude_word="排除关键词（用逗号分隔）",
        exclude_op="排除的作者（选择成员）",
        exclude_tag1="选择要排除的第一个标签",
        exclude_tag2="选择要排除的第二个标签"
    )
    @app_commands.choices(order=[
        app_commands.Choice(name=option, value=option)
        for option in SEARCH_ORDER_OPTIONS
    ])
    async def forum_search(
        self,
        interaction: discord.Interaction,
        forum_name: str,
        order: str = "最高反应降序",
        original_poster: Optional[discord.User] = None,
        tag1: Optional[str] = None,
        tag2: Optional[str] = None,
        tag3: Optional[str] = None,
        search_word: Optional[str] = None,
        exclude_word: Optional[str] = None,
        exclude_op: Optional[discord.User] = None,
        exclude_tag1: Optional[str] = None,
        exclude_tag2: Optional[str] = None
    ):
        """搜索论坛帖子的命令实现"""
        try:
            self._logger.info(f"搜索命令被调用 - 用户: {interaction.user}")
            
            # 权限检查
            if not interaction.guild:
                await interaction.response.send_message(
                    embed=self.embed_builder.create_error_embed("命令错误", "该命令只能在服务器中使用"),
                    ephemeral=True
                )
                return

            permissions = interaction.channel.permissions_for(interaction.guild.me)
            if not (permissions.send_messages and permissions.embed_links):
                await interaction.response.send_message(
                    embed=self.embed_builder.create_error_embed("权限错误", "Bot缺少必要权限，需要：发送消息、嵌入链接权限"),
                    ephemeral=True
                )
                return

            # 延迟响应，给予更多处理时间
            await interaction.response.defer(ephemeral=True)
            
            # 获取论坛频道
            forum_channel = interaction.guild.get_channel(int(forum_name))
            if not isinstance(forum_channel, discord.ForumChannel):
                await interaction.followup.send(
                    embed=self.embed_builder.create_error_embed("搜索错误", "未找到指定的论坛分区"),
                    ephemeral=True
                )
                return

            # 预处理搜索条件
            search_conditions = {
                'search_tags': set(tag.lower() for tag in [tag1, tag2, tag3] if tag),
                'exclude_tags': set(tag.lower() for tag in [exclude_tag1, exclude_tag2] if tag),
                'search_keywords': [k.strip().lower() for k in search_word.split(",")] if search_word else [],
                'exclude_keywords': [k.strip().lower() for k in exclude_word.split(",")] if exclude_word else [],
                'original_poster': original_poster,
                'exclude_op': exclude_op
            }

            # 生成搜索条件摘要
            condition_summary = []
            if search_conditions['search_tags']:
                condition_summary.append(f"🏷️ 包含标签: {', '.join(search_conditions['search_tags'])}")
            if search_conditions['exclude_tags']:
                condition_summary.append(f"🚫 排除标签: {', '.join(search_conditions['exclude_tags'])}")
            if search_conditions['search_keywords']:
                condition_summary.append(f"🔍 关键词: {', '.join(search_conditions['search_keywords'])}")
            if search_conditions['exclude_keywords']:
                condition_summary.append(f"❌ 排除词: {', '.join(search_conditions['exclude_keywords'])}")
            if original_poster:
                condition_summary.append(f"👤 发帖人: {original_poster.display_name}")
            if exclude_op:
                condition_summary.append(f"🚷 排除发帖人: {exclude_op.display_name}")
            
            conditions_text = "\n".join(condition_summary) if condition_summary else "无特定搜索条件"

            # 发送初始进度消息
            progress_message = await interaction.followup.send(
                embed=self.embed_builder.create_info_embed(
                    "搜索进行中", 
                    f"📋 搜索条件:\n{conditions_text}\n\n💫 正在搜索活动帖子...",
                ),
                ephemeral=True
            )

            filtered_results = []
            processed_count = 0
            start_time = datetime.now()
            error_count = 0
            
            # 处理活动帖子
            active_threads = forum_channel.threads
            active_count = len(active_threads)
            
            if active_count > 0:
                try:
                    active_results = await self._process_thread_batch(active_threads, search_conditions)
                    if active_results:
                        filtered_results.extend(active_results)
                    processed_count += active_count
                    
                    # 更新进度
                    await progress_message.edit(
                        embed=self.embed_builder.create_info_embed(
                            "搜索进行中",
                            f"✓ 已处理活动帖子: {processed_count} 个\n"
                            f"📊 匹配结果: {len(filtered_results)} 个\n"
                            f"⏳ 正在搜索存档帖子..."
                        )
                    )
                except Exception as e:
                    error_count += 1
                    self._logger.error(f"处理活动帖子时出错: {e}")
                    await progress_message.edit(
                        embed=self.embed_builder.create_warning_embed(
                            "搜索进行中",
                            f"❌ 处理活动帖子时出现错误\n"
                            f"📊 当前结果: {len(filtered_results)} 个\n"
                            f"⏳ 继续搜索存档帖子..."
                        )
                    )

            # 分批获取并处理存档帖子
            last_thread = None
            batch_size = 100
            batch_count = 0
            while True:
                try:
                    archived_threads = []
                    async for thread in forum_channel.archived_threads(limit=batch_size, before=last_thread):
                        archived_threads.append(thread)
                        last_thread = thread
                    
                    if not archived_threads:
                        break
                    
                    batch_count += 1
                    # 处理这一批次的帖子
                    batch_results = await self._process_thread_batch(archived_threads, search_conditions)
                    if batch_results:
                        filtered_results.extend(batch_results)
                    
                    processed_count += len(archived_threads)
                    elapsed_time = (datetime.now() - start_time).total_seconds()
                    
                    # 每处理一批次更新进度
                    await progress_message.edit(
                        embed=self.embed_builder.create_info_embed(
                            "搜索进行中",
                            f"✓ 已处理: {processed_count} 个帖子\n"
                            f"📊 匹配结果: {len(filtered_results)} 个\n"
                            f"⏱️ 用时: {elapsed_time:.1f} 秒\n"
                            f"📦 已处理 {batch_count} 批存档帖子"
                        )
                    )
                    
                except Exception as e:
                    error_count += 1
                    self._logger.error(f"获取存档帖子时出错: {e}")
                    await progress_message.edit(
                        embed=self.embed_builder.create_warning_embed(
                            "搜索进行中",
                            f"❌ 处理第 {batch_count} 批存档帖子时出现错误\n"
                            f"✓ 已处理: {processed_count} 个帖子\n"
                            f"📊 当前结果: {len(filtered_results)} 个\n"
                            f"⏳ 尝试继续搜索..."
                        )
                    )
                    if error_count >= 3:  # 连续错误超过3次则中断
                        break

            # 计算总用时
            total_time = (datetime.now() - start_time).total_seconds()

            # 更新最终进度
            status_emoji = "✅" if error_count == 0 else "⚠️"
            final_status = "搜索完成" if error_count == 0 else f"搜索完成 (有 {error_count} 处错误)"
            
            await progress_message.edit(
                embed=self.embed_builder.create_info_embed(
                    final_status,
                    f"📋 搜索条件:\n{conditions_text}\n\n"
                    f"{status_emoji} 共处理 {processed_count} 个帖子\n"
                    f"📊 找到 {len(filtered_results)} 个匹配结果\n"
                    f"⏱️ 总用时: {total_time:.1f} 秒\n"
                    f"💫 正在生成结果页面..."
                )
            )

            # 优化排序逻辑
            sort_key = None
            reverse = True

            if order == "最高反应降序":
                sort_key = lambda x: x['stats']['reaction_count']
                reverse = True
            elif order == "最高反应升序":
                sort_key = lambda x: x['stats']['reaction_count']
                reverse = False
            elif order == "总回复数降序":
                sort_key = lambda x: x['stats']['reply_count']
                reverse = True
            elif order == "总回复数升序":
                sort_key = lambda x: x['stats']['reply_count']
                reverse = False
            elif order == "发帖时间由新到旧":
                sort_key = lambda x: x['thread'].created_at
                reverse = True
            elif order == "发帖时间由旧到新":
                sort_key = lambda x: x['thread'].created_at
                reverse = False
            elif order == "最后活跃由新到旧":
                sort_key = lambda x: x['thread'].last_message.created_at if x['thread'].last_message else x['thread'].created_at
                reverse = True
            elif order == "最后活跃由旧到新":
                sort_key = lambda x: x['thread'].last_message.created_at if x['thread'].last_message else x['thread'].created_at
                reverse = False
            else:  # 默认按发帖时间降序
                sort_key = lambda x: x['thread'].created_at
                reverse = True

            if sort_key:
                filtered_results.sort(key=sort_key, reverse=reverse)

            if not filtered_results:
                await interaction.followup.send(
                    embed=self.embed_builder.create_warning_embed("无搜索结果", "未找到符合条件的帖子"),
                    ephemeral=True
                )
                return

            # 创建分页显示
            async def generate_embeds(page_items, page_number):
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
                        summary = truncate_text(first_message.content.strip(), 1000)
                        embed.description = f"**帖子摘要:**\n{summary}"

                        thumbnail_url = self.attachment_processor.get_first_image(first_message)
                        if thumbnail_url:
                            embed.set_thumbnail(url=thumbnail_url)

                    if thread.applied_tags:
                        tag_names = [tag.name for tag in thread.applied_tags]
                        embed.add_field(name="标签", value=", ".join(tag_names), inline=True)

                    # 添加统计信息
                    reaction_count = stats.get('reaction_count', 0) or 0
                    reply_count = stats.get('reply_count', 0) or 0
                    embed.add_field(
                        name="统计", 
                        value=f"👍 {reaction_count} | 💬 {reply_count}", 
                        inline=True
                    )

                    # 添加时间信息
                    embed.add_field(
                        name="时间",
                        value=f"创建: {discord.utils.format_dt(thread.created_at, 'R')}\n"
                              f"最后活跃: {discord.utils.format_dt(thread.last_message.created_at if thread.last_message else thread.created_at, 'R')}",
                        inline=True
                    )

                    # 添加页码信息
                    total_items = len(filtered_results)
                    start_idx = page_number * MESSAGES_PER_PAGE + 1
                    end_idx = min((page_number + 1) * MESSAGES_PER_PAGE, total_items)
                    embed.set_footer(text=f"第 {start_idx}-{end_idx} 个结果，共 {total_items} 个")

                    embeds.append(embed)
                return embeds

            # 使用分页器
            paginator = MultiEmbedPaginationView(
                items=filtered_results,
                items_per_page=MESSAGES_PER_PAGE,
                generate_embeds=generate_embeds
            )

            # 创建并发送初始 embeds
            initial_page_items = paginator.get_page_items(0)
            if initial_page_items:
                initial_embeds = await generate_embeds(initial_page_items, 0)
                if initial_embeds:
                    await paginator.start(interaction, initial_embeds)
                else:
                    await interaction.followup.send(
                        embed=self.embed_builder.create_error_embed("错误", "无法生成搜索结果页面"),
                        ephemeral=True
                    )
            else:
                await interaction.followup.send(
                    embed=self.embed_builder.create_warning_embed("无搜索结果", "未找到符合条件的帖子"),
                    ephemeral=True
                )

        except Exception as e:
            self._logger.error(f"搜索命令执行出错: {str(e)}", exc_info=True)
            await interaction.followup.send(
                embed=self.embed_builder.create_error_embed("搜索错误", "搜索过程中出现错误，请稍后重试"),
                ephemeral=True
            )

    @forum_search.autocomplete('forum_name')
    async def forum_name_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> List[app_commands.Choice[str]]:
        """论坛名称自动补全功能"""
        try:
            if not interaction.guild:
                return []
            
            choices = []
            # 获取所有论坛频道
            for channel in interaction.guild.channels:
                if isinstance(channel, discord.ForumChannel):
                    # 如果当前输入为空或者是频道名称的子串
                    if not current or current.lower() in channel.name.lower():
                        # 确保频道名称和ID都是有效的
                        if channel.name and channel.id:
                            choices.append(
                                app_commands.Choice(
                                    name=f"#{channel.name}", # 添加 # 前缀使其更像频道
                                    value=str(channel.id)
                                )
                            )
            
            # 按照频道名称排序
            choices.sort(key=lambda x: x.name)
            
            # Discord限制最多25个选项
            return choices[:25]
            
        except Exception as e:
            self._logger.error(f"论坛名称自动补全出错: {str(e)}", exc_info=True)
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
        """标签自动补全功能"""
        try:
            if not interaction.guild:
                return []

            # 获取已选择的论坛ID
            forum_name = None
            for option in interaction.data.get("options", []):
                if option["name"] == "forum_name":
                    forum_name = option["value"]
                    break

            if not forum_name:
                return []

            # 获取论坛频道
            forum_channel = interaction.guild.get_channel(int(forum_name))
            if not isinstance(forum_channel, discord.ForumChannel):
                return []

            # 获取所有可用标签
            available_tags = forum_channel.available_tags
            
            # 获取当前命令中已选择的标签
            selected_tags = set()
            for option in interaction.data.get("options", []):
                if option["name"].startswith("tag") and option.get("value"):
                    selected_tags.add(option["value"])
                if option["name"].startswith("exclude_tag") and option.get("value"):
                    selected_tags.add(option["value"])

            # 过滤标签
            filtered_tags = []
            for tag in available_tags:
                if tag.name in selected_tags:
                    continue
                    
                if current and current.lower() not in tag.name.lower():
                    continue
                    
                if not tag.moderated or interaction.user.guild_permissions.manage_threads:
                    filtered_tags.append(tag)

            # 按名称排序并限制数量
            filtered_tags.sort(key=lambda x: x.name.lower())
            choices = [
                app_commands.Choice(name=tag.name, value=tag.name)
                for tag in filtered_tags[:25]
            ]
            
            return choices
            
        except Exception as e:
            self._logger.error(f"标签自动补全出错: {str(e)}", exc_info=True)
            return []

async def setup(bot: commands.Bot):
    await bot.add_cog(Search(bot))
