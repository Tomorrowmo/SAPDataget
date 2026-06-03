"""AES-256-GCM 加密层 —— 用于 BW 凭据持久化 (§14, P2-15)。

设计要点:
  * 密钥 32 字节,从环境变量 ``BW_CRED_KEY`` 取 (base64 编码)。
  * 启动期 ``ensure_key()`` 校验,若 env 未设则**生成临时随机密钥**(进程重启后凭据
    自然失效) —— 一期降级方案,生产部署务必在 .env 显式提供 ``BW_CRED_KEY``。
  * GCM 模式天然带认证标签 (AEAD),防止密文被篡改。
"""
from __future__ import annotations

import base64
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


_KEY_ENV = "BW_CRED_KEY"
_KEY_BYTES = 32
_NONCE_BYTES = 12

_cached_key: bytes | None = None


def _load_key() -> bytes:
    global _cached_key
    if _cached_key is not None:
        return _cached_key
    raw = os.environ.get(_KEY_ENV, "").strip()
    if raw:
        try:
            k = base64.b64decode(raw)
        except Exception as e:
            raise RuntimeError(f"{_KEY_ENV} 必须是 base64 编码: {e}") from e
        if len(k) != _KEY_BYTES:
            raise RuntimeError(f"{_KEY_ENV} 解码后必须是 {_KEY_BYTES} 字节,实际 {len(k)}")
        _cached_key = k
    else:
        # 临时密钥 —— 进程重启凭据全失效
        _cached_key = secrets.token_bytes(_KEY_BYTES)
    return _cached_key


def reset_key_cache() -> None:
    """测试用:强制重新读取密钥。"""
    global _cached_key
    _cached_key = None


def encrypt(plaintext: str | bytes, *, aad: bytes | None = None) -> tuple[bytes, bytes]:
    """加密;返回 (ciphertext_with_tag, nonce)。"""
    key = _load_key()
    aes = AESGCM(key)
    nonce = secrets.token_bytes(_NONCE_BYTES)
    pt = plaintext.encode("utf-8") if isinstance(plaintext, str) else plaintext
    ct = aes.encrypt(nonce, pt, aad)
    return ct, nonce


def decrypt(ciphertext: bytes, nonce: bytes, *, aad: bytes | None = None) -> bytes:
    """解密;失败抛 cryptography.exceptions.InvalidTag。"""
    key = _load_key()
    aes = AESGCM(key)
    return aes.decrypt(nonce, ciphertext, aad)


def generate_key_base64() -> str:
    """生成一个新的 32 字节密钥,base64 编码 —— 给运维放到 .env 用。"""
    return base64.b64encode(secrets.token_bytes(_KEY_BYTES)).decode("ascii")
