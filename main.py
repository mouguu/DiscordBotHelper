import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import logging
import asyncio
from config.config import COMMAND_PREFIX, LOG_LEVEL
import re
import pytz
from utils.pagination import MultiEmbedPaginationView
from typing import List, Dict, Set
import signal

# Setup logging with more detailed formatting
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('discord_bot')

# Set Discord logger to WARNING to reduce noise
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('discord.http').setLevel(logging.WARNING)

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Add debug logging for token validation (only showing first 10 chars)
if TOKEN:
    logger.info(f"Token loaded successfully (starts with: {TOKEN[:10]}...)")
    # Validate token format
    token_parts = TOKEN.split('.')
    if len(token_parts) != 3:
        logger.error("Invalid token format - should have 3 parts separated by dots")
        raise ValueError("Invalid token format")
else:
    logger.error("No Discord token found in environment variables!")
    raise ValueError("Discord token is required")

class QianBot(commands.Bot):
    def __init__(self):
        # 设置所有必要的intents
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.guild_messages = True
        intents.members = True

        super().__init__(
            command_prefix=COMMAND_PREFIX,
            intents=intents,
            guild_ready_timeout=10
        )
        
        # 初始化缓存和状态
        self.initial_extensions: List[str] = [
            'cogs.search',
            'cogs.top_message'  # 保留这个，因为我们还需要这个cog
        ]
        self._ready = asyncio.Event()
        self.persistent_views_added = False
        self._guild_settings: Dict[int, Dict] = {}
        self._cached_commands: Set[str] = set()
        self._startup_time = None
        logger.info("Bot initialized with required intents")

    async def setup_hook(self):
        """A coroutine to be called to setup the bot"""
        try:
            start_time = asyncio.get_event_loop().time()
            
            # 并发加载所有扩展
            load_extension_tasks = [
                self.load_extension(extension) for extension in self.initial_extensions
            ]
            await asyncio.gather(*load_extension_tasks)
            logger.info(f"Loaded {len(self.initial_extensions)} extensions")

            # 同步命令
            logger.info("Starting command sync with Discord...")
            try:
                synced_commands = await self.tree.sync()
                self._cached_commands = {cmd.name for cmd in synced_commands}
                logger.info(f"Successfully synced {len(synced_commands)} commands")
            except Exception as e:
                logger.error(f"Failed to sync commands: {e}", exc_info=True)
                raise

            # 添加持久化视图
            if not self.persistent_views_added:
                # 创建空的持久化视图实例
                pagination_view = MultiEmbedPaginationView([], 5, lambda x, y: [], timeout=None)
                self.add_view(pagination_view)
                self.persistent_views_added = True
                logger.info("Added persistent pagination view")

            # 记录启动时间
            self._startup_time = asyncio.get_event_loop().time() - start_time
            logger.info(f"Bot setup completed in {self._startup_time:.2f} seconds")

        except Exception as e:
            logger.error(f"Setup hook failed: {e}", exc_info=True)
            raise

    async def on_ready(self):
        """When the bot is ready, this runs."""
        if self._ready.is_set():
            return

        self._ready.set()
        
        # 收集并记录服务器信息
        guild_info = []
        for guild in self.guilds:
            bot_member = guild.get_member(self.user.id)
            permissions = []
            if bot_member:
                perms = bot_member.guild_permissions
                if perms.administrator:
                    permissions.append("管理员")
                else:
                    if perms.send_messages: permissions.append("发送消息")
                    if perms.embed_links: permissions.append("嵌入链接")
                    if perms.add_reactions: permissions.append("添加反应")
                    if perms.read_messages: permissions.append("读取消息")
                    if perms.view_channel: permissions.append("查看频道")
            
            guild_info.append({
                'name': guild.name,
                'id': guild.id,
                'permissions': permissions
            })

        # 记录启动信息
        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')
        logger.info(f'Connected to {len(guild_info)} guilds:')
        for guild in guild_info:
            logger.info(f"- {guild['name']} (ID: {guild['id']})")
            logger.info(f"  权限: {', '.join(guild['permissions'])}")

        logger.info('Bot is fully ready!')

    async def close(self):
        """Clean up resources before shutdown"""
        logger.info("Bot is shutting down...")
        # 清理缓存和资源
        self._guild_settings.clear()
        self._cached_commands.clear()
        await super().close()

bot = QianBot()

# 添加信号处理
def signal_handler(sig, frame):
    logger.info(f"Received signal {sig}, initiating shutdown...")
    asyncio.create_task(bot.close())

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Global error handler for application commands"""
    error_msg = str(error)
    command_name = interaction.command.name if interaction.command else "未知命令"
    
    logger.error(f"Command '{command_name}' error: {error_msg}", exc_info=True)
    
    # 对不同类型的错误提供不同的用户提示
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"命令冷却中，请在 {error.retry_after:.1f} 秒后重试",
            ephemeral=True
        )
    elif isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            f"您缺少执行此命令的权限: {', '.join(error.missing_permissions)}",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"命令执行出错: {error_msg}",
            ephemeral=True
        )

def main():
    """Main entry point for the bot."""
    try:
        logger.info("Starting bot...")
        bot.run(TOKEN, log_handler=None)
    except discord.LoginFailure as e:
        logger.critical(f"Invalid token provided: {e}")
        raise
    except Exception as e:
        logger.critical(f"Failed to start bot: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    main()