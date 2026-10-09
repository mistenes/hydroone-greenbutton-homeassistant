"""Choose complete Toronto calendar days, including gaps after downtime."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


def export_window(today: date, readings: dict[str, str], history_days: int, lookback_days: int):
    end = today - timedelta(days=1)
    if readings:
        newest = datetime.fromtimestamp(max(map(int, readings)), ZoneInfo("America/Toronto")).date()
        start = min(end - timedelta(days=lookback_days - 1), newest - timedelta(days=1))
    else:
        start = end - timedelta(days=history_days - 1)
    try:
        oldest_available = today.replace(year=today.year - 2)
    except ValueError:
        oldest_available = today.replace(year=today.year - 2, day=28)
    return max(start, oldest_available), end
