"""端到端 HTTP 烟测 —— 用 httpx 直接打 uvicorn 起的进程。

需要先启动:
    uvicorn app.server:app --port 8000

测试覆盖业务用户主流程:
  login → list-skills → run-skill → download xlsx → list-tasks → switch model
"""
from __future__ import annotations

import io
import os
import time

import httpx
import pytest
from openpyxl import load_workbook

BASE_URL = os.environ.get("BW_TEST_URL", "http://127.0.0.1:8000")


def _delete_all_tasks(client: httpx.Client) -> None:
    tasks = client.get("/api/tasks").json().get("tasks", [])
    for task in tasks:
        client.delete(f"/api/tasks/{task['id']}")


def _server_up() -> bool:
    try:
        r = httpx.get(f"{BASE_URL}/api/status", timeout=2.0)
        return r.status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _server_up(), reason="uvicorn 未启动")


@pytest.fixture
def client():
    # Windows 上 httpx keep-alive 偶尔被远端强关 → 配置传输层重试
    transport = httpx.HTTPTransport(retries=3)
    with httpx.Client(base_url=BASE_URL, timeout=30.0, transport=transport) as c:
        yield c


@pytest.fixture
def logged_in(client: httpx.Client):
    r = client.post("/api/auth/login", json={"username": "admin", "password": ""})
    assert r.status_code == 200, r.text
    yield client
    _delete_all_tasks(client)


def test_status(client: httpx.Client):
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert data["bw_mode"] == "mock"
    assert data["skills_count"] >= 3
    assert len(data["llm"]["models"]) >= 5


def test_unauthorized(client: httpx.Client):
    r = client.get("/api/skills")
    assert r.status_code == 401


def test_login_logout(client: httpx.Client):
    r = client.post("/api/auth/login", json={"username": "alice", "password": ""})
    assert r.status_code == 200
    me = client.get("/api/auth/me").json()
    assert me["username"] == "alice"

    r = client.post("/api/auth/logout")
    assert r.status_code == 200
    r = client.get("/api/auth/me")
    assert r.status_code == 401


def test_admin_role(client: httpx.Client):
    """登录 admin 用户应自动获得 admin role (mock 模式约定)"""
    r = client.post("/api/auth/login", json={"username": "admin", "password": ""})
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


def test_skills_list_detail(logged_in: httpx.Client):
    r = logged_in.get("/api/skills")
    assert r.status_code == 200
    data = r.json()
    ids = {s["id"] for s in data["skills"]}
    assert {"monthly_sales_region", "top_customers", "plant_yield"}.issubset(ids)

    r = logged_in.get("/api/skills/monthly_sales_region")
    assert r.status_code == 200
    detail = r.json()
    assert detail["service"] == "ZBW_SALES_SRV"
    pnames = {p["name"] for p in detail["params"]}
    assert {"month", "region"}.issubset(pnames)


