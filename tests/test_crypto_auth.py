"""Crypto + auth credential persistence tests."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from app import auth
from app.crypto import (
    decrypt,
    encrypt,
    generate_key_base64,
    reset_key_cache,
)
from app.db import DB


@pytest.fixture(autouse=True)
def _isolated_key(monkeypatch):
    monkeypatch.setenv("BW_CRED_KEY", generate_key_base64())
    reset_key_cache()
    yield
    reset_key_cache()


def test_encrypt_decrypt_roundtrip():
    ct, nonce = encrypt("hello world")
    assert decrypt(ct, nonce) == b"hello world"


def test_encrypt_with_aad_required_to_decrypt():
    ct, nonce = encrypt("secret", aad=b"alice")
    assert decrypt(ct, nonce, aad=b"alice") == b"secret"
    with pytest.raises(Exception):
        decrypt(ct, nonce, aad=b"bob")


def test_encrypt_each_call_uses_fresh_nonce():
    a_ct, a_nonce = encrypt("x")
    b_ct, b_nonce = encrypt("x")
    assert a_nonce != b_nonce
    assert a_ct != b_ct


def test_generate_key_is_32_bytes():
    import base64
    k = generate_key_base64()
    assert len(base64.b64decode(k)) == 32


def test_bad_key_length_raises(monkeypatch):
    import base64
    monkeypatch.setenv("BW_CRED_KEY", base64.b64encode(b"too_short").decode())
    reset_key_cache()
    with pytest.raises(RuntimeError):
        encrypt("x")


def test_save_credentials_uses_db_when_provided(tmp_path: Path):
    db = DB(tmp_path / "t.sqlite3")
    cid = auth.save_credentials("alice", "pwd123", db=db)
    assert cid is not None and cid.startswith("c_")
    # 不进内存 cache
    assert "alice" not in auth._credential_cache or \
        auth._credential_cache.get("alice", {}).get("password") != "pwd123"

    # 取回来
    pwd = auth.get_credentials("alice", cred_id=cid, db=db)
    assert pwd == "pwd123"


def test_save_credentials_falls_back_to_memory_when_no_db():
    auth._credential_cache.clear()
    cid = auth.save_credentials("bob", "qwerty")
    assert cid is None
    assert auth.get_credentials("bob") == "qwerty"


def test_clear_credentials_removes_from_db_and_memory(tmp_path: Path):
    db = DB(tmp_path / "t.sqlite3")
    cid = auth.save_credentials("alice", "pwd", db=db)
    auth.clear_credentials("alice", cred_id=cid, db=db)
    assert auth.get_credentials("alice", cred_id=cid, db=db) is None


def test_issue_and_decode_jwt_with_cred_id():
    ident = auth.Identity(
        username="alice", display_name="Alice", role="user", cred_id="c_x",
    )
    token = auth.issue_jwt(ident)
    back = auth.decode_jwt(token)
    assert back.username == "alice"
    assert back.cred_id == "c_x"


def test_get_credentials_wrong_user_for_cred_id(tmp_path: Path):
    auth._credential_cache.clear()
    db = DB(tmp_path / "t.sqlite3")
    cid = auth.save_credentials("alice", "pwd", db=db)
    # 错的用户名 → AAD 校验失败 → 内存 cache 也没有 → 返回 None (不能从 alice 泄露给 bob)
    assert auth.get_credentials("bob", cred_id=cid, db=db) is None
