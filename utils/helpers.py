import discord
from typing import Optional

def create_embed(title: str, description: str, color: int) -> discord.Embed:
    """Create a Discord embed with consistent styling"""
    return discord.Embed(title=title, description=description, color=color)

def truncate_text(text: str, max_length: int) -> str:
    """Truncate text to max_length and add ellipsis if needed"""
    if not text:
        return "No content"
    if len(text) <= max_length:
        return text
    return f"{text[:max_length-3]}..."

def is_valid_image_url(url: Optional[str]) -> bool:
    """Simple check if URL is a valid image URL"""
    if not url:
        return False
    
    image_extensions = ('.jpg', '.jpeg', '.png', '.gif', '.webp')
    return any(url.lower().endswith(ext) for ext in image_extensions)