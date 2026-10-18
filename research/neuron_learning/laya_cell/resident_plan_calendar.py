"""Exact regular FX intervals for new forward-plan admission only."""
from collections.abc import Mapping
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
from .market_session import PROFILE, session_clock

ADMISSION_PROFILE = "NEW_FORWARD_PLAN_DECLARED_WINDOW_V1"
NY = ZoneInfo("America/New_York")
DAY_MS = 86400000


def calendar_admission(candidate, plan, now_ms, *, clock=session_clock):
    """The caller supplies the final proposal from the existing rule builder."""
    if not isinstance(candidate, Mapping) or not isinstance(plan, Mapping):
        raise ValueError("FORWARD_CANDIDATE_AND_PLAN_REQUIRED")
    window = candidate.get("window")
    if not isinstance(window, Mapping):
        raise ValueError("FORWARD_CANDIDATE_WINDOW_REQUIRED")

    def integer(obj, key):
        value = obj.get(key)
        if type(value) is not int or value < 0:
            raise ValueError("FORWARD_INTEGER_REQUIRED:" + key)
        return value

    def at(value):
        try:
            return datetime.fromtimestamp(value / 1000, timezone.utc)
        except (OverflowError, OSError, ValueError):
            raise ValueError("FORWARD_DATETIME_RANGE") from None

    now = integer({"now_ms": now_ms}, "now_ms")
    created = integer(candidate, "created_at_ms")
    trained = integer(candidate, "trained_until_ms")
    cutoff = integer(window, "training_end_ms")
    vstart = integer(window, "validation_start_ms")
    vend = integer(window, "validation_end_ms")
    plan_created = integer(plan, "created_at_ms")
    start = integer(plan, "start_ms")
    end = integer(plan, "outcome_end_ms")
    if not plan_created == now < start < end:
        raise ValueError("FORWARD_PROPOSAL_CLOCK")
    if not cutoff < vstart < vend:
        raise ValueError("FORWARD_CANDIDATE_WINDOW_ORDER")
    if max(created, trained, cutoff) > now:
        raise ValueError("FORWARD_CANDIDATE_LOOKAHEAD")
    if max(vend, end) - now > 7 * DAY_MS:
        raise ValueError("FORWARD_CALENDAR_SEARCH_SEVEN_DAY_BOUND")
    if "rule" not in plan or plan["rule"] is None:
        raise ValueError("FORWARD_VALIDATED_RULE_REQUIRED")
    length = end - start
    lead = start - now

    def is_open(value):
        state = clock(at(value))
        key = "calendar_regular_session_open"
        if not isinstance(state, Mapping) or type(state.get(key)) is not bool:
            raise ValueError("FORWARD_CALENDAR_STATE_INVALID")
        return state[key]

    def merge(intervals):
        result = []
        for lo, hi in sorted(intervals):
            if result and lo <= result[-1][1] + 1:
                result[-1][1] = max(result[-1][1], hi)
            else:
                result.append([lo, hi])
        return result

    current_open = is_open(now)
    open_intervals = []
    if vend > now:
        cuts = {now, vend + 1}
        day = at(now).astimezone(NY).date()
        last_day = at(vend).astimezone(NY).date()
        while True:
            # These are all possible transitions of the pinned regular-hours
            # profile; the existing calendar decides each resulting interval.
            for minute in (0, 15):
                boundary = int(datetime.combine(
                    day, time(17, minute), tzinfo=NY).timestamp() * 1000)
                if now < boundary < vend + 1:
                    cuts.add(boundary)
            if day == last_day:
                break
            day += timedelta(days=1)
        points = sorted(cuts)
        for left, right in zip(points, points[1:]):
            if is_open((left + right - 1) // 2):
                open_intervals.append([left, right - 1])
        open_intervals = merge(open_intervals)

    feasible = []
    for open_start, open_end in open_intervals:
        lower = max(open_start, vstart, created + 1, trained + 1,
                    cutoff + 1, now + lead)
        upper = min(open_end, vend) - length
        if lower > upper:
            continue
        # Keep the original lead: creation itself must also be in an open
        # session, and fresh original input is separately required at that time.
        for creation_start, creation_end in open_intervals:
            lo = max(lower, creation_start + lead)
            hi = min(upper, creation_end + lead)
            if lo <= hi:
                feasible.append([lo, hi])
    feasible = merge(feasible)
    ready = current_open and any(lo <= start <= hi for lo, hi in feasible)
    if ready:
        state, reason = "READY", "CONTINUOUS_OPEN_WINDOW"
    elif feasible:
        state = "DEFERRED"
        reason = ("MARKET_SESSION_CLOSED" if not current_open
                  else "CURRENT_PLAN_OUTSIDE_FEASIBLE_WINDOW")
    else:
        state = "NO_FORWARD_TRIAL"
        reason = ("CANDIDATE_WINDOW_EXPIRED" if vend <= now
                  else "NO_CONTINUOUS_OPEN_WINDOW_FITS")
    return {
        "profile": ADMISSION_PROFILE, "calendar_profile": PROFILE,
        "ready": ready, "state": state, "reason": reason, "now_ms": now,
        "calendar_regular_session_open": current_open,
        "candidate_window_ms": [vstart, vend],
        "plan_interval_ms": [start, end], "lead_ms": lead, "length_ms": length,
        "earliest_feasible_start_ms": feasible[0][0] if feasible else None,
        "latest_feasible_start_ms": feasible[-1][1] if feasible else None,
        "open_intervals_ms": open_intervals,
        "feasible_start_intervals_ms": feasible,
        "future_data_availability_proven": False,
        "calendar_holidays_and_scheduled_outages_not_proven": True,
    }
