"""In-process event broker backing the Server-Sent-Events endpoint.

Threads publish dictionaries; every connected browser gets its own queue. This
is what makes task progress, library rescans and listening-room state live
without polling or an external message bus.
"""

from __future__ import annotations

import json
import queue
import threading


class Broker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def publish(self, event: dict) -> None:
        payload = dict(event)
        payload.setdefault("ts", __import__("time").time())
        with self._lock:
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(payload)
            except queue.Full:
                pass

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)


broker = Broker()


def publish(name: str, **data) -> None:
    broker.publish({"type": name, **data})


def encode_sse(event: dict) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8")
