import logging
from typing import Optional, List, Tuple
from discord import Message, Attachment

logger = logging.getLogger('discord_bot.attachment')

SUPPORTED_IMAGE_FORMATS = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
MAX_FILE_SIZE = 8 * 1024 * 1024  # 8MB

class AttachmentProcessor:
    """Class for processing Discord message attachments"""

    @staticmethod
    def is_valid_image(attachment: Attachment) -> bool:
        """
        Check if the attachment is a valid image

        Args:
            attachment: Discord Attachment object

        Returns:
            bool: Whether it is a valid image attachment
        """
        try:
            if not attachment:
                logger.warning("Received an empty attachment object")
                return False

            # Check file size
            if attachment.size > MAX_FILE_SIZE:
                logger.warning(f"Attachment too large: {attachment.filename} ({attachment.size} bytes)")
                return False

            # Check file extension
            if not any(attachment.filename.lower().endswith(ext) for ext in SUPPORTED_IMAGE_FORMATS):
                logger.info(f"Unsupported file format: {attachment.filename}")
                return False

            # Check content type
            if not attachment.content_type or 'image' not in attachment.content_type.lower():
                logger.info(f"Non-image content type: {attachment.content_type}")
                return False

            logger.info(f"Found valid image attachment: {attachment.filename} ({attachment.content_type})")
            return True

        except Exception as e:
            logger.error(f"Error checking attachment: {str(e)}")
            return False

    @classmethod
    def get_message_images(cls, message: Message) -> Tuple[Optional[str], List[str]]:
        """
        Get image attachment URLs from a message

        Args:
            message: Discord Message object

        Returns:
            Tuple[Optional[str], List[str]]: (URL of the first image, List of all image URLs)
        """
        if not message or not message.attachments:
            logger.info(f"No attachments in message {message.id if message else 'None'}")
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
                logger.info(f"Found {len(valid_images)} valid image attachments in message {message.id}")
            else:
                logger.info(f"No valid image attachments found in message {message.id}")

            return first_image, valid_images

        except Exception as e:
            logger.error(f"Error processing image attachments for message {message.id}: {str(e)}")
            return None, []

    @classmethod
    def get_first_image(cls, message: Message) -> Optional[str]:
        """
        Get the URL of the first image attachment in a message

        Args:
            message: Discord Message object

        Returns:
            Optional[str]: URL of the first image, or None if none exists
        """
        try:
            first_image, _ = cls.get_message_images(message)
            if first_image:
                logger.info(f"Retrieved first image: {first_image}")
            return first_image
        except Exception as e:
            logger.error(f"Error getting first image: {str(e)}")
            return None

    @classmethod
    def get_all_images(cls, message: Message) -> List[str]:
        """
        Get a list of URLs for all image attachments in a message

        Args:
            message: Discord Message object

        Returns:
            List[str]: List of all image URLs
        """
        try:
            _, all_images = cls.get_message_images(message)
            if all_images:
                logger.info(f"Retrieved {len(all_images)} images")
            return all_images
        except Exception as e:
            logger.error(f"Error getting all images: {str(e)}")
            return []