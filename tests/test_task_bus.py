"""TaskBus pub/sub tests."""
from __future__ import annotations

import threading
import time
from queue import Empty

from app.task_bus import TaskBus


def test_publish_buffers_history():
    bus = TaskBus()
    bus.publish("t1", {"type": "progress", "step": "load"})
    bus.publish("t1", {"type": "progress", "step": "query"})
    hist = bus.history("t1")
    assert [h["step"] for h in hist] == ["load", "query"]


def test_late_subscriber_replays_history():
    bus = TaskBus()
    bus.publish("t1", {"type": "progress", "step": "a"})
    bus.publish("t1", {"type": "progress", "step": "b"})
    q, _unsub = bus.subscribe("t1", include_history=True)
    got = []
    for _ in range(2):
        got.append(q.get_nowait()["step"])
    assert got == ["a", "b"]


def test_subscriber_without_history():
    bus = TaskBus()
    bus.publish("t1", {"type": "progress", "step": "a"})
    q, _ = bus.subscribe("t1", include_history=False)
    try:
        q.get_nowait()
        assert False, "should have been empty"
    except Empty:
        pass


def test_terminal_event_marks_task():
    bus = TaskBus()
    bus.publish("t1", {"type": "done"})
    assert bus.is_terminal("t1")
    # Late subscriber to a terminal task should receive history + EOF marker
    q, _ = bus.subscribe("t1")
    items: list[dict] = []
    while not q.empty():
        items.append(q.get_nowait())
    assert any(i["type"] == "done" for i in items)
    assert any(i["type"] == "_eof" for i in items)


def test_publish_broadcasts_to_all_subscribers():
    bus = TaskBus()
    q1, _ = bus.subscribe("t1", include_history=False)
    q2, _ = bus.subscribe("t1", include_history=False)
    bus.publish("t1", {"type": "progress", "step": "x"})
    assert q1.get(timeout=1)["step"] == "x"
    assert q2.get(timeout=1)["step"] == "x"


def test_stream_yields_until_terminal():
    bus = TaskBus()

    def producer():
        time.sleep(0.05)
        bus.publish("t1", {"type": "progress", "step": "a"})
        time.sleep(0.05)
        bus.publish("t1", {"type": "done"})

    threading.Thread(target=producer, daemon=True).start()
    events = list(bus.stream("t1", timeout_seconds=5, idle_tick=0.01))
    # 至少包含 progress 和 done(可能夹着 keepalive)
    types = [e["type"] for e in events]
    assert "progress" in types
    assert types[-1] == "done"


def test_unsubscribe_removes_queue():
    bus = TaskBus()
    q, unsub = bus.subscribe("t1")
    unsub()
    bus.publish("t1", {"type": "progress"})
    # Should NOT receive anything (queue already removed from subscribers)
    try:
        q.get(timeout=0.1)
        assert False, "should not have received"
    except Empty:
        pass


def test_history_cap():
    bus = TaskBus(history_size=3)
    for i in range(10):
        bus.publish("t", {"type": "p", "i": i})
    hist = bus.history("t")
    assert len(hist) == 3
    assert [h["i"] for h in hist] == [7, 8, 9]
