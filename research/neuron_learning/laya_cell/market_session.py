"""Shared IB regular FX clock. Diagnostic calendar, never quote authority."""
from datetime import datetime, time as wall_time, timedelta, timezone
from zoneinfo import ZoneInfo

PROFILE = "IBKR_IDEALPRO_REGULAR_HOURS_SUN_1715_FRI_1700_NY_V1"
OFFICIAL_HOURS_URL = "https://www.interactivebrokers.co.uk/en/trading/productsprint.php"
NEW_YORK = ZoneInfo("America/New_York")

def _until_next_regular_open(ny: datetime) -> datetime | None:
    """Compute next regular *calendar* opening only when currently closed."""
    day = ny.weekday()
    t = ny.time().replace(tzinfo=None)
    if day == 5:  # Saturday
        return datetime.combine((ny + timedelta(days=1)).date(),
                                wall_time(17, 15), NEW_YORK)
    if day == 6 and t < wall_time(17, 15):  # Sunday
        return datetime.combine(ny.date(), wall_time(17, 15), NEW_YORK)
    if day == 4 and t >= wall_time(17):  # Friday after 17:00
        return datetime.combine((ny + timedelta(days=2)).date(),
                                wall_time(17, 15), NEW_YORK)
    if day in (0, 1, 2, 3) and wall_time(17) <= t < wall_time(17, 15):
        return datetime.combine(ny.date(), wall_time(17, 15), NEW_YORK)
    return None


def session_clock(when: datetime) -> dict:
    """DST-aware weekly clock profile; never infers a real quote."""
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("TIMEZONE_AWARE_WALL_CLOCK_REQUIRED")
    utc = when.astimezone(timezone.utc)
    ny = utc.astimezone(NEW_YORK)
    next_open = _until_next_regular_open(ny)
    if next_open is None:
        classification = "WITHIN_REGULAR_CALENDAR_WINDOW_NOT_MARKET_AUTHORITY"
    elif ny.weekday() in (5, 6, 4):
        classification = "OUTSIDE_REGULAR_FX_WEEKLY_SESSION"
    else:
        classification = "FX_DAILY_ROLLOVER_GAP"
    return {
        "session_profile": PROFILE,
        "official_hours_url": OFFICIAL_HOURS_URL,
        "checked_at_utc": utc.isoformat(timespec="milliseconds"),
        "checked_at_new_york": ny.isoformat(timespec="milliseconds"),
        "calendar_regular_session_open": next_open is None,
        "classification": classification,
        "next_regular_calendar_open_utc":
            next_open.astimezone(timezone.utc).isoformat(timespec="milliseconds")
            if next_open else None,
        "next_regular_calendar_open_new_york":
            next_open.isoformat(timespec="milliseconds") if next_open else None,
        "calendar_holidays_and_scheduled_outages_not_proven": True,
        "realtime_quote_subscription_admitted_by_calendar": False,
        "trade_authority": False,
    }

