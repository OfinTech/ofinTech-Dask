"""In-process event hub: agent runs publish events per thread; the app subscribes with a
streaming GET. A subscriber that connects mid-run first receives the events of the current run."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class EventHub:
    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)
        self._replay: dict[str, list[dict[str, Any]]] = {}

    def start_run(self, channel: str) -> None:
        self._replay[channel] = []

    def end_run(self, channel: str) -> None:
        self._replay.pop(channel, None)

    def publish(self, channel: str, event: dict[str, Any]) -> None:
        buf = self._replay.get(channel)
        if buf is not None:
            buf.append(event)
            if len(buf) > 5000:
                del buf[:1000]
        for q in list(self._subs.get(channel, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:  # slow consumer: drop it, it will reconnect and reload
                self._subs[channel].discard(q)

    def subscribe(self, channel: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=10000)
        for ev in self._replay.get(channel, []):
            q.put_nowait(ev)
        self._subs[channel].add(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        self._subs[channel].discard(q)
        if not self._subs[channel]:
            self._subs.pop(channel, None)
