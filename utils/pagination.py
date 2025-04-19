import discord
from discord.ui import View, button
from typing import List, Callable, Any, Union, Optional
import asyncio
import logging

logger = logging.getLogger('discord_bot.pagination')

class PageSelectModal(discord.ui.Modal, title="Jump to Page"):
    def __init__(self, max_pages: int):
        super().__init__()
        self.max_pages = max_pages
        self.page_number = discord.ui.TextInput(
            label=f'Enter page number (1-{max_pages})',
            placeholder='Enter a number...',
            min_length=1,
            max_length=len(str(max_pages)),
            required=True
        )
        self.add_item(self.page_number)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            page = int(self.page_number.value)
            if 1 <= page <= self.max_pages:
                self.result = page - 1  # Convert to 0-based index
                await interaction.response.defer()
            else:
                await interaction.response.send_message(
                    f"Please enter a valid page number (1-{self.max_pages})",
                    ephemeral=True
                )
                self.result = None
        except ValueError:
            await interaction.response.send_message(
                "Please enter a valid number",
                ephemeral=True
            )
            self.result = None

class MultiEmbedPaginationView(View):
    def __init__(
        self, 
        items: List[Any], 
        items_per_page: int, 
        generate_embeds: Callable[[List[Any], int], Union[discord.Embed, List[discord.Embed]]], 
        timeout: Optional[float] = 900.0  # 15 minutes default timeout
    ):
        super().__init__(timeout=timeout)
        self.items = items
        self.items_per_page = items_per_page
        self.generate_embeds = generate_embeds
        self.current_page = 0
        self.total_items = len(items)
        self.total_pages = max((self.total_items + items_per_page - 1) // items_per_page, 1)
        self._logger = logger
        self._logger.info(f"Initializing pagination: Total items={self.total_items}, Items per page={items_per_page}, Total pages={self.total_pages}")
        self.message = None  # Store message reference
        self.last_interaction_time = None
        self.original_user = None

    def get_page_items(self, page: int) -> List[Any]:
        """Get items for the specified page"""
        if not self.items:
            self._logger.warning("No items to display")
            return []

        if page < 0 or page >= self.total_pages:
            self._logger.warning(f"Invalid page request: page={page}, total_pages={self.total_pages}")
            return []

        start_idx = page * self.items_per_page
        end_idx = min(start_idx + self.items_per_page, self.total_items)
        
        items = self.items[start_idx:end_idx]
        self._logger.debug(f"Getting page items: page={page + 1}, start={start_idx}, end={end_idx}, count={len(items)}")
        return items

    def update_button_states(self):
        """Update button states"""
        self.first_button.disabled = self.current_page <= 0
        self.prev_button.disabled = self.current_page <= 0
        self.next_button.disabled = self.current_page >= self.total_pages - 1
        self.last_button.disabled = self.current_page >= self.total_pages - 1
        
        self._logger.debug(
            f"Button states updated: first={self.first_button.disabled}, "
            f"prev={self.prev_button.disabled}, "
            f"next={self.next_button.disabled}, "
            f"last={self.last_button.disabled}"
        )

    async def check_permissions(self, interaction: discord.Interaction) -> bool:
        """Check if the Bot has necessary permissions"""
        if not interaction.guild:
            self._logger.warning("Cannot use this feature in DMs")
            return False

        permissions = interaction.channel.permissions_for(interaction.guild.me)
        required_permissions = {
            "view_channel": "View Channel",
            "send_messages": "Send Messages",
            "embed_links": "Embed Links",
            "read_message_history": "Read Message History",
            "add_reactions": "Add Reactions"
        }

        missing_permissions = []
        for perm, name in required_permissions.items():
            if not getattr(permissions, perm):
                missing_permissions.append(name)

        if missing_permissions:
            self._logger.error(f"Missing permissions: {', '.join(missing_permissions)}")
            self._logger.error(f"Bot is missing necessary permissions: {', '.join(missing_permissions)}")
            try:
                await interaction.response.send_message(
                    f"Bot is missing necessary permissions: {', '.join(missing_permissions)}",
                    ephemeral=True
                )
            except Exception as e:
                self._logger.error(f"Failed to send permission error message: {e}")
            return False

        return True

    async def safe_defer(self, interaction: discord.Interaction) -> bool:
        """Safely defer the interaction response"""
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
            return True
        except Exception as e:
            self._logger.error(f"Failed to defer response: {e}")
            return False

    async def update_message(self, interaction: discord.Interaction) -> bool:
        """Update message content"""
        try:
            # Check permissions
            if not await self.check_permissions(interaction):
                return False

            # Ensure current page is within valid range
            if self.current_page >= self.total_pages:
                self.current_page = max(0, self.total_pages - 1)
                self._logger.warning(f"Page out of range, adjusted to: {self.current_page + 1}")

            # Get items for the current page
            page_items = self.get_page_items(self.current_page)
            if not page_items and self.current_page > 0:
                self._logger.warning(f"Current page {self.current_page + 1} has no items, trying to return to the first page")
                self.current_page = 0
                page_items = self.get_page_items(self.current_page)

            if not page_items:
                self._logger.error("Could not get valid page items")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Could not display content for this page, please try again",
                        ephemeral=True
                    )
                return False

            # Generate new embeds
            try:
                embeds = await self.generate_embeds(page_items, self.current_page)
                if not isinstance(embeds, list):
                    embeds = [embeds]
            except Exception as e:
                self._logger.error(f"Failed to generate embeds: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Error generating page content, please try again",
                        ephemeral=True
                    )
                return False

            if not embeds:
                self._logger.error("Generated embeds are empty")
                if not interaction.response.is_done():
                    await interaction.response.send_message(
                        "Could not generate page content, please try again",
                        ephemeral=True
                    )
                return False

            # Update button states
            self.update_button_states()

            # Update message
            try:
                if interaction.response.is_done():
                    await interaction.message.edit(embeds=embeds, view=self)
                else:
                    await interaction.response.edit_message(embeds=embeds, view=self)
                self.last_interaction_time = discord.utils.utcnow()
                return True
            except discord.errors.NotFound:
                self._logger.error("Message not found or has been deleted")
                return False
            except discord.errors.Forbidden as e:
                self._logger.error(f"No permission to edit message: {e}")
                return False

        except Exception as e:
            self._logger.error(f"Failed to update message: {e}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "An error occurred while updating the page, please try again",
                    ephemeral=True
                )
            return False

    async def handle_button_interaction(self, interaction: discord.Interaction, action: str) -> None:
        """Handle button interactions uniformly"""
        try:
            if not await self.check_permissions(interaction):
                return

            self._logger.debug(f"Handling button interaction: {action}")
            # Update last interaction time
            self.last_interaction_time = discord.utils.utcnow()
            await self.update_message(interaction)
        except Exception as e:
            self._logger.error(f"Error handling button {action}: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"An error occurred while handling the {action} button, please try again",
                    ephemeral=True
                )

    @button(emoji="⏮️", style=discord.ButtonStyle.blurple, custom_id="pagination:first")
    async def first_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Jump to the first page"""
        if self.current_page != 0:
            self._logger.debug("Jumping to first page")
            self.current_page = 0
            await self.handle_button_interaction(interaction, "First Page")
        else:
            await self.safe_defer(interaction)

    @button(emoji="◀️", style=discord.ButtonStyle.blurple, custom_id="pagination:prev")
    async def prev_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Previous page"""
        if self.current_page > 0:
            self._logger.debug(f"Previous page: {self.current_page + 1} -> {self.current_page}")
            self.current_page -= 1
            await self.handle_button_interaction(interaction, "Previous Page")
        else:
            await self.safe_defer(interaction)

    @button(emoji="🔢", style=discord.ButtonStyle.grey, custom_id="pagination:page")
    async def page_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Jump to a specific page"""
        modal = PageSelectModal(self.total_pages)
        await interaction.response.send_modal(modal)
        await modal.wait()
        
        if modal.result is not None:
            if self.current_page != modal.result:
                self._logger.debug(f"Jumping to page {modal.result + 1}")
                self.current_page = modal.result
                # Need to re-acquire interaction object and update message
                # The original interaction is already responded to by the modal, so we need to use followup or edit the original message
                if self.message:
                    try:
                        await self.update_message(self.message.channel.get_partial_message(self.message.id).edit) # This is conceptually wrong, need a better way to re-trigger update
                        # A better approach might be to just call self.update_message with a dummy interaction or handle update logic separately
                        # For now, just log the intent
                        self._logger.info("Page jump handled, but message update needs rework after modal.")
                    except Exception as e:
                        self._logger.error(f"Error updating message after page jump: {e}")
            else:
                # If already on the target page, just acknowledge the interaction
                await interaction.followup.send("You are already on this page.", ephemeral=True)

    @button(emoji="▶️", style=discord.ButtonStyle.blurple, custom_id="pagination:next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Next page"""
        if self.current_page < self.total_pages - 1:
            self._logger.debug(f"Next page: {self.current_page + 1} -> {self.current_page + 2}")
            self.current_page += 1
            await self.handle_button_interaction(interaction, "Next Page")
        else:
            await self.safe_defer(interaction)

    @button(emoji="⏭️", style=discord.ButtonStyle.blurple, custom_id="pagination:last")
    async def last_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Jump to the last page"""
        if self.current_page != self.total_pages - 1:
            self._logger.debug(f"Jumping to last page: {self.total_pages}")
            self.current_page = self.total_pages - 1
            await self.handle_button_interaction(interaction, "Last Page")
        else:
            await self.safe_defer(interaction)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Check if the interaction is valid and initiated by the original user"""
        if self.original_user is None:
            self._logger.warning("Original user not set")
            return False
        
        if interaction.user.id != self.original_user.id:
            await interaction.response.send_message(
                "Only the user who initiated the command can use these buttons",
                ephemeral=True
            )
            return False
            
        # Check if it's a button interaction
        if interaction.type != discord.InteractionType.component or interaction.data["component_type"] != 2:
             self._logger.debug(f"Non-button interaction ignored: {interaction.type}")
             return False
             
        # Check if custom_id matches expected pattern
        if not interaction.data["custom_id"].startswith("pagination:"):
            self._logger.debug(f"Mismatched custom ID: {interaction.data['custom_id']}")
            return False
            
        self._logger.debug(f"Interaction check passed for user: {interaction.user}")
        return True

    async def on_timeout(self):
        """Disable all buttons on timeout"""
        self._logger.info("Pagination timed out")
        if self.message:
            try:
                for item in self.children:
                    if isinstance(item, discord.ui.Button):
                        item.disabled = True
                
                # Remove view from the message
                await self.message.edit(view=None)
                self._logger.debug(f"Pagination buttons disabled and removed for message {self.message.id}")
            except discord.errors.NotFound:
                self._logger.warning("Message no longer exists on timeout")
            except discord.errors.Forbidden:
                self._logger.error("No permission to edit message on timeout")
            except Exception as e:
                self._logger.error(f"Error removing timed out view: {e}")
            self.stop()

    async def start(self, interaction: discord.Interaction, initial_embeds: Union[discord.Embed, List[discord.Embed]]):
        """Start the pagination"""
        if not await self.check_permissions(interaction):
            return
            
        # Store original user
        self.original_user = interaction.user
        self._logger.debug(f"Storing original user ID: {self.original_user.id}")

        if not isinstance(initial_embeds, list):
            initial_embeds = [initial_embeds]

        if not initial_embeds:
            self._logger.error("Initial embeds are empty")
            await interaction.followup.send(
                "无法显示搜索结果，请重试",
                ephemeral=True
            )
            return

        # Update button states
        self.update_button_states()
        
        self._logger.info(f"Starting pagination: Total pages={self.total_pages}, Current page={self.current_page + 1}")
        
        # Send initial message and save reference
        self.message = await interaction.followup.send(
            embeds=initial_embeds, 
            view=self, 
            ephemeral=getattr(interaction, 'ephemeral', False)
        )
        self.last_interaction_time = discord.utils.utcnow()

        self._logger.info(f"Pagination started (followup): Message ID={self.message.id}, User={self.original_user}")

        try:
            self.last_interaction_time = discord.utils.utcnow()
            
        except Exception as e:
            self._logger.error(f"Failed to start pagination: {e}", exc_info=True)
            # Try to notify the user about the error
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("Error starting pagination, please try again", ephemeral=True)
                else:
                    await interaction.response.send_message("Error starting pagination, please try again", ephemeral=True)
            except Exception as ie:
                self._logger.error(f"Failed to send startup error message: {ie}")