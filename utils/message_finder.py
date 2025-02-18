
import discord

async def find_first_message(channel):
    """
    查找频道或线程中的第一条消息，包括论坛的标题帖子。
    参数:
        channel (Union[discord.TextChannel, discord.ForumChannel, discord.Thread]): 要搜索的频道。
    返回:
        discord.Message 或 None: 找到的第一条消息，如果未找到则返回 None。
    """
    if isinstance(channel, discord.Thread):
        # 对于线程，直接获取线程中的第一条消息
        async for message in channel.history(limit=1, oldest_first=True):
            return message
    elif isinstance(channel, discord.ForumChannel):
        # 对于论坛频道，获取所有活跃线程并找到最早的那个
        threads = sorted(channel.threads, key=lambda t: t.created_at)
        if threads:
            first_thread = threads[0]
            async for message in first_thread.history(limit=1, oldest_first=True):
                return message
    else:
        # 对于普通频道，获取历史消息中的第一条
        async for message in channel.history(limit=100, oldest_first=True):
            if not message.reference:
                return message
    return None
