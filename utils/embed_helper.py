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
        self.ERROR_COLOR = 0xe74c3c    # Red
        self.SUCCESS_COLOR = 0x2ecc71   # Green
        self.WARNING_COLOR = 0xf1c40f   # Yellow
        self.INFO_COLOR = color         # Default blue

    def format_timestamp(self, dt: datetime, include_time: bool = True) -> str:
        """Format timestamp"""
        try:
            if include_time:
                return dt.strftime('%Y-%m-%d %H:%M')
            return dt.strftime('%Y-%m-%d')
        except Exception as e:
            logger.error(f"Error formatting timestamp: {str(e)}")
            return "Unknown time"

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
        """Create embed for a thread"""
        try:
            # Create base embed
            embed = discord.Embed(
                title=title[:256],
                url=jump_url,
                color=self.color,
                timestamp=datetime.utcnow()
            )

            # Set author info
            if author:
                embed.set_author(
                    name=author.display_name,
                    icon_url=author.display_avatar.url if hasattr(author, 'display_avatar') else None
                )

            # Create description content
            description_parts = []

            # Add basic info
            if not compact:
                description_parts.extend([
                    f"📅 **Published Time:** {created_at.strftime('%Y-%m-%d %H:%M')}",
                    f"🕒 **Last Active:** {last_active.strftime('%Y-%m-%d %H:%M')}",
                    f"👍 **Reactions:** {reactions_count}",
                    f"🏷️ **Tags:** {', '.join(tags) if tags else 'No tags'}",
                    "",
                    "💬 **Content:**",
                    summary[:1000] if summary else "No content"
                ])
            else:
                description_parts.extend([
                    f"⏰ {created_at.strftime('%Y-%m-%d %H:%M')} | 👍 {reactions_count}",
                    f"🏷️ {', '.join(tags) if tags else 'No tags'}"
                ])

            embed.description = "\n".join(description_parts)

            # Add jump link
            if not compact:
                embed.add_field(
                    name="Jump",
                    value=f"[Click to view original post]({jump_url})",
                    inline=False
                )

            # Set thumbnail
            if thumbnail_url:
                embed.set_thumbnail(url=thumbnail_url)

            # Set page info
            if page_info and len(page_info) == 2:
                current_page, total_pages = page_info
                embed.set_footer(text=f"Page {current_page}/{total_pages}")

            return embed

        except Exception as e:
            logger.error(f"Error creating thread embed: {str(e)}")
            return None

    def create_error_embed(self, title: str, description: str, show_timestamp: bool = True) -> discord.Embed:
        """Create Embed for error message"""
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
            logger.error(f"Error creating error embed: {str(e)}")
            return discord.Embed(
                title="❌ Error",
                description="An unknown error occurred",
                color=self.ERROR_COLOR
            )

    def create_success_embed(self, title: str, description: str, show_timestamp: bool = True) -> discord.Embed:
        """Create Embed for success message"""
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
            logger.error(f"Error creating success embed: {str(e)}")
            return self.create_error_embed("Error", "Could not create success message")

    def create_warning_embed(self, title: str, description: str, show_timestamp: bool = True) -> discord.Embed:
        """Create Embed for warning message"""
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
            logger.error(f"Error creating warning embed: {str(e)}")
            return self.create_error_embed("Error", "Could not create warning message")

    def create_info_embed(self, title: str, description: str, show_timestamp: bool = True) -> discord.Embed:
        """Create Embed for info message"""
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
            logger.error(f"Error creating info embed: {str(e)}")
            return self.create_error_embed("Error", "Could not create info message")

    def add_field_if_exists(
        self,
        embed: discord.Embed,
        name: str,
        value: Optional[Union[str, int, float]],
        inline: bool = True
    ) -> None:
        """Add field if value exists"""
        if value is not None and str(value).strip():
            try:
                embed.add_field(
                    name=name[:256],
                    value=str(value)[:1024],
                    inline=inline
                )
            except Exception as e:
                logger.error(f"Error adding field: {str(e)}")

    def add_message_attachments(self, embed: discord.Embed, message: discord.Message) -> None:
        """Add attachments from message to embed"""
        try:
            # Get and validate image URLs
            thumbnail_url = self.attachment_processor.get_first_image(message)
            all_images = self.attachment_processor.get_all_images(message)
            
            # Add thumbnail (if valid)
            if thumbnail_url:
                try:
                    embed.set_thumbnail(url=thumbnail_url)
                except discord.errors.InvalidArgument as e:
                    logger.warning(f"Could not set thumbnail, URL invalid: {thumbnail_url}, Error: {e}")
            
            # Add all image links (if multiple)
            if len(all_images) > 1:
                try:
                    # Create safe link text for each image
                    image_links = []
                    for i, url in enumerate(all_images):
                        # Limit URL length to prevent overly long links
                        display_url = url[:100] + "..." if len(url) > 100 else url
                        image_links.append(f"[Image {i+1}]({url})")
                    
                    # Group links to prevent exceeding Discord field value limit (1024 chars)
                    links_text = "\n".join(image_links)
                    if len(links_text) > 1024:
                        # If limit exceeded, show only the first few links
                        truncated_links = image_links[:5]
                        links_text = "\n".join(truncated_links) + "\n*(More images not shown)*"
                    
                    embed.add_field(name="Attachment Images", value=links_text, inline=False)
                except discord.errors.InvalidArgument as e:
                    logger.warning(f"Error adding image links field: {e}")
                
        except Exception as e:
            logger.error(f"Error adding message attachments: {str(e)}")