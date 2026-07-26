"""Accelerated simulated clock.

A stall only exists once time has passed, and a three-minute demo has none to
spare. Everything in the app reads time from here so the scheduler can be real
while the calendar runs fast. Nothing else may call datetime.now().
"""
import os
from datetime import datetime, timedelta

SCALE = float(os.getenv("CLOCK_SCALE", "900"))

_real_start = datetime.now()
_sim_start = None


def _default_sim_start() -> datetime:
    hh, _, mm = os.getenv("SIM_START", "13:45").partition(":")
    return datetime.now().replace(
        hour=int(hh), minute=int(mm or 0), second=0, microsecond=0
    )


def reset(sim_start: datetime | None = None) -> None:
    global _real_start, _sim_start
    _real_start = datetime.now()
    _sim_start = sim_start or _default_sim_start()


def now() -> datetime:
    if _sim_start is None:
        reset()
    elapsed = (datetime.now() - _real_start).total_seconds()
    return _sim_start + timedelta(seconds=elapsed * SCALE)


def iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


def parse(s: str) -> datetime:
    return datetime.fromisoformat(s)


def now_iso() -> str:
    return iso(now())
