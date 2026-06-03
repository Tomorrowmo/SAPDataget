"""鉴权 (§8 - 一期简化版)。

一期实现：
  * 登录时用 BW Basic Auth 探测 catalog 服务,以验证凭据有效
  * 凭据**不落盘**（一期简化,见 §8 说明）—— 仅在内存 session 中保留密码
  * JWT cookie 用 HS256,签名密钥从 AUTH_SECRET env 取,启动时若空则随机生成

二期升级:
  * 凭据 AES-256-GCM 持久化
  * SAML/OAuth SSO

mock 模式下,任意用户名密码都能通过(因为 mock 不验密码)。
"""
from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass

import jwt as pyjwt

from app.config import BWSettings
from app.bw.live import LiveBWClient

# 启动期生成 / 读取 JWT 密钥
AUTH_SECRET = os.environ.get("AUTH_SECRET", "").strip() or secrets.token_urlsafe(48)
JWT_ALGO = "HS256"
JWT_TTL_SECONDS = 8 * 3600


@dataclass
class Identity:
    username: str
    display_name: str
    role: str = "user"


class AuthError(Exception):
    pass


# 进程内的 BW 凭据缓存 —— 一期简化,二期改为加密落盘
# key = (username) -> {password, expires_at}
_credential_cache: dict[str, dict[str, object]] = {}


def verify_bw_credentials(bw_settings: BWSettings, username: str, password: str) -> Identity:
    """用 BW Gateway 验证用户名密码。

    mock 模式直接放行,任何凭据都成功。
    """
    if bw_settings.mode == "mock":
        return Identity(username=username or "demo", display_name=username or "演示用户", role="admin" if username == "admin" else "user")

    # live 模式: 临时建一个 LiveBWClient 用这对凭据试探 catalog
    cloned = BWSettings(
        mode="live",
        mock_data_dir=bw_settings.mock_data_dir,
        mock_latency_ms=0,
        base_url=bw_settings.base_url,
        username=username,
        password=password,
        client=bw_settings.client,
        language=bw_settings.language,
        verify_ssl=bw_settings.verify_ssl,
        timeout=bw_settings.timeout,
    )
    client = LiveBWClient(cloned)
    resp = client.list_services(top=1)
    if resp.status_code == 401 or resp.status_code == 403:
        raise AuthError("BW 拒绝该用户名密码 (401/403)")
    if not resp.ok:
        raise AuthError(f"BW 连接失败: {resp.error}")
    return Identity(username=username, display_name=username, role="user")


def save_credentials(username: str, password: str) -> None:
    _credential_cache[username] = {
        "password": password,
        "expires_at": time.time() + JWT_TTL_SECONDS,
    }


def get_credentials(username: str) -> str | None:
    rec = _credential_cache.get(username)
    if not rec:
        return None
    if float(rec["expires_at"]) < time.time():
        _credential_cache.pop(username, None)
        return None
    return str(rec["password"])


def clear_credentials(username: str) -> None:
    _credential_cache.pop(username, None)


def issue_jwt(identity: Identity) -> str:
    now = int(time.time())
    payload = {
        "sub": identity.username,
        "name": identity.display_name,
        "role": identity.role,
        "iat": now,
        "exp": now + JWT_TTL_SECONDS,
    }
    return pyjwt.encode(payload, AUTH_SECRET, algorithm=JWT_ALGO)


def decode_jwt(token: str) -> Identity:
    try:
        payload = pyjwt.decode(token, AUTH_SECRET, algorithms=[JWT_ALGO])
    except pyjwt.ExpiredSignatureError as e:
        raise AuthError("登录已过期,请重新登录") from e
    except pyjwt.InvalidTokenError as e:
        raise AuthError(f"无效的 JWT: {e}") from e
    return Identity(
        username=payload["sub"],
        display_name=payload.get("name", payload["sub"]),
        role=payload.get("role", "user"),
    )
