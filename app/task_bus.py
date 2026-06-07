"""In-memory pub/sub for task progress events (SSE backbone, P1-13).

Lifecycle:
  * 创建任务后,Orchestrator / Agent 在关键节点调用 ``publish(task_id, event)``。
  * 前端通过 SSE 订阅 /api/tasks/{task_id}/stream;后端调 ``subscribe(task_id)``
    拿到一个 Queue,然后逐条 yield。
  * 一旦发布事件 type=='done' 或 'failed',订阅端读到后自然关闭。
  * 没人订阅的任务事件也会缓存最近 100 条 (历史回放)。

不依赖第三方;线程安全 (用 threading.Lock + queue.Queue)。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from queue import Empty, Queue
from typing import Any, Iterator


class TaskBus:
    """Per-task in-memory event bus.

    Single-process. Sufficient for the single-Uvicorn-worker deploy in §12.
    """

    def __init__(self, history_size: int = 100) -> None:
        self._lock = threading.RLock()
        self._subscribers: dict[str, list[Queue[dict[str, Any]]]] = defaultdict(list)
        self._history: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._terminal: set[str] = set()
        self._history_size = history_size

    def publish(self, task_id: str, event: dict[str, Any]) -> None:
        """Push event to all subscribers; cache in history."""
        ev = dict(event)
        ev.setdefault("ts", time.time())
        with self._lock:
            hist = self._history[task_id]
            hist.append(ev)
            if len(hist) > self._history_size:
                hist[:] = hist[-self._history_size :]
            if ev.get("type") in ("done", "failed", "cancelled"):
                self._terminal.add(task_id)
            for q in list(self._subscribers[task_id]):
                try:
                    q.put_nowait(ev)
                except Exception:                                  # noqa: BLE001
                    pass

    def history(self, task_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history.get(task_id, []))

    def is_terminal(self, task_id: str) -> bool:
        with self._lock:
            return task_id in self._terminal

    def subscribe(
        self, task_id: str, *, include_history: bool = True,
    ) -> tuple[Queue[dict[str, Any]], "_Unsubscribe"]:
        q: Queue[dict[str, Any]] = Queue()
        with self._lock:
            if include_history:
                for ev in self._history.get(task_id, []):
                    q.put_nowait(ev)
            self._subscribers[task_id].append(q)
            already_terminal = task_id in self._terminal
        if already_terminal:
            q.put_nowait({"type": "_eof", "ts": time.time()})

        def _unsub() -> None:
            with self._lock:
                subs = self._subscribers.get(task_id, [])
                if q in subs:
                    subs.remove(q)
        return q, _Unsubscribe(_unsub)

    def stream(
        self, task_id: str, *, timeout_seconds: float = 60.0, idle_tick: float = 5.0,
    ) -> Iterator[dict[str, Any]]:
        """Generator yielding events for ``task_id``.

        Stops when:
          * a terminal event (done/failed/cancelled) is received, OR
          * ``timeout_seconds`` of inactivity elapses.

        Idle ticks (``type='keepalive'``) are yielded every ``idle_tick`` seconds
        to keep proxies happy.
        """
        q, unsub = self.subscribe(task_id, include_history=True)
        deadline = time.monotonic() + timeout_seconds
        try:
            while True:
                try:
                    ev = q.get(timeout=idle_tick)
                except Empty:
                    if time.monotonic() >= deadline:
                        yield {"type": "timeout", "ts": time.time()}
                        return
                    yield {"type": "keepalive", "ts": time.time()}
                    continue
                if ev.get("type") == "_eof":
                    return
                yield ev
                # Refresh deadline on any real activity
                deadline = time.monotonic() + timeout_seconds
                if ev.get("type") in ("done", "failed", "cancelled"):
                    return
        finally:
            unsub()


class _Unsubscribe:
    def __init__(self, fn: Any) -> None:
        self._fn = fn

    def __call__(self) -> None:
        self._fn()


# Process-wide singleton; server attaches to STATE.events.
BUS = TaskBus()
