"""Exact, finite UTC systemd calendar terms for a frozen half-open interval.

Only integer UTC seconds on the Unix-epoch grid are scheduled: start_ms <= t <
end_ms and t % (step_seconds * 1000) == 0. No grace or final closeout is added.
The caller owns systemd installation and any explicit deadline observation.
"""
from datetime import datetime, timedelta, timezone

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
MAX_TERMS = 64


def _field(values, width=2):
    values = sorted(set(values))
    parts = []
    first = previous = values[0]
    for value in values[1:] + [None]:
        if value is not None and value == previous + 1:
            previous = value
            continue
        left, right = f'{first:0{width}d}', f'{previous:0{width}d}'
        parts.append(left if first == previous else left + '..' + right)
        first = previous = value
    return ','.join(parts)


def calendar_terms(start_ms, end_ms, step_seconds=1):
    """Return <=64 exact finite OnCalendar strings, or raise ValueError.

    step_seconds=1 means UTC whole seconds; 60 means UTC whole minutes.
    Arbitrary positive integer grids are supported when representable in the
    term budget. Compression merges identical second sets across minutes,
    identical minute/second sets across hours, then matching times across dates
    of the same month. Each merge is a Cartesian product of identical sets.
    """
    if (type(start_ms) is not int or type(end_ms) is not int or type(step_seconds) is not int
            or step_seconds <= 0 or end_ms < start_ms):
        raise ValueError('INTEGER_HALF_OPEN_INTERVAL_AND_POSITIVE_STEP_REQUIRED')
    if start_ms == end_ms:
        return []
    quantum_ms = step_seconds * 1000
    first = ((start_ms + quantum_ms - 1) // quantum_ms) * step_seconds
    last = ((end_ms - 1) // quantum_ms) * step_seconds
    if first > last:
        return []
    groups = {}
    day_start = (first // 86400) * 86400
    while day_start <= last:
        try:
            date = _EPOCH + timedelta(seconds=day_start)
        except OverflowError as exc:
            raise ValueError('CALENDAR_DATE_OUTSIDE_SUPPORTED_RANGE') from exc
        hours = {}
        for hour in range(24):
            minutes = {}
            for minute in range(60):
                base = day_start + hour * 3600 + minute * 60
                low, high = max(first, base), min(last, base + 59)
                aligned = ((low + step_seconds - 1) // step_seconds) * step_seconds
                if aligned <= high:
                    seconds = tuple(range(aligned - base, high - base + 1, step_seconds))
                    minutes.setdefault(seconds, []).append(minute)
            for seconds, minute_values in minutes.items():
                hours.setdefault((tuple(minute_values), seconds), []).append(hour)
        for (minutes, seconds), hour_values in hours.items():
            key = (date.year, date.month, tuple(hour_values), minutes, seconds)
            groups.setdefault(key, []).append(date.day)
        if len(groups) > MAX_TERMS:
            raise ValueError('EXACT_CALENDAR_REQUIRES_MORE_THAN_64_TERMS')
        # Jump directly to the next day containing a grid point; sparse grids
        # never require scanning every intervening empty date.
        next_point = ((day_start + 86400 + step_seconds - 1) // step_seconds) * step_seconds
        day_start = (next_point // 86400) * 86400
    result = []
    for (year, month, hours, minutes, seconds), days in sorted(groups.items(),
            key=lambda item: (item[0][0], item[0][1], min(item[1]), item[0][2], item[0][3], item[0][4])):
        result.append(f'{year:04d}-{month:02d}-{_field(days)} '
                      f'{_field(hours)}:{_field(minutes)}:{_field(seconds)} UTC')
    return result
