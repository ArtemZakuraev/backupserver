"""
Клиент для взаимодействия с агентами резервного копирования
"""
from __future__ import annotations

from typing import Optional, Dict, Any, List
import aiohttp
import logging

from schemas import AgentSystemInfo, AgentFilesystemInfo, AgentTaskConfig, AgentTaskExecute
from config import settings

logger = logging.getLogger(__name__)


class AgentClient:
    """Клиент для взаимодействия с агентом"""
    
    def __init__(self, ip_address: str, port: int = 11540):
        self.ip_address = ip_address
        self.port = port
        self.base_url = f"http://{ip_address}:{port}"
        self.timeout = aiohttp.ClientTimeout(total=30)
    
    async def ping(self) -> bool:
        """Проверяет доступность агента"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(f"{self.base_url}/ping") as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Error pinging agent {self.ip_address}:{self.port}: {e}")
            return False
    
    async def get_system_info(self) -> Optional[AgentSystemInfo]:
        """Получает информацию о системе агента"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(f"{self.base_url}/api/system") as response:
                    if response.status == 200:
                        data = await response.json()
                        return AgentSystemInfo(**data)
                    return None
        except Exception as e:
            logger.error(f"Error getting system info from agent {self.ip_address}:{self.port}: {e}")
            return None
    
    async def get_filesystem_info(self, path: str) -> Optional[AgentFilesystemInfo]:
        """Получает информацию о файловой системе для указанного пути"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/api/filesystem",
                    json={"path": path}
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return AgentFilesystemInfo(**data)
                    return None
        except Exception as e:
            logger.error(f"Error getting filesystem info from agent {self.ip_address}:{self.port}: {e}")
            return None
    
    async def send_task_config(self, task_config: AgentTaskConfig) -> bool:
        """Отправляет конфигурацию задачи агенту"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/api/task/config",
                    json=task_config.dict()
                ) as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Error sending task config to agent {self.ip_address}:{self.port}: {e}")
            return False
    
    async def execute_task(self, task_execute: AgentTaskExecute) -> Dict[str, Any]:
        """Запускает выполнение задачи на агенте"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/api/task/execute",
                    json=task_execute.dict()
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    return {"success": False, "error": f"HTTP {response.status}"}
        except Exception as e:
            logger.error(f"Error executing task on agent {self.ip_address}:{self.port}: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_backup_info(self) -> Optional[List[Dict[str, Any]]]:
        """Получает информацию о бэкапах от агента"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(f"{self.base_url}/api/backups") as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("backups", [])
                    return None
        except Exception as e:
            logger.error(f"Error getting backup info from agent {self.ip_address}:{self.port}: {e}")
            return None
    
    async def get_all_disks(self) -> Optional[List[Dict[str, Any]]]:
        """Получает информацию о всех дисках агента"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.get(f"{self.base_url}/api/disks") as response:
                    if response.status == 200:
                        data = await response.json()
                        return data.get("disks", [])
                    return None
        except Exception as e:
            logger.error(f"Error getting disks info from agent {self.ip_address}:{self.port}: {e}")
            return None
    
    async def set_postgres_connection(self, connection_id: int, host: str, port: int, username: str, password: str, database: str) -> bool:
        """Настраивает подключение к PostgreSQL на агенте"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/api/postgres/connection",
                    json={
                        "id": connection_id,
                        "host": host,
                        "port": port,
                        "username": username,
                        "password": password,
                        "database": database
                    }
                ) as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Error setting PostgreSQL connection on agent {self.ip_address}:{self.port}: {e}")
            return False
    
    async def set_postgres_task_config(self, task_config: Dict[str, Any]) -> bool:
        """Отправляет конфигурацию задачи PostgreSQL агенту"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/api/postgres/task/config",
                    json=task_config
                ) as response:
                    return response.status == 200
        except Exception as e:
            logger.error(f"Error sending PostgreSQL task config to agent {self.ip_address}:{self.port}: {e}")
            return False
    
    async def execute_postgres_backup(self, task_id: int, connection_id: int) -> Dict[str, Any]:
        """Запускает выполнение задачи резервного копирования PostgreSQL на агенте"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/api/postgres/backup",
                    json={
                        "task_id": task_id,
                        "connection_id": connection_id
                    }
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    return {"success": False, "error": f"HTTP {response.status}"}
        except Exception as e:
            logger.error(f"Error executing PostgreSQL backup on agent {self.ip_address}:{self.port}: {e}")
            return {"success": False, "error": str(e)}
    
    async def execute_postgres_restore(self, task_id: int, connection_id: int, storage_path: str, target_database: Optional[str] = None) -> Dict[str, Any]:
        """Запускает восстановление PostgreSQL на агенте"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/api/postgres/restore",
                    json={
                        "task_id": task_id,
                        "connection_id": connection_id,
                        "storage_path": storage_path,
                        "target_database": target_database
                    }
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    return {"success": False, "error": f"HTTP {response.status}"}
        except Exception as e:
            logger.error(f"Error executing PostgreSQL restore on agent {self.ip_address}:{self.port}: {e}")
            return {"success": False, "error": str(e)}
    
    async def get_storage_space(self, path: str) -> Optional[Dict[str, float]]:
        """Получает информацию о месте на диске для указанного пути"""
        try:
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                async with session.post(
                    f"{self.base_url}/api/storage/space",
                    json={"path": path}
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    return None
        except Exception as e:
            logger.error(f"Error getting storage space from agent {self.ip_address}:{self.port}: {e}")
            return None

