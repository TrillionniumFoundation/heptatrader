"""Cooperative fixed-slot read deadlines; no source or admission authority."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import time

from .integrity import ContractError


class SourceReadDeadline(ContractError):
    """A read must stop without returning a partially verified snapshot."""


_active = ContextVar("laya_source_read_deadline", default=None)


def check_source_budget():
    budget = _active.get()
    if budget is not None:
        wall_ms, monotonic_end = budget
        if time.time() * 1000 > wall_ms or time.monotonic() > monotonic_end:
            raise SourceReadDeadline("INPUT_SLOT_EXPIRED:DURING_QUOTE_READ")


@contextmanager
def source_read_budget(deadline_ms=None):
    if deadline_ms is None:
        yield
        return
    if type(deadline_ms) is not int or deadline_ms <= 0:
        raise ContractError("SOURCE_READ_DEADLINE_INVALID")
    remaining = (deadline_ms - time.time() * 1000) / 1000
    end = time.monotonic() + max(0, remaining)
    parent = _active.get()
    if parent is not None:
        deadline_ms, end = min(deadline_ms, parent[0]), min(end, parent[1])
    token = _active.set((deadline_ms, end))
    try:
        check_source_budget()
        yield
        check_source_budget()
    finally:
        _active.reset(token)
