from datetime import datetime, timedelta


def now_ts() -> int:
    return int(datetime.now().timestamp())


def start_of_today_ts() -> int:
    now = datetime.now()
    return int(datetime(now.year, now.month, now.day).timestamp())


def days_ago_ts(days: int) -> int:
    return int((datetime.now() - timedelta(days=days)).timestamp())


def start_of_year_ts() -> int:
    now = datetime.now()
    return int(datetime(now.year, 1, 1).timestamp())


def format_datetime(ts: int | float | None) -> str:
    if not ts:
        return ""
    try:
        dt = datetime.fromtimestamp(ts)
        today = datetime.now().date()
        if dt.date() == today:
            return "今天 " + dt.strftime("%H:%M")
        if dt.date() == today - timedelta(days=1):
            return "昨天 " + dt.strftime("%H:%M")
        if dt.year == datetime.now().year:
            return dt.strftime("%m-%d %H:%M")
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""
