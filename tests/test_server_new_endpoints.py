"""端到端测试 —— 新加的 endpoints: rerun, delete, favorites, messages, admin skill CRUD,
SSE stream, skill status, quota。

需要 uvicorn 在跑:
    uvicorn app.server:app --port 8000
"""
from __future__ import annotations

import io
import json
import os
import time

import httpx
import pytest
from openpyxl import Workbook

BASE_URL = os.environ.get("BW_TEST_URL", "http://127.0.0.1:8000")

_TEMP_SKILL_PREFIXES = (
    "test_temp_skill_",
    "test_tpl_skill_",
    "test_tpl_ok_",
    "test_chart_",
    "test_visible_",
)


def _server_up() -> bool:
    try:
        return httpx.get(f"{BASE_URL}/api/status", timeout=2.0).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _server_up(), reason="uvicorn 未启动")


def _delete_all_tasks(client: httpx.Client) -> None:
    tasks = client.get("/api/tasks").json().get("tasks", [])
    for task in tasks:
        client.delete(f"/api/tasks/{task['id']}")


def _delete_temp_skills(admin_client: httpx.Client) -> None:
    skills = admin_client.get("/api/skills").json().get("skills", [])
    for skill in skills:
        skill_id = skill["id"]
        if skill_id.startswith(_TEMP_SKILL_PREFIXES):
            admin_client.delete(f"/api/admin/skills/{skill_id}")


@pytest.fixture
def client():
    transport = httpx.HTTPTransport(retries=3)
    with httpx.Client(base_url=BASE_URL, timeout=30.0, transport=transport) as c:
        yield c


def _make_client() -> httpx.Client:
    return httpx.Client(
        base_url=BASE_URL,
        timeout=30.0,
        transport=httpx.HTTPTransport(retries=3),
    )


@pytest.fixture
def admin():
    with _make_client() as admin_client:
        r = admin_client.post("/api/auth/login", json={"username": "admin", "password": ""})
        assert r.status_code == 200
        yield admin_client
        _delete_temp_skills(admin_client)
        _delete_all_tasks(admin_client)


@pytest.fixture
def alice():
    with _make_client() as alice_client:
        r = alice_client.post("/api/auth/login", json={"username": "alice", "password": ""})
        assert r.status_code == 200
        yield alice_client
        _delete_all_tasks(alice_client)


# ============================== rerun + delete ==============================


def test_task_rerun_with_same_params(alice: httpx.Client):
    # 先跑一次
    r1 = alice.post(
        "/api/skills/monthly_sales_region/run",
        json={"params": {"month": "202605", "region": "HD", "top_n": 5}},
    )
    assert r1.json()["status"] == "done"
    tid = r1.json()["task_id"]

    # rerun 不改参
    r2 = alice.post(f"/api/tasks/{tid}/rerun", json={})
    assert r2.status_code == 200
    body = r2.json()
    assert body["status"] == "done"
    assert body["row_count"] == r1.json()["row_count"]
    assert body["task_id"] != tid


def test_task_rerun_with_override_params(alice: httpx.Client):
    r1 = alice.post(
        "/api/skills/monthly_sales_region/run",
        json={"params": {"month": "202605", "region": "HD", "top_n": 5}},
    )
    tid = r1.json()["task_id"]
    r2 = alice.post(f"/api/tasks/{tid}/rerun", json={"params": {"top_n": 2}})
    assert r2.status_code == 200
    # row_count = $inlinecount=allpages 的 total (= 3 总行数);
    # 真正被 top 截断后的实际返回行,只能从 rows_preview 看
    assert len(r2.json()["rows_preview"]) == 2


def test_task_rerun_other_user_404(alice: httpx.Client, client: httpx.Client):
    r1 = alice.post(
        "/api/skills/monthly_sales_region/run",
        json={"params": {"month": "202605", "region": "HD"}},
    )
    tid = r1.json()["task_id"]
    # bob 不能 rerun alice 的任务
    client.post("/api/auth/login", json={"username": "bob", "password": ""})
    r = client.post(f"/api/tasks/{tid}/rerun", json={})
    assert r.status_code == 404


