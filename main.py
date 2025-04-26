import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import logging
import asyncio
from config.config import COMMAND_PREFIX, LOG_LEVEL
import signal
from utils.pagination import MultiEmbedPaginationView

# Configure logging
logging.basicConfig(level=LOG_LEVEL, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                   datefmt='%Y-%m-%d %H:%M:%S')
logger = logging.getLogger('discord_bot')
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('discord.http').setLevel(logging.WARNING)

# Load token
load_dotenv()
TOKEN = os.getenv('DISCORD_TOKEN')
if not TOKEN or len(TOKEN.split('.')) != 3:
    logger.error("[boundary:error] Invalid or missing token")
    raise ValueError("Valid Discord token required")

class QianBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = intents.guilds = intents.guild_messages = intents.members = True
        super().__init__(command_prefix=COMMAND_PREFIX, intents=intents, guild_ready_timeout=10)
        
        self.initial_extensions = ['cogs.search', 'cogs.top_message', 'cogs.stats']
        self._ready = asyncio.Event()
        self.persistent_views_added = False
        self._guild_settings = {}
        self._cached_commands = set()

    async def setup_hook(self):
        try:
            start = asyncio.get_event_loop().time()
            
            # Load extensions & sync commands
            await asyncio.gather(*[self.load_extension(ext) for ext in self.initial_extensions])
            logger.info(f"[init] Loaded {len(self.initial_extensions)} extensions")
            
            try:
                cmds = await self.tree.sync()
                self._cached_commands = {cmd.name for cmd in cmds}
                logger.info(f"[signal] Synced {len(cmds)} commands")
            except Exception as e:
                logger.error(f"[boundary:error] Command sync failed: {e}")
                raise
                
            # Add pagination view
            if not self.persistent_views_added:
                self.add_view(MultiEmbedPaginationView([], 5, lambda items, page: [], timeout=None))
                self.persistent_views_added = True
                
            logger.info(f"[signal] Setup completed in {asyncio.get_event_loop().time() - start:.2f}s")
        except Exception as e:
            logger.error(f"[boundary:error] Setup failed: {e}")
            raise

    async def on_ready(self):
        if self._ready.is_set(): return
        self._ready.set()
        
        # Log guild connections
        guild_info = []
        for guild in self.guilds:
            perms = []
            if member := guild.get_member(self.user.id):
                p = member.guild_permissions
                if p.administrator:
                    perms.append("Administrator")
                else:
                    for name, has in [("Send Messages", p.send_messages), ("Embed Links", p.embed_links),
                                      ("Add Reactions", p.add_reactions), ("View Channel", p.view_channel)]:
                        if has: perms.append(name)
            
            guild_info.append({'name': guild.name, 'id': guild.id, 'permissions': perms})
            logger.info(f"[signal] Guild: {guild['name']} | Perms: {', '.join(perms)}")

        logger.info(f"[signal] Ready as {self.user} | Connected to {len(guild_info)} guilds")

    async def close(self):
        logger.info("[signal] Shutting down...")
        self._guild_settings.clear()
        self._cached_commands.clear()
        await super().close()

bot = QianBot()

# Signal handlers
for sig in (signal.SIGINT, signal.SIGTERM):
    signal.signal(sig, lambda s, f: asyncio.create_task(bot.close()))

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    cmd = interaction.command.name if interaction.command else "Unknown"
    logger.error(f"[boundary:error] Command '{cmd}' failed: {error}")
    
    msg = f"An error occurred: {error}"
    if isinstance(error, app_commands.CommandOnCooldown):
        msg = f"Command on cooldown, try again in {error.retry_after:.1f}s"
    elif isinstance(error, app_commands.MissingPermissions):
        msg = f"Missing permissions: {', '.join(error.missing_permissions)}"
        
    await interaction.response.send_message(msg, ephemeral=True)

if __name__ == "__main__":
    try:
        logger.info("[init] Starting bot...")
        bot.run(TOKEN, log_handler=None)
    except Exception as e:
        logger.critical(f"[boundary:error] Bot failed: {e}")
        raise
