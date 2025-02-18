import discord
from discord.ext import commands
from discord import app_commands
from utils.message_finder import find_first_message

class TopMessage(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def get_actual_channel(self, channel):
        """Helper function to get the actual channel from thread or forum"""
        return channel  # 直接返回当前频道，让message_finder处理具体逻辑

    @app_commands.command(name="回顶", description="快速跳转到频道或帖子的第一条消息")
    async def back_to_top(self, interaction: discord.Interaction):
        """直接查找并显示第一条消息的链接"""
        await interaction.response.defer(ephemeral=True)
        
        channel = interaction.channel
        actual_channel = await self.get_actual_channel(channel)
        first_message = await find_first_message(actual_channel)
        
        if first_message:
            # 创建一个包含链接按钮的嵌入消息
            embed = discord.Embed(
                title="找到最初的消息",
                description="点击下方按钮跳转到最初的消息",
                color=discord.Color.green()
            )
            
            # 创建一个包含链接按钮的视图
            view = discord.ui.View()
            view.add_item(
                discord.ui.Button(
                    style=discord.ButtonStyle.link,
                    label="点击跳转",
                    emoji="🔗",
                    url=first_message.jump_url
                )
            )
            
            # 添加时间和作者信息
            embed.add_field(
                name="发送时间",
                value=discord.utils.format_dt(first_message.created_at, "R"),
                inline=True
            )
            
            if first_message.author:
                embed.add_field(
                    name="帖子作者",
                    value=first_message.author.mention,
                    inline=True
                )

            await interaction.followup.send(
                embed=embed,
                view=view,
                ephemeral=True
            )
        else:
            # 创建错误提示的嵌入消息
            error_embed = discord.Embed(
                title="❌ 未找到消息",
                description="无法找到最初的消息，这可能是因为：\n• 消息已被删除\n• 没有权限访问\n• 频道为空",
                color=discord.Color.red()
            )
            
            await interaction.followup.send(
                embed=error_embed,
                ephemeral=True
            )

async def setup(bot):
    await bot.add_cog(TopMessage(bot))
