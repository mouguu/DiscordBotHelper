import logging
from typing import Optional, List, Tuple
from discord import Message, Attachment

logger = logging.getLogger('discord_bot.attachment')

SUPPORTED_IMAGE_FORMATS = ['.jpg', '.jpeg', '.png', '.gif', '.webp']
MAX_FILE_SIZE = 8 * 1024 * 1024  # 8MB

class AttachmentProcessor:
    """处理Discord消息附件的类"""

    @staticmethod
    def is_valid_image(attachment: Attachment) -> bool:
        """
        检查附件是否为有效的图片

        Args:
            attachment: Discord的附件对象

        Returns:
            bool: 是否为有效的图片附件
        """
        try:
            if not attachment:
                logger.warning("收到空的附件对象")
                return False

            # 检查文件大小
            if attachment.size > MAX_FILE_SIZE:
                logger.warning(f"附件太大: {attachment.filename} ({attachment.size} bytes)")
                return False

            # 检查文件扩展名
            if not any(attachment.filename.lower().endswith(ext) for ext in SUPPORTED_IMAGE_FORMATS):
                logger.info(f"不支持的文件格式: {attachment.filename}")
                return False

            # 检查content type
            if not attachment.content_type or 'image' not in attachment.content_type.lower():
                logger.info(f"非图片content type: {attachment.content_type}")
                return False

            logger.info(f"找到有效图片附件: {attachment.filename} ({attachment.content_type})")
            return True

        except Exception as e:
            logger.error(f"检查附件时出错: {str(e)}")
            return False

    @classmethod
    def get_message_images(cls, message: Message) -> Tuple[Optional[str], List[str]]:
        """
        获取消息中的图片附件URL

        Args:
            message: Discord消息对象

        Returns:
            Tuple[Optional[str], List[str]]: (第一张图片URL, 所有图片URL列表)
        """
        if not message or not message.attachments:
            logger.info(f"消息 {message.id if message else 'None'} 中没有附件")
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
                logger.info(f"在消息 {message.id} 中找到 {len(valid_images)} 个有效图片附件")
            else:
                logger.info(f"在消息 {message.id} 中没有找到有效的图片附件")

            return first_image, valid_images

        except Exception as e:
            logger.error(f"处理消息 {message.id} 的图片附件时出错: {str(e)}")
            return None, []

    @classmethod
    def get_first_image(cls, message: Message) -> Optional[str]:
        """
        获取消息中的第一个图片附件URL

        Args:
            message: Discord消息对象

        Returns:
            Optional[str]: 第一个图片的URL，如果没有则返回None
        """
        try:
            first_image, _ = cls.get_message_images(message)
            if first_image:
                logger.info(f"获取到首张图片: {first_image}")
            return first_image
        except Exception as e:
            logger.error(f"获取首张图片时出错: {str(e)}")
            return None

    @classmethod
    def get_all_images(cls, message: Message) -> List[str]:
        """
        获取消息中所有图片附件的URL列表

        Args:
            message: Discord消息对象

        Returns:
            List[str]: 所有图片URL的列表
        """
        try:
            _, all_images = cls.get_message_images(message)
            if all_images:
                logger.info(f"获取到 {len(all_images)} 张图片")
            return all_images
        except Exception as e:
            logger.error(f"获取所有图片时出错: {str(e)}")
            return []