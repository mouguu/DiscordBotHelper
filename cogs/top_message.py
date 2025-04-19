import discord
from discord.ext import commands
from discord import app_commands
from utils.message_finder import find_first_message

class TopMessage(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def get_actual_channel(self, channel):
        """Helper function to get the actual channel from thread or forum"""
        return channel  # Directly return the current channel, let message_finder handle the specific logic

    @app_commands.command(name="back_to_top", description="Quickly jump to the first message of a channel or thread")
    async def back_to_top(self, interaction: discord.Interaction):
        """Directly find and display the link to the first message"""
        await interaction.response.defer(ephemeral=True)
        
        channel = interaction.channel
        actual_channel = await self.get_actual_channel(channel)
        first_message = await find_first_message(actual_channel)
        
        if first_message:
            # Create an embed message containing a link button
            embed = discord.Embed(
                title="Found the first message",
                description="Click the button below to jump to the first message",
                color=discord.Color.green()
            )
            
            # Create a view containing a link button
            view = discord.ui.View()
            view.add_item(
                discord.ui.Button(
                    style=discord.ButtonStyle.link,
                    label="Click to Jump",
                    emoji="🔗",
                    url=first_message.jump_url
                )
            )
            
            # Add time and author information
            embed.add_field(
                name="Sent Time",
                value=discord.utils.format_dt(first_message.created_at, "R"),
                inline=True
            )
            
            if first_message.author:
                embed.add_field(
                    name="Post Author",
                    value=first_message.author.mention,
                    inline=True
                )

            await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True
            )
        else:
            # Create an error embed message
            error_embed = discord.Embed(
                title="❌ Message Not Found",
                description="Could not find the first message. This might be because:\n• The message has been deleted\n• No permission to access\n• The channel is empty",
                color=discord.Color.red()
            )
            
            await interaction.followup.send(
                embed=error_embed,
                ephemeral=True
            )

async def setup(bot):
    await bot.add_cog(TopMessage(bot))
