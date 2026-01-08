"""
Генератор отчетов для отправки в Mattermost
"""
import logging
from typing import Dict, Any, List
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models import Agent, PostgresBackupTask, BackupTask, BackupHistory, PostgresBackupHistory, AgentStatus

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Генератор отчетов"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def generate_report(self, agent_ids: List[int], postgres_task_ids: List[int]) -> str:
        """Генерирует текстовый отчет для Mattermost"""
        report_lines = []
        report_lines.append("## 📊 Отчет о резервном копировании\n")
        report_lines.append(f"**Дата формирования:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        
        # Информация об агентах
        if agent_ids:
            report_lines.append("### 🤖 Агенты\n")
            result_agents = await self.db.execute(
                select(Agent).where(Agent.id.in_(agent_ids))
            )
            agents = result_agents.scalars().all()
            
            for agent in agents:
                result_status = await self.db.execute(
                    select(AgentStatus).where(AgentStatus.agent_id == agent.id)
                )
                status = result_status.scalar_one_or_none()
                
                report_lines.append(f"**{agent.name}** ({agent.ip_address})")
                if status:
                    report_lines.append(f"- Статус: {'🟢 Онлайн' if status.is_online else '🔴 Офлайн'}")
                    if status.disk_total_gb:
                        disk_used = status.disk_total_gb - status.disk_free_gb
                        disk_percent = (disk_used / status.disk_total_gb) * 100
                        report_lines.append(f"- Диск: {disk_used:.2f} / {status.disk_total_gb:.2f} GB ({disk_percent:.1f}%)")
                    if status.memory_total_mb:
                        memory_used = status.memory_total_mb - status.memory_free_mb
                        memory_percent = (memory_used / status.memory_total_mb) * 100
                        report_lines.append(f"- Память: {memory_used:.2f} / {status.memory_total_mb:.2f} MB ({memory_percent:.1f}%)")
                    if status.cpu_load_percent:
                        report_lines.append(f"- CPU: {status.cpu_load_percent:.1f}%")
                else:
                    report_lines.append("- Статус: ⚠️ Неизвестно")
                report_lines.append("")
        
        # Информация о задачах бэкапа папок
        if agent_ids:
            report_lines.append("### 📁 Задачи резервного копирования папок\n")
            result_tasks = await self.db.execute(
                select(BackupTask).where(BackupTask.agent_id.in_(agent_ids))
            )
            tasks = result_tasks.scalars().all()
            
            if tasks:
                for task in tasks:
                    report_lines.append(f"**{task.name}**")
                    report_lines.append(f"- Путь: `{task.source_path}`")
                    report_lines.append(f"- Статус: {'✅ Активна' if task.is_active else '❌ Неактивна'}")
                    if task.last_status:
                        status_icon = "✅" if task.last_status == "success" else "❌" if task.last_status == "error" else "⏳"
                        report_lines.append(f"- Последний статус: {status_icon} {task.last_status}")
                    if task.last_run:
                        report_lines.append(f"- Последний запуск: {task.last_run.strftime('%Y-%m-%d %H:%M:%S')}")
                    report_lines.append("")
            else:
                report_lines.append("Нет задач для выбранных агентов\n")
        
        # Информация о PostgreSQL задачах
        if postgres_task_ids:
            report_lines.append("### 🗄️ Задачи резервного копирования PostgreSQL\n")
            result_pg_tasks = await self.db.execute(
                select(PostgresBackupTask).where(PostgresBackupTask.id.in_(postgres_task_ids))
            )
            pg_tasks = result_pg_tasks.scalars().all()
            
            if pg_tasks:
                for task in pg_tasks:
                    report_lines.append(f"**{task.name}**")
                    report_lines.append(f"- База данных: `{task.database}`")
                    report_lines.append(f"- Хост: {task.host}:{task.port}")
                    report_lines.append(f"- Статус: {'✅ Активна' if task.is_active else '❌ Неактивна'}")
                    if task.last_status:
                        status_icon = "✅" if task.last_status == "success" else "❌" if task.last_status == "error" else "⏳"
                        report_lines.append(f"- Последний статус: {status_icon} {task.last_status}")
                    if task.last_run:
                        report_lines.append(f"- Последний запуск: {task.last_run.strftime('%Y-%m-%d %H:%M:%S')}")
                    report_lines.append("")
            else:
                report_lines.append("Нет задач для выбранных СУБД\n")
        
        # Статистика за последние 24 часа
        report_lines.append("### 📈 Статистика за последние 24 часа\n")
        cutoff_time = datetime.utcnow() - timedelta(hours=24)
        
        # Статистика бэкапов папок
        if agent_ids:
            result_history = await self.db.execute(
                select(BackupHistory)
                .join(BackupTask)
                .where(BackupTask.agent_id.in_(agent_ids))
                .where(BackupHistory.started_at >= cutoff_time)
            )
            folder_backups = result_history.scalars().all()
            success_count = len([b for b in folder_backups if b.status == "success"])
            error_count = len([b for b in folder_backups if b.status == "error"])
            report_lines.append(f"- Бэкапы папок: ✅ {success_count} успешных, ❌ {error_count} ошибок")
        
        # Статистика PostgreSQL бэкапов
        if postgres_task_ids:
            result_pg_history = await self.db.execute(
                select(PostgresBackupHistory)
                .where(PostgresBackupHistory.task_id.in_(postgres_task_ids))
                .where(PostgresBackupHistory.started_at >= cutoff_time)
            )
            pg_backups = result_pg_history.scalars().all()
            success_count = len([b for b in pg_backups if b.status == "success"])
            error_count = len([b for b in pg_backups if b.status == "error"])
            report_lines.append(f"- Бэкапы PostgreSQL: ✅ {success_count} успешных, ❌ {error_count} ошибок")
        
        report_lines.append("\n---")
        report_lines.append(f"*Сгенерировано системой резервного копирования*")
        
        return "\n".join(report_lines)




