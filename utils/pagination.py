import discord
from discord.ui import View, button
from typing import List, Callable, Any, Union, Optional
import asyncio
import logging

logger = logging.getLogger('discord_bot.pagination')

class PageSelectModal(discord.ui.Modal, title="跳转到指定页数"):
    def __init__(self, max_pages: int):
        super().__init__()
        self.max_pages = max_pages
        self.page_number = discord.ui.TextInput(
            label=f'请输入页数 (1-{max_pages})',
            placeholder='输入一个数字...',
            min_length=1,
            max_length=len(str(max_pages)),
            required=True
        )
        self.add_item(self.page_number)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            page = int(self.page_number.value)
            if 1 <= page <= self.max_pages:
                self.result = page - 1  # 转换为0基索引
                await interaction.response.defer()
            else:
                await interaction.response.send_message(
                    f"请输入有效的页数 (1-{self.max_pages})",
                    ephemeral=True
                )
                self.result = None
        except ValueError:
            await interaction.response.send_message(
                "请输入有效的数字",
                ephemeral=True
            )
            self.result = None

class MultiEmbedPaginationView(View):
    def __init__(
        self, 
        items: List[Any], 
        items_per_page: int, 
        generate_embeds: Callable[[List[Any], int], Union[discord.Embed, List[discord.Embed]]], 
        timeout: Optional[float] = 900.0  # 15分钟默认超时
    ):
        super().__init__(timeout=timeout)
        self.items = items
        self.items_per_page = items_per_page
        self.generate_embeds = generate_embeds
        self.current_page = 0
        self.total_items = len(items)
        self.total_pages = max((self.total_items + items_per_page - 1) // items_per_page, 1)
        self._logger = logger
        self._logger.info(f"初始化分页器: 总项目={self.total_items}, 每页项目={items_per_page}, 总页数={self.total_pages}")
        self.message = None  # 存储消息引用
        self.last_interaction_time = None
        self.original_user = None

    def get_page_items(self, page: int) -> List[Any]:
        """获取指定页面的项目"""
        if not self.items:
            self._logger.warning("没有可显示的项目")
            return []

        if page < 0 or page >= self.total_pages:
            self._logger.warning(f"无效的页面请求: page={page}, total_pages={self.total_pages}")
            return []

        start_idx = page * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, self.total_items)
        
        items = self.items[start_idx:end_idx]
        self._logger.debug(f"获取页面项目: page={page + 1}, start={start_idx}, end={end_idx}, count={len(items)}")
        return items

    def update_button_states(self):
        """更新按钮状态"""
        self.first_button.disabled = self.current_page <= 0
        self.prev_button.disabled = self.current_page <= 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1
        self.last_button.disabled = self.current_page >= self.total_pages - 1
        
        self._logger.debug(
            f"按钮状态更新: first={self.first_button.disabled}, "
            f"prev={self.prev_button.disabled}, "
            f"next={self.next_button.disabled}, "
            f"last={self.last_button.disabled}"
        )

    async def check_permissions(self, interaction: discord.Interaction) -> bool:
        """检查Bot是否有必要的权限"""
        if not interaction.guild:
            self._logger.warning("无法在私信中使用此功能")
            return False

        permissions = interaction.channel.permissions_for(interaction.guild.me)
        required_permissions = {
            "view_channel": "查看频道",
            "send_messages": "发送消息",
            "embed_links": "嵌入链接",
            "read_message_history": "读取消息历史",
            "add_reactions": "添加反应"
        }

        missing_permissions = []
        for perm, name in required_permissions.items():
            if not getattr(permissions, perm):
                missing_permissions.append(name)

        if missing_permissions:
            self._logger.error(f"缺少权限: {', '.join(missing_permissions)}")
            try:
                await interaction.response.send_message(
                    f"Bot缺少必要权限: {', '.join(missing_permissions)}",
                    ephemeral=True
                )
            except Exception as e:
                self._logger.error(f"发送权限错误消息失败: {e}")
            return False

        return True

    async def safe_defer(self, interaction: discord.Interaction) -> bool:
        """安全地延迟响应交互"""
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
            return True
        except Exception as e:
            self._logger.error(f"延迟响应失败: {e}")
            return False

    async def update_message(self, interaction: discord.Interaction) -> bool:
        """更新消息内容"""
        try:
            # 检查权限
            if not await self.check_permissions(interaction):
                return False

            # 确保当前页面在有效范围内
            if self.current_page >= self.total_pages:
                self.current_page = max(0, self.total_pages - 1)
                self._logger.warning(f"页面超出范围，调整为: {self.current_page + 1}")

            # 获取当前页面的项目
            page_items = self.get_page_items(self.current_page)
            if not page_items and self.current_page > 0:
                self._logger.warning(f"当前页面 {self.current_page + 1} 没有项目，尝试返回第一页")
                self.current_page = 0
                page_items = self.get_page_items(self.current_page)

            if not page_items:
                self._logger.error("无法获取有效的页面项目")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "无法显示此页面的内容，请重试",
                        ephemeral=True
                    )
                return False

            # 生成新的 embeds
            try:
                embeds = await self.generate_embeds(page_items, self.current_page)
                if not isinstance(embeds, list):
                    embeds = [embeds]
            except Exception as e:
                self._logger.error(f"生成 embeds 失败: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "生成页面内容时出错，请重试",
                        ephemeral=True
                    )
                return False

            if not embeds:
                self._logger.error("生成的 embeds 为空")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "无法生成页面内容，请重试",
                        ephemeral=True
                    )
                return False

            # 更新按钮状态
            self.update_button_states()

            # 更新消息
            try:
                if interaction.response.is_done():
                    await interaction.message.edit(embeds=embeds, view=self)
                else:
                    await interaction.response.edit_message(embeds=embeds, view=self)
                self.last_interaction_time = discord.utils.utcnow()
                return True
            except discord.errors.NotFound:
                self._logger.error("消息不存在或已被删除")
                return False
            except discord.errors.Forbidden as e:
                self._logger.error(f"没有权限编辑消息: {e}")
                return False

        except Exception as e:
            self._logger.error(f"更新消息失败: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "更新页面时发生错误，请重试",
                    ephemeral=True
                )
            return False

    async def handle_button_interaction(self, interaction: discord.Interaction, action: str) -> None:
        """统一处理按钮交互"""
        try:
            if not await self.check_permissions(interaction):
                return

            self._logger.debug(f"处理按钮交互: {action}")
            # 更新最后交互时间
            self.last_interaction_time = discord.utils.utcnow()
            await self.update_message(interaction)
        except Exception as e:
            self._logger.error(f"处理按钮 {action} 时出错: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"处理 {action} 按钮时出现错误，请重试",
                    ephemeral=True
                )

    @button(emoji="⏮️", style=discord.ButtonStyle.blurple, custom_id="pagination:first")
    async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """跳转到第一页"""
        if self.current_page != 0:
            self._logger.debug("跳转到第一页")
            self.current_page = 0
            await self.handle_button_interaction(interaction, "首页")
        else:
            await self.safe_defer(interaction)

    @button(emoji="◀️", style=discord.ButtonStyle.blurple, custom_id="pagination:prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """上一页"""
        if self.current_page > 0:
            self._logger.debug(f"上一页: {self.current_page + 1} -> {self.current_page}")
            self.current_page -= 1
            await self.handle_button_interaction(interaction, "上一页")
        else:
            await self.safe_defer(interaction)

    @button(emoji="🔢", style=discord.ButtonStyle.grey, custom_id="pagination:page")
    async def page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """页面选择"""
        try:
            if not await self.check_permissions(interaction):
                return

            modal = PageSelectModal(self.total_pages)
            await interaction.response.send_modal(modal)
            await modal.wait()
            
            if hasattr(modal, 'result') and modal.result is not None:
                self._logger.debug(f"跳转到指定页面: {modal.result + 1}")
                self.current_page = modal.result
                await self.update_message(interaction)
        except Exception as e:
            self._logger.error(f"处理页面选择时出错: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "处理页面选择时出现错误，请重试",
                    ephemeral=True
                )

    @button(emoji="▶️", style=discord.ButtonStyle.blurple, custom_id="pagination:next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """下一页"""
        if self.current_page < self.total_pages - 1:
            self._logger.debug(f"下一页: {self.current_page + 1} -> {self.current_page + 2}")
            self.current_page += 1
            await self.handle_button_interaction(interaction, "下一页")
        else:
            await self.safe_defer(interaction)

    @button(emoji="⏭️", style=discord.ButtonStyle.blurple, custom_id="pagination:last")
    async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """跳转到最后一页"""
        if self.current_page != self.total_pages - 1:
            self._logger.debug(f"跳转到最后一页: {self.current_page + 1} -> {self.total_pages}")
            self.current_page = self.total_pages - 1
            await self.handle_button_interaction(interaction, "末页")
        else:
            await self.safe_defer(interaction)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """确保只有原始用户可以使用按钮"""
        try:
            # 如果是第一次交互，存储原始用户
            if self.original_user is None:
                self.original_user = interaction.user
                return True

            # 检查是否是原始用户
            if interaction.user.id == self.original_user.id:
                return True
                
            # 如果不是原始用户，发送提示消息并返回False
            await interaction.response.send_message(
                "只有使用搜索命令的用户才能操作这些按钮",
                ephemeral=True
            )
            self._logger.warning(f"用户 {interaction.user.id} 尝试使用非其创建的分页器")
            return False
            
        except Exception as e:
            self._logger.error(f"检查交互权限时出错: {e}")
            return False

    async def on_timeout(self):
        """
        处理视图超时
        - 当视图超时时（无人交互超过timeout时间）
        - 当bot重启或断开连接时
        都会触发此方法，直接删除分页消息
        """
        try:
            self._logger.info("分页视图超时，准备清理消息")
            
            # 如果消息引用存在，尝试删除消息
            if self.message:
                try:
                    await self.message.delete()
                    self._logger.info("成功删除超时的分页消息")
                except discord.NotFound:
                    # 消息可能已经被删除
                    self._logger.info("分页消息已不存在")
                except discord.Forbidden:
                    # 没有删除消息的权限
                    self._logger.warning("没有权限删除分页消息")
                except Exception as e:
                    self._logger.error(f"删除分页消息时发生未知错误: {e}")
            else:
                self._logger.warning("分页消息引用不存在，无法删除")
                
        except Exception as e:
            self._logger.error(f"处理分页超时时出错: {e}", exc_info=True)

    async def start(self, interaction: discord.Interaction, initial_embeds: Union[discord.Embed, List[discord.Embed]]):
        """开始分页显示"""
        try:
            if not await self.check_permissions(interaction):
                return

            # 存储原始用户
            self.original_user = interaction.user
            self._logger.debug(f"存储原始用户ID: {self.original_user.id}")

            if not isinstance(initial_embeds, list):
                initial_embeds = [initial_embeds]

            if not initial_embeds:
                self._logger.error("初始 embeds 为空")
                await interaction.followup.send(
                    "无法显示搜索结果，请重试",
                    ephemeral=True
                )
                return

            # 更新按钮状态
            self.update_button_states()
            
            self._logger.info(f"开始分页显示: 总页数={self.total_pages}, 当前页={self.current_page + 1}")
            
            # 发送初始消息并保存引用
            self.message = await interaction.followup.send(
                embeds=initial_embeds, 
                view=self, 
                ephemeral=True
            )
            self.last_interaction_time = discord.utils.utcnow()

        except Exception as e:
            self._logger.error(f"启动分页显示时出错: {e}", exc_info=True)
            await interaction.followup.send(
                "启动分页显示时出现错误，请重试",
                ephemeral=True
            )