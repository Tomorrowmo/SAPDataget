"""Qwen 链路 + 多用户 key 隔离烟测。"""
from __future__ import annotations

import json
import sys

import httpx

if sys.platform == "win32":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")          # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def main() -> int:
    real_key = sys.argv[1] if len(sys.argv) > 1 else "sk-fake-qwen-key-12345678"

    c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=60.0, trust_env=False)
    print("=" * 64)
    print(" Qwen 链路 + 多用户 key 隔离烟测")
    print("=" * 64)

    # ===== 用户 1: alice =====
    print("\n[A1] alice 登录")
    c.post("/api/auth/login", json={"username": "alice", "password": ""}).raise_for_status()

    print(f"[A2] alice 配置 DASHSCOPE_API_KEY = {real_key[:8]}...{real_key[-4:]}")
    r = c.put("/api/llm/keys/DASHSCOPE_API_KEY", json={"value": real_key})
    print(f"     -> {r.status_code}  tail=…{r.json().get('tail')}")

    print("[A3] alice 看 dashscope/qwen-max 是否 ready")
    models = c.get("/api/llm/models").json()["models"]
    qwen = next(m for m in models if m["id"] == "dashscope/qwen-max")
    print(f"     -> ready={qwen['ready']}  key_source={qwen.get('key_source')}")

    print("[A4] alice 切到 qwen-max")
    r = c.post("/api/llm/model", json={"model": "dashscope/qwen-max"})
    print(f"     -> current={r.json()['current']}  current_ready={r.json()['current_ready']}")

    print("[A5] alice 测试 key —— 真假 key 都看 LiteLLM 反应")
    r = c.post("/api/llm/keys/DASHSCOPE_API_KEY/test")
    body = r.json()
    if body["ok"]:
        print(f"     ✅ 有效 model={body['model']} latency={body['latency_ms']}ms reply='{body['reply']}'")
    else:
        print(f"     ❌ 失败 category={body['category']}")
        print(f"        error: {body['error'][:300]}")

    # ===== 用户 2: bob =====
    print("\n[B1] bob 登录")
    c.post("/api/auth/login", json={"username": "bob", "password": ""}).raise_for_status()

    print("[B2] bob 看自己的 qwen-max 是否 ready (应 False —— 没继承 alice 的)")
    models = c.get("/api/llm/models").json()["models"]
    qwen_b = next(m for m in models if m["id"] == "dashscope/qwen-max")
    print(f"     -> ready={qwen_b['ready']}  key_source={qwen_b.get('key_source')}")
    assert qwen_b["ready"] is False, "❌ 隔离失败:bob 不该看到 alice 的 key"

    print("[B3] bob 看自己的 keys 列表")
    bob_keys = c.get("/api/llm/keys").json()["providers"]
    bob_qwen = next(p for p in bob_keys if p["env_var"] == "DASHSCOPE_API_KEY")
    print(f"     env_var={bob_qwen['env_var']}  has_personal={bob_qwen['has_personal']}")
    assert bob_qwen["has_personal"] is False

    print("[B4] bob 尝试 chat (无 key 应给清晰提示)")
    r = c.post("/api/chat", json={"message": "测试"})
    body = r.json()
    print(f"     status_code={r.status_code}  error_category={body.get('error_category')}")
    print(f"     answer/error: {body.get('answer') or body.get('error')[:120]}")

    # ===== 清理 =====
    print("\n[C] alice 清理")
    c.post("/api/auth/login", json={"username": "alice", "password": ""})
    c.delete("/api/llm/keys/DASHSCOPE_API_KEY")

    print("\n" + "=" * 64)
    print(" ✅ 用户 key 隔离正确 · Qwen 调用链路打通 (是否 key 有效看上方 A5)")
    print("=" * 64)
    print("\n如果 A5 失败 category=auth ——你的 key 无效;category=network ——网络问题;\n"
          "category=other ——LiteLLM 没认这个 provider/model 配对。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