def test_task_delete(alice: httpx.Client):
    r1 = alice.post(
        "/api/skills/monthly_sales_region/run",
        json={"params": {"month": "202605", "region": "HD"}},
    )
    tid = r1.json()["task_id"]
    r = alice.delete(f"/api/tasks/{tid}")
    assert r.status_code == 200
    # 再查 → 404
    assert alice.get(f"/api/tasks/{tid}").status_code == 404


# ============================== favorites ==============================


def test_favorite_skill_add_list_remove(alice: httpx.Client):
    # add
    r = alice.post("/api/favorites", json={"kind": "skill", "ref_id": "monthly_sales_region"})
    assert r.status_code == 200
    # list
    favs = alice.get("/api/favorites?kind=skill").json()["favorites"]
    assert any(f["ref_id"] == "monthly_sales_region" for f in favs)
    # skill list 上能看到 favorite=true 标记
    skills = alice.get("/api/skills").json()["skills"]
    s = next(s for s in skills if s["id"] == "monthly_sales_region")
    assert s["favorite"] is True
    # remove
    alice.delete("/api/favorites/skill/monthly_sales_region")
    favs2 = alice.get("/api/favorites?kind=skill").json()["favorites"]
    assert not any(f["ref_id"] == "monthly_sales_region" for f in favs2)


def test_favorite_isolation(alice: httpx.Client, client: httpx.Client):
    alice.post("/api/favorites", json={"kind": "skill", "ref_id": "top_customers"})
    client.post("/api/auth/login", json={"username": "bob2", "password": ""})
    favs = client.get("/api/favorites").json()["favorites"]
    assert not any(f["ref_id"] == "top_customers" for f in favs)
    # 清理
    client.post("/api/auth/login", json={"username": "alice", "password": ""})
    client.delete("/api/favorites/skill/top_customers")


def test_favorite_invalid_kind_400(alice: httpx.Client):
    r = alice.post("/api/favorites", json={"kind": "BOGUS", "ref_id": "x"})
    assert r.status_code == 400


# ============================== task_messages (chat 多轮) ==============================


def test_task_messages_empty_for_skill_task(alice: httpx.Client):
    r1 = alice.post(
        "/api/skills/monthly_sales_region/run",
        json={"params": {"month": "202605", "region": "HD"}},
    )
    tid = r1.json()["task_id"]
    msgs = alice.get(f"/api/tasks/{tid}/messages").json()["messages"]
    assert msgs == []                                   # skill 任务没消息历史


def test_task_messages_other_user_404(alice: httpx.Client, client: httpx.Client):
    r1 = alice.post(
        "/api/skills/monthly_sales_region/run",
        json={"params": {"month": "202605", "region": "HD"}},
    )
    tid = r1.json()["task_id"]
    client.post("/api/auth/login", json={"username": "carol", "password": ""})
    assert client.get(f"/api/tasks/{tid}/messages").status_code == 404


# ============================== SSE stream ==============================


def test_task_stream_completed_immediately(alice: httpx.Client):
    r1 = alice.post(
        "/api/skills/monthly_sales_region/run",
        json={"params": {"month": "202605", "region": "HD"}},
    )
    tid = r1.json()["task_id"]
    # 终态任务 → 一次返回即关
    with httpx.stream("GET", f"{BASE_URL}/api/tasks/{tid}/stream",
                      cookies=alice.cookies, timeout=10.0) as resp:
        assert resp.status_code == 200
        chunks = list(resp.iter_text())
    data = "".join(chunks)
    assert "event: done" in data or "event: failed" in data


def test_task_stream_404_for_other_user(alice: httpx.Client, client: httpx.Client):
    r1 = alice.post(
        "/api/skills/monthly_sales_region/run",
        json={"params": {"month": "202605", "region": "HD"}},
    )
    tid = r1.json()["task_id"]
    client.post("/api/auth/login", json={"username": "dave", "password": ""})
    r = client.get(f"/api/tasks/{tid}/stream")
    assert r.status_code == 404


# ============================== admin skill CRUD ==============================


