"""
Клиент для отправки уведомлений в Mattermost
"""
import aiohttp
from typing import Optional, Dict, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class MattermostClient:
    """Клиент для отправки сообщений в Mattermost через webhook"""
    
    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
        self.timeout = aiohttp.ClientTimeout(total=10)
    
    async def send_message(self, text: str, username: str = "Backup Server", 
                          icon_url: Optional[str] = None, channel: Optional[str] = None) -> bool:
        """Отправляет сообщение в Mattermost"""
        if not self.webhook_url:
            return False
        
        payload = {
            "text": text,
            "username": username,
            "icon_url": icon_url or "https://mattermost.com/wp-content/uploads/2022/02/icon.png"
        }
        
        # Добавляем канал, если указан
        if channel:
            payload["channel"] = channel
        
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(self.webhook_url, json=payload) as response:
                    if response.status == 200:
                        return True
                    else:
                        logger.error(f"Mattermost webhook returned status {response.status}")
                        return False
        except Exception as e:
            logger.error(f"Error sending message to Mattermost: {e}")
            return False
    
    async def send_backup_alert(self, task_name: str, error_message: str, channel: Optional[str] = None) -> bool:
        """Отправляет уведомление о проблеме с бэкапом"""
        text = f"⚠️ **Проблема с резервным копированием**\n\n"
        text += f"**Задача:** {task_name}\n"
        text += f"**Ошибка:** {error_message}\n"
        text += f"**Время:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        return await self.send_message(text, channel=channel)
    
    async def send_daily_report(self, report_data: Dict[str, Any], channel: Optional[str] = None) -> bool:
        """Отправляет ежедневный отчет о бэкапах"""
        text = "📊 **Ежедневный отчет о резервном копировании**\n\n"
        
        total_tasks = report_data.get("total_tasks", 0)
        successful = report_data.get("successful", 0)
        failed = report_data.get("failed", 0)
        warnings = report_data.get("warnings", 0)
        
        text += f"**Всего задач:** {total_tasks}\n"
        text += f"✅ **Успешных:** {successful}\n"
        text += f"❌ **Ошибок:** {failed}\n"
        text += f"⚠️ **Предупреждений:** {warnings}\n\n"
        
        if report_data.get("failed_tasks"):
            text += "**Задачи с ошибками:**\n"
            for task in report_data["failed_tasks"]:
                text += f"- {task['name']}: {task['error']}\n"
            text += "\n"
        
        if report_data.get("disk_warnings"):
            text += "**Предупреждения о дисках:**\n"
            for warning in report_data["disk_warnings"]:
                text += f"- {warning}\n"
            text += "\n"
        
        text += f"**Время отчета:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        
        return await self.send_message(text, channel=channel)
    
    async def send_custom_report(self, report_text: str, channel: Optional[str] = None) -> bool:
        """Отправляет пользовательский отчет"""
        return await self.send_message(report_text, channel=channel)
    
    async def send_test_message(self, channel: Optional[str] = None) -> bool:
        """Отправляет тестовое сообщение"""
        text = "✅ **Тестовое уведомление**\n\n"
        text += "Это тестовое сообщение от системы резервного копирования.\n"
        text += f"**Время:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        text += "Если вы видите это сообщение, значит интеграция с Mattermost настроена правильно! 🎉"
        
        return await self.send_message(text, channel=channel)







