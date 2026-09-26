"""Async pub/sub event bus feeding the SSE endpoints.

Each subscriber gets its own bounded queue. Publishing never blocks: a full
subscriber queue drops its oldest item to make room for the new one. `job`
progress ticks can be throttled per job_id to at most one per second; status
changes (or any other event) should be published without a throttle key so
they are never dropped by the throttle.
"""
from __future__ import annotations

import asyncio
import time
from typing import Optional

from mytts.contracts import Event

THROTTLE_S = 1.0
_QUEUE_SIZE = 200


class EventBus:
    def __init__(self, queue_size: int = _QUEUE_SIZE):
        self._queue_size = queue_size
        self._subscribers: dict[int, "asyncio.Queue[Event]"] = {}
        self._next_id = 0
        self._last_emit: dict[str, float] = {}

    def subscribe(self) -> tuple[int, "asyncio.Queue[Event]"]:
        sid = self._next_id
        self._next_id += 1
        q: "asyncio.Queue[Event]" = asyncio.Queue(self._queue_size)
        self._subscribers[sid] = q
        return sid, q

    def unsubscribe(self, sid: int) -> None:
        self._subscribers.pop(sid, None)

    def publish(self, event: Event, *, throttle_key: Optional[str] = None) -> None:
        if throttle_key is not None:
            now = time.monotonic()
            last = self._last_emit.get(throttle_key)
            if last is not None and now - last < THROTTLE_S:
                return
            self._last_emit[throttle_key] = now
        for q in list(self._subscribers.values()):
            self._put_dropping_oldest(q, event)

    @staticmethod
    def _put_dropping_oldest(q: "asyncio.Queue[Event]", event: Event) -> None:
        try:
            q.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass  # lost a race with another producer; drop silently
