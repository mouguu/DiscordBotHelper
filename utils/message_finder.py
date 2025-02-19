import discord

async def find_first_message(channel):
    """
    查找频道或线程中的第一条消息。
    参数:
        channel (Union[discord.TextChannel, discord.Thread]): 要搜索的频道。
    返回:
        discord.Message 或 None: 找到的第一条消息，如果未找到则返回 None。
    """
    if isinstance(channel, discord.Thread):
        # 对于线程，直接获取线程中的第一条消息
        async for message in channel.history(limit=1, oldest_first=True):
            return message
    else:
        # 对于普通频道，获取历史消息中的第一条
        async for message in channel.history(limit=100, oldest_first=True):
            if not message.reference:
                return message
    return None
