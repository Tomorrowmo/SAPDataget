"""DB 新增表的单元测试 (task_messages / favorites / bw_creds / skill_status / llm_quota)。"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.db import DB


@pytest.fixture
def db(tmp_path: Path) -> DB:
    return DB(tmp_path / "test.sqlite3")


# ============================== task_messages ==============================


def test_task_messages_roundtrip(db: DB):
    task_id = db.create_task(username="alice", source="chat", question="hi")
    db.add_task_message(task_id, role="user", text="how do I query sales?")
    db.add_task_message(task_id, role="assistant", text="I'll look it up",
                        blocks=[{"type": "text", "text": "I'll look it up"}])
    msgs = db.list_task_messages(task_id)
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[1]["blocks"][0]["type"] == "text"


def test_task_messages_cascade_delete(db: DB):
    task_id = db.create_task(username="alice", source="chat")
    db.add_task_message(task_id, role="user", text="x")
    assert db.delete_task(task_id, "alice")
    assert db.list_task_messages(task_id) == []


def test_task_messages_isolation(db: DB):
    t1 = db.create_task(username="alice", source="chat")
    t2 = db.create_task(username="alice", source="chat")
    db.add_task_message(t1, role="user", text="A")
    db.add_task_message(t2, role="user", text="B")
    assert len(db.list_task_messages(t1)) == 1
    assert len(db.list_task_messages(t2)) == 1


# ============================== favorites ==============================


def test_favorites_add_remove(db: DB):
    db.add_favorite("alice", "skill", "monthly_sales")
    assert db.is_favorite("alice", "skill", "monthly_sales")
    db.remove_favorite("alice", "skill", "monthly_sales")
    assert not db.is_favorite("alice", "skill", "monthly_sales")


def test_favorites_idempotent(db: DB):
    db.add_favorite("alice", "task", "t_1")
    db.add_favorite("alice", "task", "t_1")
    assert len(db.list_favorites("alice", "task")) == 1


def test_favorites_per_user_isolation(db: DB):
    db.add_favorite("alice", "skill", "s1")
    db.add_favorite("bob", "skill", "s2")
    a = {f["ref_id"] for f in db.list_favorites("alice")}
    b = {f["ref_id"] for f in db.list_favorites("bob")}
    assert a == {"s1"} and b == {"s2"}


def test_favorites_kind_filter(db: DB):
    db.add_favorite("alice", "skill", "s1")
    db.add_favorite("alice", "task", "t1")
    skills = db.list_favorites("alice", "skill")
    tasks = db.list_favorites("alice", "task")
    assert len(skills) == 1 and len(tasks) == 1
    assert skills[0]["ref_id"] == "s1"
    assert tasks[0]["ref_id"] == "t1"


# ============================== bw_creds ==============================


def test_bw_cred_save_get_delete(db: DB):
    cid = db.save_bw_cred(username="alice", ciphertext=b"abc", nonce=b"123", ttl_seconds=300)
    rec = db.get_bw_cred(cid)
    assert rec is not None
    assert rec["username"] == "alice"
    assert rec["ciphertext"] == b"abc"
    db.delete_bw_cred(cid)
    assert db.get_bw_cred(cid) is None


def test_bw_cred_expired_not_returned(db: DB):
    cid = db.save_bw_cred(username="alice", ciphertext=b"x", nonce=b"y", ttl_seconds=-10)
    assert db.get_bw_cred(cid) is None


def test_bw_cred_cleanup(db: DB):
    db.save_bw_cred(username="alice", ciphertext=b"x", nonce=b"y", ttl_seconds=-10)
    db.save_bw_cred(username="alice", ciphertext=b"x", nonce=b"y", ttl_seconds=-1)
    db.save_bw_cred(username="alice", ciphertext=b"x", nonce=b"y", ttl_seconds=300)
    deleted = db.cleanup_expired_bw_creds()
    assert deleted == 2


# ============================== skill_status ==============================


def test_skill_status_default_active(db: DB):
    assert db.get_skill_status("never_seen", 1) == "active"


def test_skill_status_set_and_get(db: DB):
    db.set_skill_status("monthly_sales", 1, "deprecated", changed_by="admin")
    assert db.get_skill_status("monthly_sales", 1) == "deprecated"
    db.set_skill_status("monthly_sales", 1, "archived", changed_by="admin")
    assert db.get_skill_status("monthly_sales", 1) == "archived"


def test_list_skill_statuses(db: DB):
    db.set_skill_status("a", 1, "active")
    db.set_skill_status("a", 2, "draft")
    db.set_skill_status("b", 1, "deprecated")
    rows = db.list_skill_statuses()
    assert len(rows) == 3


# ============================== llm_quota ==============================


def test_quota_starts_zero(db: DB):
    u = db.get_user_quota_usage("alice", "2026-06")
    assert u == {"input_tokens": 0, "output_tokens": 0, "call_count": 0}


def test_quota_accumulates(db: DB):
    db.add_user_quota_usage("alice", "2026-06", input_tokens=100, output_tokens=50)
    db.add_user_quota_usage("alice", "2026-06", input_tokens=200, output_tokens=20)
    u = db.get_user_quota_usage("alice", "2026-06")
    assert u["input_tokens"] == 300
    assert u["output_tokens"] == 70
    assert u["call_count"] == 2


def test_quota_limit_set_get(db: DB):
    assert db.get_user_quota_limit("alice") is None
    db.set_user_quota_limit("alice", 100000, set_by="admin")
    assert db.get_user_quota_limit("alice") == 100000
    db.set_user_quota_limit("alice", None, set_by="admin")
    assert db.get_user_quota_limit("alice") is None


def test_quota_status_listing(db: DB):
    month = datetime.utcnow().strftime("%Y-%m")
    db.add_user_quota_usage("alice", month, input_tokens=100, output_tokens=50)
    db.add_user_quota_usage("bob", month, input_tokens=5000, output_tokens=500)
    db.set_user_quota_limit("alice", 1000)
    rows = db.list_quota_status()
    assert len(rows) == 2
    # bob has more tokens → first
    assert rows[0]["username"] == "bob"
    assert rows[0]["limit_tokens"] is None
    alice_row = next(r for r in rows if r["username"] == "alice")
    assert alice_row["limit_tokens"] == 1000


# ============================== delete_task ==============================


def test_delete_task_only_by_owner(db: DB):
    tid = db.create_task(username="alice", source="chat")
    # other user can't delete
    assert not db.delete_task(tid, "bob")
    # owner can
    assert db.delete_task(tid, "alice")
    assert db.get_task(tid) is None