def test_admin_skill_crud_lifecycle(admin: httpx.Client):
    sid = "test_temp_skill_" + str(int(time.time()))[-6:]
    skill_md = (
        "---\n"
        f"id: {sid}\n"
        "title: 测试临时 Skill\n"
        "version: 1\n"
        "keywords: [test]\n"
        "params:\n"
        "  - name: month\n"
        "    required: true\n"
        "    description: YYYYMM\n"
        "---\n"
        "# test\n"
    )
    service_yaml = (
        "service: ZBW_SALES_SRV\n"
        "entity_set: SalesByOfficeView\n"
        "filter_template: \"CALMONTH eq '{{ month }}'\"\n"
        "select: [OfficeCode, NETWR_F]\n"
    )
    # create
    r = admin.post("/api/admin/skills", json={
        "id": sid, "skill_md": skill_md, "service_yaml": service_yaml,
    })
    assert r.status_code == 200, r.text

    # 重复创建 → 409
    r2 = admin.post("/api/admin/skills", json={
        "id": sid, "skill_md": skill_md, "service_yaml": service_yaml,
    })
    assert r2.status_code == 409

    # 出现在列表
    skills = admin.get("/api/skills").json()["skills"]
    assert any(s["id"] == sid for s in skills)

    # 读源
    src = admin.get(f"/api/admin/skills/{sid}/source").json()
    assert sid in src["skill_md"]

    # 改
    r3 = admin.put(f"/api/admin/skills/{sid}", json={
        "skill_md": skill_md.replace("测试临时 Skill", "测试 (改名)"),
    })
    assert r3.status_code == 200

    # 试运行 (mock 数据有 202605/HD/HN 等)
    r4 = admin.post(f"/api/admin/skills/{sid}/test-run", json={
        "params": {"month": "202605"},
    })
    assert r4.status_code == 200
    assert r4.json()["status"] == "done"

    # 状态切换
    r5 = admin.patch(f"/api/admin/skills/{sid}/status", json={"status": "deprecated"})
    assert r5.status_code == 200
    skills2 = admin.get("/api/skills").json()["skills"]
    s = next(s for s in skills2 if s["id"] == sid)
    assert s["status"] == "deprecated"

    # archived 时非 admin 看不见
    admin.patch(f"/api/admin/skills/{sid}/status", json={"status": "archived"})
    # 用 bob 登录看
    with httpx.Client(base_url=BASE_URL, timeout=10.0) as bob:
        bob.post("/api/auth/login", json={"username": "bob3", "password": ""})
        ids = {s["id"] for s in bob.get("/api/skills").json()["skills"]}
        assert sid not in ids

    # 删
    admin.delete(f"/api/admin/skills/{sid}")
    skills3 = admin.get("/api/skills").json()["skills"]
    assert not any(s["id"] == sid for s in skills3)


def test_admin_skill_create_invalid_id(admin: httpx.Client):
    r = admin.post("/api/admin/skills", json={
        "id": "Has-Bad-Chars",
        "skill_md": "---\nid: x\n---\n",
        "service_yaml": "service: X\nentity_set: Y\n",
    })
    assert r.status_code == 400


def test_admin_skill_endpoints_require_admin(client: httpx.Client):
    client.post("/api/auth/login", json={"username": "rando", "password": ""})
    assert client.post("/api/admin/skills", json={
        "id": "x", "skill_md": "", "service_yaml": "",
    }).status_code == 403
    assert client.patch("/api/admin/skills/x/status", json={"status": "active"}).status_code == 403


def test_admin_skill_upload_template_rejects_with_macros(admin: httpx.Client, tmp_path):
    # 先建一个临时 skill
    sid = "test_tpl_skill_" + str(int(time.time()))[-6:]
    admin.post("/api/admin/skills", json={
        "id": sid, "skill_md": "---\nid: " + sid + "\n---\n",
        "service_yaml": "service: ZBW_SALES_SRV\nentity_set: SalesByOfficeView\n",
    })
    # 伪造一个含 vbaProject.bin 的 xlsx
    import zipfile
    bad = tmp_path / "bad.xlsx"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("xl/vbaProject.bin", b"x")
        zf.writestr("[Content_Types].xml", "<x/>")
    files = {"file": ("bad.xlsx", bad.read_bytes(), "application/xlsx")}
    r = admin.post(f"/api/admin/skills/{sid}/files/template", files=files)
    assert r.status_code == 400 and "VBA" in r.text
    admin.delete(f"/api/admin/skills/{sid}")


