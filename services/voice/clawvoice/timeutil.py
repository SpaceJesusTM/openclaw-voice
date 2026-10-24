"""IANA-timezone helpers. All civil dates and spoken times are agent-local."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


class Clock:
    """Wall clock; replaced by FrozenClock in tests."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        import time as _time

        return _time.monotonic()


class FrozenClock(Clock):
    def __init__(self, at: datetime):
        self.at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        self._mono = 1000.0

    def now(self) -> datetime:
        return self.at

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)
        self._mono += seconds


SYSTEM_CLOCK = Clock()


def zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def local_now(tz: ZoneInfo, clock: Clock = SYSTEM_CLOCK) -> datetime:
    return clock.now().astimezone(tz)


def resolve_day(spec: str, tz: ZoneInfo, clock: Clock = SYSTEM_CLOCK) -> date:
    """today | tomorrow | yesterday | +N | YYYY-MM-DD in the agent's zone."""
    today = local_now(tz, clock).date()
    key = str(spec).strip().lower()
    if key == "today":
        return today
    if key == "tomorrow":
        return today + timedelta(days=1)
    if key == "yesterday":
        return today - timedelta(days=1)
    if key.startswith("+") and key[1:].isdigit():
        return today + timedelta(days=int(key[1:]))
    return date.fromisoformat(key)


def next_local_midnight(tz: ZoneInfo, clock: Clock = SYSTEM_CLOCK) -> datetime:
    """The next local 00:00 as an aware instant (23/25-hour DST days included)."""
    today = local_now(tz, clock).date()
    return datetime.combine(today + timedelta(days=1), time(0), tz)


def spoken_clock(value: datetime | time) -> str:
    hour, minute = value.hour, value.minute
    suffix = "AM" if hour < 12 else "PM"
    label = hour % 12 or 12
    return f"{label}:{minute:02d} {suffix}" if minute else f"{label} {suffix}"


def spoken_span(start: datetime | time, end: datetime | time) -> str:
    a, b = spoken_clock(start), spoken_clock(end)
    if a[-2:] == b[-2:]:
        return f"{a[:-3]} to {b}"
    return f"{a} to {b}"


def minutes_label(minutes: int) -> str:
    """Planner minutes-from-midnight (0..1440) as a spoken clock time."""
    minutes = max(0, min(1440, int(minutes)))
    if minutes == 1440:
        return "midnight"
    return spoken_clock(time(minutes // 60, minutes % 60))


def minutes_span(start: int, end: int) -> str:
    if end >= 1440:
        return f"{minutes_label(start)} to midnight"
    return spoken_span(time(start // 60, start % 60), time(end // 60, end % 60))


def hhmm(minutes: int) -> str:
    minutes = max(0, min(1440, int(minutes)))
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def day_label(day: date, tz: ZoneInfo, clock: Clock = SYSTEM_CLOCK) -> str:
    today = local_now(tz, clock).date()
    name = f"{day.strftime('%A')}, {day.strftime('%B')} {day.day}"
    delta = (day - today).days
    if delta == 0:
        return f"today, {name}"
    if delta == 1:
        return f"tomorrow, {name}"
    if delta == -1:
        return f"yesterday, {name}"
    return name


def parse_instant(value: str) -> datetime:
    """Parse an offset-bearing ISO 8601 timestamp; naive values are rejected."""
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp needs an explicit UTC offset")
    return parsed


def age_phrase(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return "just now" if seconds < 10 else f"{seconds} seconds ago"
    minutes = seconds // 60
    if minutes < 90:
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = minutes // 60
    return f"about {hours} hour{'s' if hours != 1 else ''} ago"
