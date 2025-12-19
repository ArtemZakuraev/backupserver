"""
Утилита для конвертации человекочитаемых параметров расписания в cron-формат
"""
from typing import Optional, List


def convert_to_cron(
    schedule_type: str,
    hour: Optional[int] = None,
    minute: Optional[int] = None,
    day_of_week: Optional[int] = None
) -> str:
    """
    Конвертирует человекочитаемые параметры в cron-формат
    
    Args:
        schedule_type: Тип расписания
            - "daily": Каждый день в выбранное время
            - "weekly": Раз в неделю в выбранный день и время
            - "hourly": Каждый час в выбранную минуту
            - "minutely": Каждую минуту
        hour: Час (0-23), обязателен для daily и weekly
        minute: Минута (0-59), обязательна для daily, weekly и hourly
        day_of_week: День недели (0-6, где 0=воскресенье, 1=понедельник), обязателен для weekly
    
    Returns:
        Cron-выражение в формате "минута час день месяц день_недели"
    
    Examples:
        convert_to_cron("daily", hour=2, minute=0) -> "0 2 * * *"
        convert_to_cron("weekly", hour=3, minute=0, day_of_week=1) -> "0 3 * * 1"
        convert_to_cron("hourly", minute=15) -> "15 * * * *"
        convert_to_cron("minutely") -> "* * * * *"
    """
    if schedule_type == "minutely":
        return "* * * * *"
    
    if schedule_type == "hourly":
        if minute is None:
            minute = 0
        return f"{minute} * * * *"
    
    if schedule_type == "daily":
        if hour is None:
            hour = 0
        if minute is None:
            minute = 0
        return f"{minute} {hour} * * *"
    
    if schedule_type == "weekly":
        if hour is None:
            hour = 0
        if minute is None:
            minute = 0
        if day_of_week is None:
            day_of_week = 0
        # Cron использует 0-6, где 0=воскресенье, 1=понедельник и т.д.
        return f"{minute} {hour} * * {day_of_week}"
    
    raise ValueError(f"Unknown schedule type: {schedule_type}")


def parse_cron_to_human(cron: str) -> dict:
    """
    Парсит cron-выражение обратно в человекочитаемый формат (если возможно)
    
    Args:
        cron: Cron-выражение
    
    Returns:
        Словарь с параметрами расписания или None, если не удалось распарсить
    """
    parts = cron.strip().split()
    if len(parts) != 5:
        return None
    
    minute, hour, day, month, day_of_week = parts
    
    # Каждую минуту
    if minute == "*" and hour == "*" and day == "*" and month == "*" and day_of_week == "*":
        return {"schedule_type": "minutely"}
    
    # Каждый час
    if minute != "*" and hour == "*" and day == "*" and month == "*" and day_of_week == "*":
        try:
            return {
                "schedule_type": "hourly",
                "minute": int(minute)
            }
        except ValueError:
            return None
    
    # Каждый день
    if minute != "*" and hour != "*" and day == "*" and month == "*" and day_of_week == "*":
        try:
            return {
                "schedule_type": "daily",
                "hour": int(hour),
                "minute": int(minute)
            }
        except ValueError:
            return None
    
    # Раз в неделю
    if minute != "*" and hour != "*" and day == "*" and month == "*" and day_of_week != "*":
        try:
            return {
                "schedule_type": "weekly",
                "hour": int(hour),
                "minute": int(minute),
                "day_of_week": int(day_of_week)
            }
        except ValueError:
            return None
    
    return None


def get_day_of_week_options() -> List[dict]:
    """Возвращает список дней недели для выбора"""
    return [
        {"value": 0, "label": "Воскресенье"},
        {"value": 1, "label": "Понедельник"},
        {"value": 2, "label": "Вторник"},
        {"value": 3, "label": "Среда"},
        {"value": 4, "label": "Четверг"},
        {"value": 5, "label": "Пятница"},
        {"value": 6, "label": "Суббота"},
    ]




