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

# 设置日志配置
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('discord_bot')

# 设置 Discord 日志级别为 WARNING
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('discord.http').setLevel(logging.WARNING)

# 加载环境变量
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# 验证令牌格式
if TOKEN:
    logger.info(f"Token loaded successfully (starts with: {TOKEN[:10]}...)")
    token_parts = TOKEN.split('.')
    if len(token_parts) != 3:
        logger.error("Invalid token format")
        raise ValueError("Invalid token format")
else:
    logger.error("No Discord token found!")
    raise ValueError("Discord token is required")

class QianBot(commands.Bot):
    def __init__(self):
        # 设置意图
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
        
        # 初始化
        self.initial_extensions: List[str] = [
            'cogs.search',
            'cogs.top_message'
        ]
        self._ready = asyncio.Event()  # 标记 bot 是否已准备好
        self.persistent_views_added = False  # 标记是否已添加持久化视图
        self._guild_settings: Dict[int, Dict] = {}  # 服务器设置
        self._cached_commands: Set[str] = set()  # 缓存命令
        self._startup_time = None  # 启动时间记录

    async def setup_hook(self):
        """初始化设置"""
        try:
            start_time = asyncio.get_event_loop().time()
            
            # 加载扩展
            load_extension_tasks = [
                self.load_extension(extension) for extension in self.initial_extensions
            ]
            await asyncio.gather(*load_extension_tasks)
            logger.info(f"Loaded {len(self.initial_extensions)} extensions")

            # 同步命令到 Discord
            logger.info("Syncing commands with Discord...")
            try:
                synced_commands = await self.tree.sync()
                self._cached_commands = {cmd.name for cmd in synced_commands}
                logger.info(f"Synced {len(synced_commands)} commands")
            except Exception as e:
                logger.error(f"Failed to sync commands: {e}", exc_info=True)
                raise

            # 添加持久化视图
            if not self.persistent_views_added:
                pagination_view = MultiEmbedPaginationView([], 5, lambda x, y: [], timeout=None)
                self.add_view(pagination_view)
                self.persistent_views_added = True

            self._startup_time = asyncio.get_event_loop().time() - start_time
            logger.info(f"Setup completed in {self._startup_time:.2f} seconds")

        except Exception as e:
            logger.error(f"Setup failed: {e}", exc_info=True)
            raise

    async def on_ready(self):
        """当 bot 启动完成时调用"""
        if self._ready.is_set():
            return

        self._ready.set()
        
        # 收集服务器信息
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
        logger.info(f'Connected to {len(guild_info)} guilds')
        for guild in guild_info:
            logger.info(f"- {guild['name']} (ID: {guild['id']})")
            logger.info(f"  权限: {', '.join(guild['permissions'])}")

    async def close(self):
        """关闭时清理"""
        logger.info("Bot is shutting down...")
        self._guild_settings.clear()
        self._cached_commands.clear()
        await super().close()

bot = QianBot()

# 处理中断或终止信号
def signal_handler(sig, frame):
    logger.info(f"Received signal {sig}, initiating shutdown...")
    asyncio.create_task(bot.close())

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """命令错误处理器"""
    error_msg = str(error)
    command_name = interaction.command.name if interaction.command else "未知命令"
    
    logger.error(f"Command '{command_name}' error: {error_msg}", exc_info=True)
    
    # 根据错误类型返回不同的提示
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
    """启动 bot"""
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
