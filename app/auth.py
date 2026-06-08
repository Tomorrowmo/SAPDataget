"""鉴权 (§8)。

实现:
  * 登录时用 BW Basic Auth 探测 catalog 服务,以验证凭据有效
  * 凭据持久化: AES-256-GCM 加密后存 ``bw_creds`` 表 (§14, P2-15);
    JWT payload 带 ``cred_id``,后端按 cred_id 解密拿密码
  * 兼容旧路径: 若 DB 缺失或加密失败,降级到进程内存 cache
  * JWT cookie 用 HS256,签名密钥从 AUTH_SECRET env 取,启动时若空则随机生成

二期升级:
  * SAML/OAuth SSO + Principal Propagation

mock 模式下,任意用户名密码都能通过(因为 mock 不验密码)。
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any

import jwt as pyjwt

from app.config import BWSettings
from app.bw.live import LiveBWClient

log = logging.getLogger(__name__)

# 启动期生成 / 读取 JWT 密钥
AUTH_SECRET = os.environ.get("AUTH_SECRET", "").strip() or secrets.token_urlsafe(48)
JWT_ALGO = "HS256"
JWT_TTL_SECONDS = 8 * 3600


@dataclass
class Identity:
    username: str
    display_name: str
    role: str = "user"
    cred_id: str | None = None


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
        client_fallback=bw_settings.client_fallback,
        max_export_rows=bw_settings.max_export_rows,
    )
    client = LiveBWClient(cloned)
    resp = client.list_services(top=1)
    if resp.status_code == 401 or resp.status_code == 403:
        raise AuthError("BW 拒绝该用户名密码 (401/403)")
    if not resp.ok:
        # 某些系统未启用 CATALOGSERVICE（常见 404），回退到业务 OData metadata 探测。
        probe_env = os.environ.get("BW_AUTH_PROBE_SERVICES", "").strip()
        probe_services = [x.strip() for x in probe_env.split(",") if x.strip()]
        if not probe_services:
            probe_services = ["ZBW_QUERY_LIST_SRV"]

        probe_errors: list[str] = []
        for service in probe_services:
            probe = client.get_metadata(service)
            if probe.status_code in (401, 403):
                raise AuthError("BW 拒绝该用户名密码 (401/403)")
            if probe.ok:
                return Identity(username=username, display_name=username, role="user")
            probe_errors.append(f"{service}: {probe.error or f'HTTP {probe.status_code}'}")

        detail = "; ".join(probe_errors) if probe_errors else (resp.error or f"HTTP {resp.status_code}")
        raise AuthError(f"BW 连接失败: {detail}")
    return Identity(username=username, display_name=username, role="user")


def save_credentials(
    username: str, password: str, *, db: Any | None = None,
) -> str | None:
    """优先 AES-256-GCM 加密落盘;失败或没传 db 时退回进程内存。

    返回值: cred_id (写盘成功) 或 None (走内存 cache)。
    """
    if db is not None:
        try:
            from app.crypto import encrypt
            ct, nonce = encrypt(password, aad=username.encode("utf-8"))
            cred_id = db.save_bw_cred(
                username=username, ciphertext=ct, nonce=nonce,
                ttl_seconds=JWT_TTL_SECONDS,
            )
            return cred_id
        except Exception as e:                                  # noqa: BLE001
            log.warning("BW 凭据加密落盘失败,降级到内存 cache: %s", e)
    _credential_cache[username] = {
        "password": password,
        "expires_at": time.time() + JWT_TTL_SECONDS,
    }
    return None


def get_credentials(
    username: str, *, cred_id: str | None = None, db: Any | None = None,
) -> str | None:
    """优先按 cred_id 从加密表取;否则降级到进程内存 cache。"""
    if cred_id and db is not None:
        try:
            from app.crypto import decrypt
            rec = db.get_bw_cred(cred_id)
            if rec and rec.get("username") == username:
                pt = decrypt(rec["ciphertext"], rec["nonce"],
                             aad=username.encode("utf-8"))
                return pt.decode("utf-8")
        except Exception as e:                                  # noqa: BLE001
            log.warning("BW 凭据解密失败 cred_id=%s: %s", cred_id, e)
    rec = _credential_cache.get(username)
    if not rec:
        return None
    if float(rec["expires_at"]) < time.time():
        _credential_cache.pop(username, None)
        return None
    return str(rec["password"])


def clear_credentials(
    username: str, *, cred_id: str | None = None, db: Any | None = None,
) -> None:
    if cred_id and db is not None:
        try:
            db.delete_bw_cred(cred_id)
        except Exception as e:                                  # noqa: BLE001
            log.warning("删除加密凭据失败 cred_id=%s: %s", cred_id, e)
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
    if identity.cred_id:
        payload["cred_id"] = identity.cred_id
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
        cred_id=payload.get("cred_id"),
    )
