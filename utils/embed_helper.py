import discord
from typing import Optional, List, Union
import logging
from datetime import datetime
from .attachment_helper import AttachmentProcessor

logger = logging.getLogger('discord_bot.embed')

class DiscordEmbedBuilder:
    def __init__(self, color: int = 0x3498db):
        self.color = color
        self.attachment_processor = AttachmentProcessor()
        self.ERROR_COLOR = 0xe74c3c    # 红色
        self.SUCCESS_COLOR = 0x2ecc71   # 绿色
        self.WARNING_COLOR = 0xf1c40f   # 黄色
        self.INFO_COLOR = color         # 默认蓝色

    def format_timestamp(self, dt: datetime, include_time: bool = True) -> str:
        """格式化时间戳"""
        try:
            if include_time:
                return dt.strftime('%Y-%m-%d %H:%M')
            return dt.strftime('%Y-%m-%d')
        except Exception as e:
            logger.error(f"格式化时间戳出错: {str(e)}")
            return "未知时间"

    def create_thread_embed(
        self,
        title: str,
        author: Optional[discord.Member],
        created_at: datetime,
        last_active: datetime,
        reactions_count: int,
        tags: List[str],
        summary: str,
        jump_url: str,
        thumbnail_url: Optional[str] = None,
        page_info: Optional[tuple] = None,
        compact: bool = False
    ) -> Optional[discord.Embed]:
        """创建帖子的Embed"""
        try:
            # 创建基础embed
            embed = discord.Embed(
                title=title[:256],
                url=jump_url,
                color=self.color,
                timestamp=datetime.utcnow()
            )

            # 设置作者信息
            if author:
                embed.set_author(
                    name=author.display_name,
                    icon_url=author.display_avatar.url if hasattr(author, 'display_avatar') else None
                )

            # 创建描述内容
            description_parts = []

            # 添加基本信息
            if not compact:
                description_parts.extend([
                    f"📅 **发布时间：** {created_at.strftime('%Y-%m-%d %H:%M')}",
                    f"🕒 **最后活动：** {last_active.strftime('%Y-%m-%d %H:%M')}",
                    f"👍 **反应数：** {reactions_count}",
                    f"🏷️ **标签：** {', '.join(tags) if tags else '无标签'}",
                    "",
                    "💬 **内容：**",
                    summary[:1000] if summary else "无内容"
                ])
            else:
                description_parts.extend([
                    f"⏰ {created_at.strftime('%Y-%m-%d %H:%M')} | 👍 {reactions_count}",
                    f"🏷️ {', '.join(tags) if tags else '无标签'}"
                ])

            embed.description = "\n".join(description_parts)

            # 添加跳转链接
            if not compact:
                embed.add_field(
                    name="跳转",
                    value=f"[点击查看原帖]({jump_url})",
                    inline=False
                )

            # 设置缩略图
            if thumbnail_url:
                embed.set_thumbnail(url=thumbnail_url)

            # 设置页码信息
            if page_info and len(page_info) == 2:
                current_page, total_pages = page_info
                embed.set_footer(text=f"第 {current_page}/{total_pages} 页")

            return embed

        except Exception as e:
            logger.error(f"创建帖子embed时出错: {str(e)}")
            return None

    def create_error_embed(self, title: str, description: str, show_timestamp: bool = True) -> discord.Embed:
        """创建错误提示的Embed"""
        try:
            embed = discord.Embed(
                title=f"❌ {title[:256]}",
                description=description[:4096],
                color=self.ERROR_COLOR
            )
            if show_timestamp:
                embed.timestamp = datetime.utcnow()
            return embed
        except Exception as e:
            logger.error(f"创建错误embed时出错: {str(e)}")
            return discord.Embed(
                title="❌ 错误",
                description="发生未知错误",
                color=self.ERROR_COLOR
            )

    def create_success_embed(self, title: str, description: str, show_timestamp: bool = True) -> discord.Embed:
        """创建成功提示的Embed"""
        try:
            embed = discord.Embed(
                title=f"✅ {title[:256]}",
                description=description[:4096],
                color=self.SUCCESS_COLOR
            )
            if show_timestamp:
                embed.timestamp = datetime.utcnow()
            return embed
        except Exception as e:
            logger.error(f"创建成功embed时出错: {str(e)}")
            return self.create_error_embed("错误", "无法创建成功提示")

    def create_warning_embed(self, title: str, description: str, show_timestamp: bool = True) -> discord.Embed:
        """创建警告提示的Embed"""
        try:
            embed = discord.Embed(
                title=f"⚠️ {title[:256]}",
                description=description[:4096],
                color=self.WARNING_COLOR
            )
            if show_timestamp:
                embed.timestamp = datetime.utcnow()
            return embed
        except Exception as e:
            logger.error(f"创建警告embed时出错: {str(e)}")
            return self.create_error_embed("错误", "无法创建警告提示")

    def create_info_embed(self, title: str, description: str, show_timestamp: bool = True) -> discord.Embed:
        """创建信息提示的Embed"""
        try:
            embed = discord.Embed(
                title=f"ℹ️ {title[:256]}",
                description=description[:4096],
                color=self.INFO_COLOR
            )
            if show_timestamp:
                embed.timestamp = datetime.utcnow()
            return embed
        except Exception as e:
            logger.error(f"创建信息embed时出错: {str(e)}")
            return self.create_error_embed("错误", "无法创建信息提示")

    def add_field_if_exists(
        self,
        embed: discord.Embed,
        name: str,
        value: Optional[Union[str, int, float]],
        inline: bool = True
    ) -> None:
        """如果值存在则添加字段"""
        if value is not None and str(value).strip():
            try:
                embed.add_field(
                    name=name[:256],
                    value=str(value)[:1024],
                    inline=inline
                )
            except Exception as e:
                logger.error(f"添加字段时出错: {str(e)}")

    def add_message_attachments(self, embed: discord.Embed, message: discord.Message) -> None:
        """添加消息中的附件到 embed"""
        try:
            # 获取并验证图片URL
            thumbnail_url = self.attachment_processor.get_first_image(message)
            all_images = self.attachment_processor.get_all_images(message)
            
            # 添加缩略图（如果有效）
            if thumbnail_url:
                try:
                    embed.set_thumbnail(url=thumbnail_url)
                except discord.errors.InvalidArgument as e:
                    logger.warning(f"无法设置缩略图，URL无效: {thumbnail_url}, 错误: {e}")
            
            # 添加所有图片链接（如果有多个）
            if len(all_images) > 1:
                try:
                    # 为每个图片创建安全的链接文本
                    image_links = []
                    for i, url in enumerate(all_images):
                        # 限制URL长度以防止过长的链接
                        display_url = url[:100] + "..." if len(url) > 100 else url
                        image_links.append(f"[图片 {i+1}]({url})")
                    
                    # 将链接分组以防止超过Discord的字段值限制（1024字符）
                    links_text = "\n".join(image_links)
                    if len(links_text) > 1024:
                        # 如果超过限制，只显示前几个链接
                        truncated_links = image_links[:5]
                        links_text = "\n".join(truncated_links) + "\n*(更多图片未显示)*"
                    
                    embed.add_field(name="附件图片", value=links_text, inline=False)
                except discord.errors.InvalidArgument as e:
                    logger.warning(f"添加图片链接字段时出错: {e}")
                
        except Exception as e:
            logger.error(f"添加消息附件时出错: {str(e)}")