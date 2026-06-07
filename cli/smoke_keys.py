"""API Key 配置功能烟测 —— 模拟用户在 UI 上配 key 的完整链路。"""
from __future__ import annotations

import os
import sys

import httpx

if sys.platform == "win32":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")           # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def main() -> int:
    c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=30.0, trust_env=False)
    print("=" * 60)
    print(" API Key 配置功能 — 实地烟测")
    print("=" * 60)

    # admin 登录
    print("\n[1] admin 登录")
    r = c.post("/api/auth/login", json={"username": "admin", "password": ""})
    assert r.status_code == 200
    print("  ✅ admin")

    # 当前 key 状态
    print("\n[2] GET /api/llm/keys —— 当前各 provider 状态")
    providers = c.get("/api/llm/keys").json()["providers"]
    for p in providers:
        status = "●已配置" if p["configured"] else "未配置"
        src = f" ({p['source']})" if p["source"] else ""
        print(f"  {p['env_var']:<24}  {p['provider']:<12}  {status}{src}")

    # 配 key
    print("\n[3] PUT /api/llm/keys/DEEPSEEK_API_KEY")
    r = c.put("/api/llm/keys/DEEPSEEK_API_KEY", json={"value": "sk-test-deepseek-abcdef1234567890"})
    assert r.status_code == 200
    print(f"  ✅ saved, tail=…{r.json()['tail']}")

    # 查 ready 是否切换
    print("\n[4] GET /api/llm/models —— DeepSeek 是否 ready=True")
    models = c.get("/api/llm/models").json()["models"]
    ds = next(m for m in models if m["id"] == "deepseek/deepseek-chat")
    print(f"  ready={ds['ready']}")
    assert ds["ready"] is True

    # 切到 DeepSeek
    print("\n[5] POST /api/llm/model —— 切到 DeepSeek")
    r = c.post("/api/llm/model", json={"model": "deepseek/deepseek-chat"})
    assert r.status_code == 200
    print(f"  current={r.json()['current']}, ready={r.json()['current_ready']}")

    # 审计是否记录
    print("\n[6] GET /api/audit —— 看 set_api_key 与 switch_model 是否记上")
    audit = c.get("/api/audit?limit=10").json()["audit"]
    actions = [a["action"] for a in audit[:8]]
    print(f"  最近 8 条动作: {actions}")
    assert "set_api_key" in actions
    assert "switch_model" in actions

    # 清除 key
    print("\n[7] DELETE /api/llm/keys/DEEPSEEK_API_KEY")
    r = c.delete("/api/llm/keys/DEEPSEEK_API_KEY")
    assert r.status_code == 200
    models = c.get("/api/llm/models").json()["models"]
    ds = next(m for m in models if m["id"] == "deepseek/deepseek-chat")
    print(f"  删后 ready={ds['ready']}")
    assert ds["ready"] is False

    print("\n" + "=" * 60)
    print(" ✅ 所有 7 步 ok —— API Key 管理功能可用")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
