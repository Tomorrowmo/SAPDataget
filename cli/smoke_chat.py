"""触发一次真实 /api/chat 调用,把后端的完整错误链路打印出来。

用法:
    python -m cli.smoke_chat <fake-or-real-key>
"""
from __future__ import annotations

import json
import sys
import time

import httpx

if sys.platform == "win32":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")          # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def main() -> int:
    key = sys.argv[1] if len(sys.argv) > 1 else "sk-fake-1234567890abcdef"
    msg = sys.argv[2] if len(sys.argv) > 2 else "测试,请直接回复'你好'两个字"

    c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=120.0, trust_env=False)

    print("[1] login admin")
    r = c.post("/api/auth/login", json={"username": "admin", "password": ""})
    assert r.status_code == 200

    print(f"[2] set DEEPSEEK_API_KEY = {key[:8]}...{key[-4:]}")
    r = c.put("/api/llm/keys/DEEPSEEK_API_KEY", json={"value": key})
    assert r.status_code == 200, r.text

    print("[3] switch to deepseek/deepseek-chat")
    r = c.post("/api/llm/model", json={"model": "deepseek/deepseek-chat"})
    assert r.status_code == 200, r.text
    print(f"    current_ready={r.json()['current_ready']}")

    print(f"[4] POST /api/chat  message={msg!r}")
    t0 = time.monotonic()
    r = c.post("/api/chat", json={"message": msg})
    dt = (time.monotonic() - t0) * 1000
    print(f"    status_code={r.status_code} latency={dt:.0f}ms")
    try:
        body = r.json()
    except Exception:
        body = {"raw_text": r.text}
    print(json.dumps(body, ensure_ascii=False, indent=2, default=str)[:2000])

    # 清理
    print("[5] cleanup: delete key")
    c.delete("/api/llm/keys/DEEPSEEK_API_KEY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
