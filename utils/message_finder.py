import discord

async def find_first_message(channel):
    """
    Find the first message in a channel or thread.
    Args:
        channel (Union[discord.TextChannel, discord.Thread]): The channel to search.
    Returns:
        discord.Message or None: The first message found, or None if not found.
    """
    if isinstance(channel, discord.Thread):
        # For threads, directly fetch the first message in the thread
        async for message in channel.history(limit=1, oldest_first=True):
            return message
    else:
        # For regular channels, get the first message from history
        async for message in channel.history(limit=100, oldest_first=True):
            if not message.reference:
                return message
    return None
