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

# Set up logging configuration
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('discord_bot')

# Set Discord log level to WARNING
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('discord.http').setLevel(logging.WARNING)

# Load environment variables
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')

# Validate token format
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
        # Set intents
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
        
        # Initialization
        self.initial_extensions: List[str] = [
            'cogs.search',
            'cogs.top_message',
            'cogs.stats' # Ensure stats cog is loaded
        ]
        self._ready = asyncio.Event()  # Mark if the bot is ready
        self.persistent_views_added = False  # Mark if persistent views have been added
        self._guild_settings: Dict[int, Dict] = {}  # Server settings
        self._cached_commands: Set[str] = set()  # Cached commands
        self._startup_time = None  # Startup time record

    async def setup_hook(self):
        """Initialization setup"""
        try:
            start_time = asyncio.get_event_loop().time()
            
            # Load extensions
            load_extension_tasks = [
                self.load_extension(extension) for extension in self.initial_extensions
            ]
            await asyncio.gather(*load_extension_tasks)
            logger.info(f"Loaded {len(self.initial_extensions)} extensions")

            # Sync commands with Discord
            logger.info("Syncing commands with Discord...")
            try:
                synced_commands = await self.tree.sync()
                self._cached_commands = {cmd.name for cmd in synced_commands}
                logger.info(f"Synced {len(synced_commands)} commands")
            except Exception as e:
                logger.error(f"Failed to sync commands: {e}", exc_info=True)
                raise

            # Add persistent views
            if not self.persistent_views_added:
                # Instantiate the view (ensure parameters match constructor)
                pagination_view = MultiEmbedPaginationView([], 5, lambda items, page: [], timeout=None) 
                self.add_view(pagination_view)
                self.persistent_views_added = True

            self._startup_time = asyncio.get_event_loop().time() - start_time
            logger.info(f"Setup completed in {self._startup_time:.2f} seconds")

        except Exception as e:
            logger.error(f"Setup failed: {e}", exc_info=True)
            raise

    async def on_ready(self):
        """Called when the bot is ready"""
        if self._ready.is_set():
            return

        self._ready.set()
        
        # Collect server information
        guild_info = []
        for guild in self.guilds:
            bot_member = guild.get_member(self.user.id)
            permissions = []
            if bot_member:
                perms = bot_member.guild_permissions
                if perms.administrator:
                    permissions.append("Administrator")
                else:
                    if perms.send_messages: permissions.append("Send Messages")
                    if perms.embed_links: permissions.append("Embed Links")
                    if perms.add_reactions: permissions.append("Add Reactions")
                    if perms.read_messages: permissions.append("Read Messages")
                    if perms.view_channel: permissions.append("View Channel")
            
            guild_info.append({
                'name': guild.name,
                'id': guild.id,
                'permissions': permissions
            })

        # Log startup information
        logger.info(f'Logged in as {self.user} (ID: {self.user.id})')
        logger.info(f'Connected to {len(guild_info)} guilds')
        for guild in guild_info:
            logger.info(f"- {guild['name']} (ID: {guild['id']})")
            logger.info(f"  Permissions: {', '.join(guild['permissions'])}")

    async def close(self):
        """Clean up on close"""
        logger.info("Bot is shutting down...")
        self._guild_settings.clear()
        self._cached_commands.clear()
        await super().close()

bot = QianBot()

# Handle interrupt or termination signals
def signal_handler(sig, frame):
    logger.info(f"Received signal {sig}, initiating shutdown...")
    asyncio.create_task(bot.close())

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    """Command error handler"""
    error_msg = str(error)
    command_name = interaction.command.name if interaction.command else "Unknown command"
    
    logger.error(f"Command '{command_name}' error: {error_msg}", exc_info=True)
    
    # Return different messages based on error type
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"Command is on cooldown, please try again in {error.retry_after:.1f} seconds",
            ephemeral=True
        )
    elif isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            f"You lack the permissions to execute this command: {', '.join(error.missing_permissions)}",
            ephemeral=True
        )
    else:
        # Generic error message
        await interaction.response.send_message(
            f"An error occurred while executing the command: {error_msg}",
            ephemeral=True
        )

def main():
    """Start the bot"""
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