def test_admin_skill_upload_template_ok(admin: httpx.Client, tmp_path):
    sid = "test_tpl_ok_" + str(int(time.time()))[-6:]
    admin.post("/api/admin/skills", json={
        "id": sid, "skill_md": "---\nid: " + sid + "\n---\n",
        "service_yaml": "service: ZBW_SALES_SRV\nentity_set: SalesByOfficeView\n",
    })
    p = tmp_path / "ok.xlsx"
    wb = Workbook()
    wb.save(p)
    files = {"file": ("ok.xlsx", p.read_bytes(),
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    r = admin.post(f"/api/admin/skills/{sid}/files/template", files=files)
    assert r.status_code == 200
    src = admin.get(f"/api/admin/skills/{sid}/source").json()
    assert src["has_template"] is True
    admin.delete(f"/api/admin/skills/{sid}")


def test_admin_skill_chart_set_and_delete(admin: httpx.Client):
    sid = "test_chart_" + str(int(time.time()))[-6:]
    admin.post("/api/admin/skills", json={
        "id": sid, "skill_md": "---\nid: " + sid + "\n---\n",
        "service_yaml": "service: ZBW_SALES_SRV\nentity_set: SalesByOfficeView\n",
    })
    r = admin.put(f"/api/admin/skills/{sid}/chart", json={
        "chart": {"kind": "bar", "x": "OfficeCode", "y": ["NETWR_F"]},
    })
    assert r.status_code == 200
    src = admin.get(f"/api/admin/skills/{sid}/source").json()
    assert src["has_chart"] is True
    admin.delete(f"/api/admin/skills/{sid}/chart")
    src2 = admin.get(f"/api/admin/skills/{sid}/source").json()
    assert src2["has_chart"] is False
    admin.delete(f"/api/admin/skills/{sid}")


# ============================== quota ==============================


def test_my_quota_endpoint(alice: httpx.Client):
    r = alice.get("/api/quota/me")
    assert r.status_code == 200
    body = r.json()
    assert "usage" in body and "month" in body
    assert "input_tokens" in body["usage"]


def test_admin_set_quota_limit(admin: httpx.Client, client: httpx.Client):
    r = admin.put("/api/admin/quota/quotauser1", json={"monthly_tokens": 50000})
    assert r.status_code == 200
    # 由 quotauser1 自己查
    client.post("/api/auth/login", json={"username": "quotauser1", "password": ""})
    r2 = client.get("/api/quota/me")
    assert r2.json()["limit_tokens"] == 50000


def test_admin_quota_requires_admin(client: httpx.Client):
    client.post("/api/auth/login", json={"username": "stranger", "password": ""})
    assert client.put("/api/admin/quota/x", json={"monthly_tokens": 1}).status_code == 403
    assert client.get("/api/admin/quota").status_code == 403


# ============================== visible_to ==============================


def test_visible_to_hides_for_wrong_role(admin: httpx.Client, client: httpx.Client):
    """创建一个仅 ['finance'] 可见的 skill,普通用户(role=user) 应看不到。"""
    sid = "test_visible_" + str(int(time.time()))[-6:]
    skill_md = (
        f"---\nid: {sid}\ntitle: finance only\nvisible_to: [finance]\n---\n# x\n"
    )
    admin.post("/api/admin/skills", json={
        "id": sid, "skill_md": skill_md,
        "service_yaml": "service: ZBW_FIN_SRV\nentity_set: GLBalance\n",
    })
    # admin 看得到
    admin_ids = {s["id"] for s in admin.get("/api/skills").json()["skills"]}
    assert sid in admin_ids
    # 普通用户看不到 (role=user)
    client.post("/api/auth/login", json={"username": "joe", "password": ""})
    user_ids = {s["id"] for s in client.get("/api/skills").json()["skills"]}
    assert sid not in user_ids
    admin.delete(f"/api/admin/skills/{sid}")