def test_run_skill_and_download(logged_in: httpx.Client):
    r = logged_in.post(
        "/api/skills/monthly_sales_region/run",
        json={"params": {"month": "202605", "region": "HD", "top_n": 5}},
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "done"
    assert data["row_count"] >= 1
    assert data["excel"] is not None
    download_url = data["excel"]["download_url"]
    task_id = data["task_id"]

    # 下载 Excel
    r2 = logged_in.get(download_url)
    assert r2.status_code == 200
    assert r2.headers["content-type"].startswith("application/")
    wb = load_workbook(io.BytesIO(r2.content))
    assert "查询信息" in wb.sheetnames

    # 任务历史里能查到
    tasks = logged_in.get("/api/tasks").json()["tasks"]
    assert any(t["id"] == task_id for t in tasks)


def test_run_skill_validates(logged_in: httpx.Client):
    """缺必填参数应失败 (200 + status=failed,不要 5xx)"""
    r = logged_in.post(
        "/api/skills/monthly_sales_region/run",
        json={"params": {"region": "HD"}},  # 缺 month
    )
    assert r.status_code == 200
    assert r.json()["status"] == "failed"


def test_services_list(logged_in: httpx.Client):
    r = logged_in.get("/api/services")
    assert r.status_code == 200
    names = {s["TechnicalServiceName"] for s in r.json()["services"]}
    assert "ZBW_SALES_SRV" in names


def test_service_metadata(logged_in: httpx.Client):
    r = logged_in.get("/api/services/ZBW_SALES_SRV")
    assert r.status_code == 200
    es = {e["name"] for e in r.json()["entity_sets"]}
    assert "SalesByOfficeView" in es


def test_llm_switch_model(logged_in: httpx.Client):
    # 切到 qwen-plus
    r = logged_in.post(
        "/api/llm/model",
        json={"model": "dashscope/qwen-plus"},
    )
    assert r.status_code == 200
    assert r.json()["current"] == "dashscope/qwen-plus"

    # 切回 deepseek
    r2 = logged_in.post(
        "/api/llm/model",
        json={"model": "deepseek/deepseek-chat"},
    )
    assert r2.status_code == 200
    assert r2.json()["current"] == "deepseek/deepseek-chat"


def test_admin_endpoints_require_admin(client: httpx.Client):
    # 普通用户登录
    client.post("/api/auth/login", json={"username": "bob", "password": ""})
    assert client.get("/api/audit").status_code == 403
    assert client.get("/api/sensitive-fields").status_code == 403


def test_admin_audit_and_sensitive(logged_in: httpx.Client):
    # admin 已登录
    r = logged_in.get("/api/audit?limit=10")
    assert r.status_code == 200
    assert "audit" in r.json()

    # 敏感字段 CRUD
    r2 = logged_in.post(
        "/api/sensitive-fields",
        json={"service": "ZBW_HR_SRV", "field": "SALARY_BASE", "mask_mode": "redact"},
    )
    assert r2.status_code == 200
    listed = logged_in.get("/api/sensitive-fields").json()["fields"]
    assert any(f["field"] == "SALARY_BASE" for f in listed)
    logged_in.delete("/api/sensitive-fields/ZBW_HR_SRV/SALARY_BASE")
    listed2 = logged_in.get("/api/sensitive-fields").json()["fields"]
    assert not any(f["field"] == "SALARY_BASE" for f in listed2)


def test_llm_keys_list(logged_in: httpx.Client):
    """所有登录用户可查 key 状态(只读)"""
    r = logged_in.get("/api/llm/keys")
    assert r.status_code == 200
    providers = r.json()["providers"]
    env_vars = {p["env_var"] for p in providers}
    assert {"DEEPSEEK_API_KEY", "DASHSCOPE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"}.issubset(env_vars)


def test_llm_keys_per_user_isolation(client: httpx.Client):
    """alice 的 key 不应影响 bob —— 每用户独立"""
    # alice 配 key
    client.post("/api/auth/login", json={"username": "alice", "password": ""})
    r = client.put("/api/llm/keys/DASHSCOPE_API_KEY", json={"value": "sk-alice-key-xxxxxxxx"})
    assert r.status_code == 200, r.text
    alice_models = client.get("/api/llm/models").json()["models"]
    qwen = next(m for m in alice_models if m["id"] == "dashscope/qwen-max")
    assert qwen["ready"] is True

    # bob 登录,不应看到 alice 的 key
    client.post("/api/auth/login", json={"username": "bob", "password": ""})
    bob_models = client.get("/api/llm/models").json()["models"]
    qwen_b = next(m for m in bob_models if m["id"] == "dashscope/qwen-max")
    assert qwen_b["ready"] is False, "bob 不应继承 alice 的 key"

    bob_keys = client.get("/api/llm/keys").json()["providers"]
    bob_qwen = next(p for p in bob_keys if p["env_var"] == "DASHSCOPE_API_KEY")
    assert bob_qwen["has_personal"] is False

    # 清理
    client.post("/api/auth/login", json={"username": "alice", "password": ""})
    client.delete("/api/llm/keys/DASHSCOPE_API_KEY")


def test_llm_key_set_test_delete_self(logged_in: httpx.Client):
    """普通流程:管理员配自己的 key + 列表查询 + 删除"""
    r = logged_in.put("/api/llm/keys/DEEPSEEK_API_KEY", json={"value": "sk-fake-1234567890abcdef"})
    assert r.status_code == 200
    assert r.json()["tail"] == "cdef"

    listed = logged_in.get("/api/llm/keys").json()["providers"]
    ds = next(p for p in listed if p["env_var"] == "DEEPSEEK_API_KEY")
    assert ds["has_personal"] is True
    assert ds["source"] == "user"
    assert ds["tail"] == "cdef"

    models = logged_in.get("/api/llm/models").json()["models"]
    ds_model = next(m for m in models if m["id"] == "deepseek/deepseek-chat")
    assert ds_model["ready"] is True

    # 删除
    r2 = logged_in.delete("/api/llm/keys/DEEPSEEK_API_KEY")
    assert r2.status_code == 200
    models2 = logged_in.get("/api/llm/models").json()["models"]
    ds_model2 = next(m for m in models2 if m["id"] == "deepseek/deepseek-chat")
    assert ds_model2["ready"] is False


def test_llm_key_unknown_env_rejected(logged_in: httpx.Client):
    r = logged_in.put("/api/llm/keys/MADE_UP_API_KEY", json={"value": "x"})
    assert r.status_code == 400


def test_llm_key_empty_rejected(logged_in: httpx.Client):
    r = logged_in.put("/api/llm/keys/DEEPSEEK_API_KEY", json={"value": ""})
    assert r.status_code == 400


def test_report_list_shortcut_works_without_llm(logged_in: httpx.Client):
    r = logged_in.post("/api/chat", json={"message": "报告清单"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"].startswith("已查询到报告清单")
    assert body["llm_model"] == "builtin/report-list"
    assert body["task"]["status"] == "done"
    assert body["task"]["row_count"] >= 1
    assert body["task"]["excel"] is not None
    assert body["task"]["excel"]["download_url"].startswith("/api/tasks/")
    first = body["task"]["rows_preview"][0]
    assert {"ReportID", "ReportDescription"}.issubset(first.keys())


def test_report_list_shortcut_respects_top_n(logged_in: httpx.Client):
    r = logged_in.post("/api/chat", json={"message": "报告清单前1条"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task"]["status"] == "done"
    assert len(body["task"]["rows_preview"]) == 1


def test_spa_fallback(client: httpx.Client):
    """前端路由 fallback 应返回 index.html"""
    r = client.get("/some/spa/path")
    assert r.status_code == 200
    assert "<!doctype html>" in r.text.lower() or "<html" in r.text.lower()
