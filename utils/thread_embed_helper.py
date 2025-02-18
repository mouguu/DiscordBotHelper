import os
import discord

def create_thread_embed(thread: dict, index: int) -> discord.Embed:
    """
    Generate an enhanced embed for a forum thread with a card-like layout.

    Expected thread dictionary keys:
      - title: str -- The thread title.
      - author: str -- The thread author's username.
      - author_id: Optional[int] -- The thread author's Discord user ID for a proper mention.
      - content: str -- The full thread content (to be truncated for preview).
      - attachment: Optional[str] -- URL or file path for an image (used as thumbnail).
      - highest_replies: int -- Highest number of replies.
      - replies: int -- Total number of replies.
      - date: str -- Formatted date string of thread creation.
      - thread_url: Optional[str] -- URL to view the full thread.
    """
    preview_length = 100
    content_text = thread.get("content", "")
    preview = content_text if len(content_text) <= preview_length else content_text[:preview_length] + "..."
    
    # Use proper mention if author_id is provided.
    author = thread.get("author", "Unknown")
    if "author_id" in thread:
        author_mention = f"<@{thread['author_id']}>"
    else:
        author_mention = author  # Remove @ symbol, just use the plain author name
    
    highest_replies = thread.get("highest_replies", 0)
    replies = thread.get("replies", 0)
    date = thread.get("date", "Unknown")
    title = thread.get("title", "No Title")
    thread_url = thread.get("thread_url")
    attachment = thread.get("attachment")
    
    # Create the embed with a clickable title if thread_url exists.
    embed = discord.Embed(
        title=f"Thread {index} - {title}",
        color=0x00aaff,
        url=thread_url if thread_url else None
    )
    
    # Add individual fields for a card-like layout.
    embed.add_field(name="作者", value=author_mention, inline=True)
    embed.add_field(name="回复", value=f"最高回复数: {highest_replies} | 回复数: {replies}", inline=True)
    embed.add_field(name="发布时间", value=date, inline=True)
    
    if attachment:
        filename = os.path.basename(attachment)
        embed.add_field(name="附件", value=f"[缩略图: `{filename}`]", inline=False)
    
    embed.add_field(name="预览", value=f"*{preview}*", inline=False)
    
    if thread_url:
        embed.add_field(name="链接", value=f"[点击查看帖子]({thread_url})", inline=False)
    
    # Set thumbnail if an attachment image exists.
    if attachment:
        embed.set_thumbnail(url=attachment)
    
    return embed
