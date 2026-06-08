"""行级归属过滤 app/rowsec.py 单测。"""
from __future__ import annotations

from app.rowsec import scope_rows_to_user


def test_scopes_by_uname_case_insensitive():
    rows = [
        {"UName": "ADMIN", "x": 1},
        {"UName": "BOB", "x": 2},
        {"UName": " admin ", "x": 3},   # 大小写 + 空格归一化后仍属 admin
    ]
    scoped, applied = scope_rows_to_user(rows, "UName", "admin")
    assert applied is True
    assert [r["x"] for r in scoped] == [1, 3]


def test_field_name_case_insensitive():
    rows = [{"uname": "ADMIN", "x": 1}, {"uname": "BOB", "x": 2}]
    scoped, applied = scope_rows_to_user(rows, "UName", "Admin")
    assert applied is True
    assert [r["x"] for r in scoped] == [1]


def test_no_owner_field_is_noop():
    rows = [{"Region": "HD", "x": 1}, {"Region": "HN", "x": 2}]
    scoped, applied = scope_rows_to_user(rows, "UName", "admin")
    assert applied is False
    assert scoped == rows


def test_empty_rows_noop():
    scoped, applied = scope_rows_to_user([], "UName", "admin")
    assert scoped == [] and applied is False


def test_user_has_no_rows():
    rows = [{"UName": "ALICE"}, {"UName": "BOB"}]
    scoped, applied = scope_rows_to_user(rows, "UName", "carol")
    assert applied is True
    assert scoped == []


def test_configurable_owner_field():
    rows = [{"ERNAM": "ADMIN", "x": 1}, {"ERNAM": "BOB", "x": 2}]
    scoped, applied = scope_rows_to_user(rows, "ERNAM", "admin")
    assert applied is True and [r["x"] for r in scoped] == [1]
