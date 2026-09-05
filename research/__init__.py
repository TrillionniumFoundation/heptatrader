"""Capability-free deterministic research contracts.

The public runner symbols are loaded lazily so ``python -m
research.run_protocol`` does not import the module twice (which would emit a
``runpy`` warning and make strict CI logs noisy).
"""

__all__ = [
    "EventLog",
    "EventRecord",
    "ResearchProtocolError",
    "evaluate_run",
    "validate_run_manifest",
]


def __getattr__(name: str):
    if name in __all__:
        from . import run_protocol

        return getattr(run_protocol, name)
    raise AttributeError(name)
