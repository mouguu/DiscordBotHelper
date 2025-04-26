import discord
from discord.ui import View, button
from typing import List, Callable, Any, Union, Optional
import asyncio, logging

logger = logging.getLogger('discord_bot.pagination')

class PageSelectModal(discord.ui.Modal, title="Jump to Page"):
    def __init__(self, max_pages: int):
        super().__init__()
        self.max_pages = max_pages
        self.page_number = discord.ui.TextInput(label=f'Page (1-{max_pages})', placeholder='Number...', 
                                               min_length=1, max_length=len(str(max_pages)), required=True)
        self.add_item(self.page_number)

    async def on_submit(self, interaction: discord.Interaction):
        try: 
            page = int(self.page_number.value)
            self.result = page - 1 if 1 <= page <= self.max_pages else None
            await (interaction.response.defer() if self.result is not None else 
                  interaction.response.send_message(f"Invalid page (1-{self.max_pages})", ephemeral=True))
        except ValueError: 
            await interaction.response.send_message("Enter a valid number", ephemeral=True)
            self.result = None

class MultiEmbedPaginationView(View):
    def __init__(self, items: List[Any], items_per_page: int, 
                embed_generator: Callable[[List[Any], int], Union[discord.Embed, List[discord.Embed]]], 
                timeout: float = 900.0):
        super().__init__(timeout=timeout)
        self.items, self.items_per_page, self.generate_embeds = items, items_per_page, embed_generator
        self.current_page, self.message, self.original_user = 0, None, None
        self.total_pages = max(1, (len(items) + items_per_page - 1) // items_per_page)

    def get_page_items(self, page: int = None) -> List[Any]: 
        page = self.current_page if page is None else page
        return [] if not self.items or not (0 <= page < self.total_pages) else (
            self.items[page * self.items_per_page:min((page + 1) * self.items_per_page, len(self.items))]
        )

    def update_button_states(self):
        disabled_first = self.current_page <= 0
        disabled_last = self.current_page >= self.total_pages - 1
        self.first_button.disabled = self.prev_button.disabled = disabled_first
        self.next_button.disabled = self.last_button.disabled = disabled_last

    async def check_permissions(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild: return False
        permissions = interaction.channel.permissions_for(interaction.guild.me)
        required = {"view_channel", "send_messages", "embed_links", "read_message_history", "add_reactions"}
        missing = [name.replace("_", " ").title() for name in required if not getattr(permissions, name)]
        missing and await interaction.response.send_message(f"Missing: {', '.join(missing)}", ephemeral=True)
        return not missing

    async def update_message(self, interaction: discord.Interaction) -> bool:
        if not await self.check_permissions(interaction): return False
        
        # Ensure valid page and get items
        self.current_page = max(0, min(self.current_page, self.total_pages - 1))
        items = self.get_page_items() or (self.current_page > 0 and (setattr(self, 'current_page', 0) or self.get_page_items()))
        
        if not items:
            not interaction.response.is_done() and await interaction.response.send_message("No content", ephemeral=True)
            return False
            
        try:
            # Generate and update embeds
            embeds = await self.generate_embeds(items, self.current_page)
            embeds = [embeds] if not isinstance(embeds, list) else embeds
            if not embeds: raise ValueError()
            
            self.update_button_states()
            
            # Use message.edit or interaction.response based on state
            update_fn = interaction.message.edit if interaction.response.is_done() else interaction.response.edit_message
            await update_fn(embeds=embeds, view=self)
            return True
        except:
            not interaction.response.is_done() and await interaction.response.send_message("Update failed", ephemeral=True)
            return False

    @button(emoji="⏮️", style=discord.ButtonStyle.blurple, custom_id="pagination:first")
    async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page == 0: await interaction.response.defer(); return
        self.current_page = 0
        await self.check_permissions(interaction) and await self.update_message(interaction)

    @button(emoji="◀️", style=discord.ButtonStyle.blurple, custom_id="pagination:prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page <= 0: await interaction.response.defer(); return
        self.current_page -= 1
        await self.check_permissions(interaction) and await self.update_message(interaction)

    @button(emoji="🔢", style=discord.ButtonStyle.grey, custom_id="pagination:page")
    async def page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = PageSelectModal(self.total_pages)
        await interaction.response.send_modal(modal)
        await modal.wait()
        
        if modal.result is not None:
            if self.current_page != modal.result:
                self.current_page = modal.result
                self.message and await self.update_message(interaction)
            else:
                await interaction.followup.send("Already on this page", ephemeral=True)

    @button(emoji="▶️", style=discord.ButtonStyle.blurple, custom_id="pagination:next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page >= self.total_pages - 1: await interaction.response.defer(); return
        self.current_page += 1
        await self.check_permissions(interaction) and await self.update_message(interaction)

    @button(emoji="⏭️", style=discord.ButtonStyle.blurple, custom_id="pagination:last")
    async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page == self.total_pages - 1: await interaction.response.defer(); return
        self.current_page = self.total_pages - 1
        await self.check_permissions(interaction) and await self.update_message(interaction)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        valid_interaction = (interaction.type == discord.InteractionType.component and 
                           interaction.data["component_type"] == 2 and 
                           interaction.data["custom_id"].startswith("pagination:"))
        
        if not self.original_user or interaction.user.id != self.original_user.id:
            self.original_user and await interaction.response.send_message("Only initiator can use", ephemeral=True)
            return False
            
        return valid_interaction

    async def on_timeout(self):
        if self.message:
            try:
                [setattr(btn, 'disabled', True) for btn in self.children if isinstance(btn, discord.ui.Button)]
                await self.message.edit(view=None)
            except:
                pass
            self.stop()

    async def start(self, interaction: discord.Interaction, initial_embeds: Union[discord.Embed, List[discord.Embed]]):
        if not await self.check_permissions(interaction): return
        
        self.original_user = interaction.user
        initial_embeds = [initial_embeds] if not isinstance(initial_embeds, list) else initial_embeds
        
        if not initial_embeds:
            await interaction.followup.send("无法显示结果", ephemeral=True)
            return
            
        self.update_button_states()
        self.message = await interaction.followup.send(
            embeds=initial_embeds, 
            view=self, 
            ephemeral=getattr(interaction, 'ephemeral', False)
        )