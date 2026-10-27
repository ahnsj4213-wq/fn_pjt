"""Server clock helpers. All timestamps are UTC; posting deadlines are entered as KST dates."""
from datetime import UTC, date, datetime, time, timedelta, timezone

KST = timezone(timedelta(hours=9))


def utcnow() -> datetime:
    return datetime.now(UTC)


def kst_end_of_day(d: date) -> datetime:
    """A deadline date means the end of that day in Korea (23:59:59 KST)."""
    return datetime.combine(d, time(23, 59, 59), tzinfo=KST).astimezone(UTC)


def to_kst_date(dt: datetime | None) -> date | None:
    return dt.astimezone(KST).date() if dt else None
