import logging
from typing import Optional, List, Tuple
from discord import Message, Attachment

logger = logging.getLogger('discord_bot.attachment')

SUPPORTED_IMAGE_FORMATS = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
MAX_FILE_SIZE = 8 * 1024 * 1024  # 8MB

class AttachmentProcessor:
    """Processes Discord message attachments with validation and extraction"""

    @staticmethod
    def is_valid_image(attachment: Attachment) -> bool:
        try:
            if not attachment:
                logger.warning("[boundary:error] Empty attachment object received")
                return False

            # Size validation
            if attachment.size > MAX_FILE_SIZE:
                logger.info(f"[boundary] Attachment too large: {attachment.filename} ({attachment.size} bytes)")
                return False

            # Extension validation
            if not any(attachment.filename.lower().endswith(ext) for ext in SUPPORTED_IMAGE_FORMATS):
                return False

            # Content type validation
            if not attachment.content_type or 'image' not in attachment.content_type.lower():
                return False

            logger.debug(f"[signal] Valid image: {attachment.filename}")
            return True

        except Exception as e:
            logger.error(f"[boundary:error] Attachment validation failed: {e}")
            return False

    @classmethod
    def get_message_images(cls, message: Message) -> Tuple[Optional[str], List[str]]:
        if not message or not message.attachments:
            return None, []

        valid_images = []
        first_image = None

        try:
            for attachment in message.attachments:
                if cls.is_valid_image(attachment):
                    image_url = attachment.proxy_url or attachment.url
                    if not first_image:
                        first_image = image_url
                    valid_images.append(image_url)

            if valid_images:
                logger.debug(f"[signal] Found {len(valid_images)} images in message {message.id}")

            return first_image, valid_images

        except Exception as e:
            logger.error(f"[boundary:error] Image extraction failed for message {message.id}: {e}")
            return None, []

    @classmethod
    def get_first_image(cls, message: Message) -> Optional[str]:
        try:
            first_image, _ = cls.get_message_images(message)
            return first_image
        except Exception as e:
            logger.error(f"[boundary:error] First image extraction failed: {e}")
            return None

    @classmethod
    def get_all_images(cls, message: Message) -> List[str]:
        try:
            _, all_images = cls.get_message_images(message)
            return all_images
        except Exception as e:
            logger.error(f"[boundary:error] All images extraction failed: {e}")
            return []