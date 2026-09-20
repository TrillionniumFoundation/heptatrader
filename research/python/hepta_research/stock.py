"""Explicit end-labelled seven-field stock bars into the existing replay consumer.

Format reference: HeptaDLL heptaDataFileHelper::ReadheptaStockKindleFile.
This is a new strict reader, not the old permissive runtime parser. It never
carries forward missing prices, infers a period, or authorizes broker orders.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import timedelta
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .legacy import (_decimal, _label, _line, _price_ticks, _sessions, _source,
                     _unsigned, _utc, I64_MAX, MAX_ROWS)
from .model import ReplayBar, number

HEADER = ("DateTime", "Open", "High", "Low", "Close", "Volume", "TurnOver")
MAX_SOURCE_BYTES = 64 * 1024 * 1024


def read_stock_bars(path: Path, sessions_path: Path, *, instrument: str,
                    clock_zone: str, period_seconds: int, tick_size: object,
                    complete_through_us: int, max_bars: int = 100000
                    ) -> tuple[list[ReplayBar], dict]:
    if not isinstance(instrument, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", instrument):
        raise ValueError("instrument identity")
    if type(period_seconds) is not int or not 1 <= period_seconds <= 86400:
        raise ValueError("explicit stock period_seconds in 1..86400 required")
    if type(max_bars) is not int or not 1 <= max_bars <= MAX_ROWS:
        raise ValueError("bar count bound")
    if type(complete_through_us) is not int or not 0 <= complete_through_us <= I64_MAX:
        raise ValueError("explicit source completeness watermark required")
    if not isinstance(clock_zone, str) or not 1 <= len(clock_zone) <= 128:
        raise ValueError("explicit clock zone required")
    zone, scale = ZoneInfo(clock_zone), number(tick_size, positive=True)
    sessions, session_digest = _sessions(sessions_path)
    beginnings = [s[0] for s in sessions]
    bars, attributes, header = [], [], None
    with _source(path) as (source, digest):
        while (fields := _line(source, digest)) is not None:
            if source.tell() > MAX_SOURCE_BYTES or len(fields) != 7:
                raise ValueError("stock input byte/field bound")
            if not bars and header is None and fields[0].casefold() == "datetime":
                if tuple(f.casefold() for f in fields) != tuple(f.casefold() for f in HEADER):
                    raise ValueError("stock header names/order not qualified")
                header = fields
                continue
            if len(bars) >= max_bars:
                raise ValueError("stock bar count bound exceeded")
            closing_clock = _label(fields[0])
            opening_clock = closing_clock - timedelta(seconds=period_seconds)
            begin, begin_offset = _utc(opening_clock, zone)
            end, end_offset = _utc(closing_clock, zone)
            if end-begin != period_seconds*1000000 or begin_offset != end_offset:
                raise ValueError("stock interval crosses a clock-offset transition")
            i = bisect_right(beginnings, begin)-1
            if i < 0 or end > sessions[i][1]:
                raise ValueError("stock bar outside/straddling explicit session")
            if bars and (begin < bars[-1].end_us or not bars[-1].complete):
                raise ValueError("stock overlap/order or data after incomplete bar")
            prices, ticks = zip(*(_price_ticks(v, scale) for v in fields[1:5]))
            opening, high, low, close = prices
            if low <= 0 or low > min(opening, close) or high < max(opening, close):
                raise ValueError("stock positive consistent OHLC required")
            volume, turnover = _unsigned(fields[5]), _decimal(fields[6])
            if turnover < 0:
                raise ValueError("negative stock turnover")
            complete = end <= complete_through_us
            bars.append(ReplayBar(begin, end, opening, close, complete))
            attributes.append({"trading_day": sessions[i][2], "begin_us": begin, "end_us": end,
                               "open_ticks": ticks[0], "high_ticks": ticks[1],
                               "low_ticks": ticks[2], "close_ticks": ticks[3],
                               "volume": volume, "turnover": turnover, "tick_count": None,
                               "open_interest": None, "utc_offset_seconds": begin_offset,
                               "source_close_label": fields[0], "complete": complete})
        if not bars:
            raise ValueError("empty stock bar stream")
        return bars, {"bars_sha256": digest.hexdigest(), "sessions_sha256": session_digest,
                      "instrument": instrument, "bar_count": len(bars), "tick_size": scale,
                      "source_format": "hepta_stock_kindle_csv_7", "header": header,
                      "clock_zone": clock_zone, "timestamp_label": "interval_end",
                      "period_us": period_seconds*1000000, "volume_field": "Volume",
                      "volume_semantics": "per_bar", "complete_through_us": complete_through_us,
                      "source_fields": attributes}
