"""FastAPI 后端入口 (§5 API 设计)。

启动:
    uvicorn app.server:app --host 0.0.0.0 --port 8000 --reload

路由:
  POST   /api/auth/login                登录
  POST   /api/auth/logout               登出
  GET    /api/auth/me                   当前身份
  GET    /api/llm/models                可选模型列表
  POST   /api/llm/model                 切换模型
  GET    /api/skills                    Skill 列表
  GET    /api/skills/{id}               Skill 详情
  POST   /api/skills/{id}/run           跑 Skill (返回 task)
  GET    /api/services                  BW 服务目录
  GET    /api/services/{name}           BW 服务元数据
  POST   /api/chat                      自由对话(走 LLM)
  GET    /api/tasks                     我的任务历史
  GET    /api/tasks/{id}                单任务详情
  GET    /api/tasks/{id}/file           下载 Excel
  GET    /api/audit                     审计 (admin)
  GET    /api/sensitive-fields          敏感字段 (admin)
  POST   /api/sensitive-fields          新增 / 更新
  DELETE /api/sensitive-fields/{svc}/{f} 删除
  GET    /api/status                    系统状态 (BW mode + LLM model + skill count)

前端静态文件:
  /   /assets/*                         web/dist 构建产物
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agent import Agent
from app.auth import (
    AuthError, Identity, clear_credentials, decode_jwt, get_credentials,
    issue_jwt, save_credentials, verify_bw_credentials,
)
from app.bw.factory import make_bw_client
from app.config import load_settings, Settings
from app.db import DB
from app.llm import LLMClient, KNOWN_MODELS, find_model, model_ready
from app.orchestrator import TaskOrchestrator
from app.skills.registry import SkillRegistry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("app.server")


# ============================== 应用状态(单例) ==============================

class AppState:
    settings: Settings
    bw: Any                                # BWClient
    skills: SkillRegistry
    llm: LLMClient
    orchestrator: TaskOrchestrator
    db: DB


STATE = AppState()


def _bootstrap() -> None:
    settings = load_settings()
    errors = settings.validate()
    if errors:
        raise RuntimeError("配置错误:\n  " + "\n  ".join(errors))
    STATE.settings = settings
    STATE.bw = make_bw_client(settings)
    STATE.skills = SkillRegistry(settings.skills_dir)
    STATE.skills.reload()
    STATE.llm = LLMClient(settings.llm)
    STATE.orchestrator = TaskOrchestrator(settings, STATE.bw, STATE.skills)
    STATE.db = DB(settings.output_dir.parent / "app.sqlite3")
    # 注: 不再启动期把 key 灌 os.environ —— 现在 key 按用户隔离,
    #     每次 chat / test 调用时从 DB 取 当前用户 的 key 直接传给 LiteLLM。
    #     .env 中的 *_API_KEY 仍作为 fallback (无个人 key 时用)。
    log.info("[bootstrap] %s", STATE.bw.describe())
    log.info("[bootstrap] %s", STATE.llm.describe())
    log.info("[bootstrap] skills_loaded=%d", len(STATE.skills))


# ============================== FastAPI 实例 ==============================

app = FastAPI(title="SAP BW 智能取数平台", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],   # vite dev
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    _bootstrap()


# ============================== 身份依赖 ==============================

COOKIE_NAME = "bw_session"


def current_identity(request: Request) -> Identity:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    try:
        return decode_jwt(token)
    except AuthError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e))


def require_admin(identity: Identity = Depends(current_identity)) -> Identity:
    if identity.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return identity


# ============================== Pydantic 模型 ==============================

class LoginRequest(BaseModel):
    username: str
    password: str


class SwitchModelRequest(BaseModel):
    """切换当前模型 —— 不接受 api_key / api_base,这两项分别由 §key 管理 + .env 控制。"""
    model: str


class RunSkillRequest(BaseModel):
    params: dict[str, Any]


class ChatRequest(BaseModel):
    message: str


class SensitiveFieldRequest(BaseModel):
    service: str
    field: str
    mask_mode: str           # redact | partial | hash


class ApiKeyRequest(BaseModel):
    value: str               # 明文 key,后端 base64 暂存


# ============================== /api/status ==============================

@app.get("/api/status")
def status_endpoint(request: Request) -> dict[str, Any]:
    ident: Identity | None = None
    token = request.cookies.get(COOKIE_NAME)
    if token:
        try:
            ident = decode_jwt(token)
        except AuthError:
            ident = None
    return {
        "version": "0.2.0",
        "bw_mode": STATE.settings.bw.mode,
        "bw": STATE.bw.describe(),
        "llm": _user_llm_status(ident),
        "skills_count": len(STATE.skills),
    }


# ============================== /api/auth ==============================

@app.post("/api/auth/login")
def auth_login(req: LoginRequest, response: Response, request: Request) -> dict[str, Any]:
    try:
        identity = verify_bw_credentials(STATE.settings.bw, req.username, req.password)
    except AuthError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(e))
    save_credentials(identity.username, req.password)
    STATE.db.upsert_user(identity.username, identity.display_name, identity.role)
    token = issue_jwt(identity)
    response.set_cookie(
        COOKIE_NAME, token,
        httponly=True, samesite="lax",
        max_age=8 * 3600, path="/",
    )
    STATE.db.write_audit(
        username=identity.username, action="login",
        ip=request.client.host if request.client else None,
    )
    return {
        "username": identity.username,
        "display_name": identity.display_name,
        "role": identity.role,
    }


@app.post("/api/auth/logout")
def auth_logout(response: Response, identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    clear_credentials(identity.username)
    response.delete_cookie(COOKIE_NAME, path="/")
    STATE.db.write_audit(username=identity.username, action="logout")
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    return {
        "username": identity.username,
        "display_name": identity.display_name,
        "role": identity.role,
    }


# ============================== /api/llm ==============================

@app.get("/api/llm/models")
def list_llm_models(identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    """带「当前用户能否调用」的视图。

    每个模型的 ready 字段 = 当前用户已配 key OR .env 有 fallback。
    """
    base = _user_llm_status(identity)
    # 顺手标注 key 来源 (user / env / None),供 UI 区分私有 vs 全局兜底
    for m in base["models"]:
        env_var = next((x.api_key_env for x in KNOWN_MODELS if x.id == m["id"]), "")
        if env_var:
            _val, src = _effective_key(identity.username, env_var)
            m["key_source"] = src
    return base


# ---------- LLM API keys (每用户独立) ----------

def _effective_key(username: str, env_var: str) -> tuple[str | None, str | None]:
    """取用户的有效 key —— 优先用户私有 (DB),无则用 .env 全局 fallback。

    Returns (key_value_or_none, source: "user"|"env"|None)
    """
    own = STATE.db.get_user_api_key(username, env_var)
    if own:
        return own, "user"
    fb = os.environ.get(env_var, "").strip()
    if fb:
        return fb, "env"
    return None, None


def _user_llm_status(identity: Identity | None) -> dict[str, Any]:
    """构造「按当前用户视角」的 LLM 状态。

    `/api/status` (匿名可访问) 和 `/api/llm/models` (需登录) 都走这里,
    保证两个端点对 current_ready / models[].ready 的口径完全一致。
    未登录时退化为 .env 全局 fallback 判断。
    """
    if identity is None:
        return STATE.llm.current_status()

    def is_ready(info):
        if not info.api_key_env:
            return True
        key, _src = _effective_key(identity.username, info.api_key_env)
        return bool(key)

    return STATE.llm.current_status(is_ready=is_ready)


@app.get("/api/llm/keys")
def list_llm_keys(identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    """列出当前用户的 key 状态(每个 provider 一行)。"""
    db_meta = {m["env_var"]: m for m in STATE.db.list_user_api_keys_meta(identity.username)}
    providers: dict[str, dict[str, Any]] = {}
    for m in KNOWN_MODELS:
        if not m.api_key_env:
            continue
        if m.api_key_env in providers:
            providers[m.api_key_env]["models"].append(m.id)
            continue
        own_meta = db_meta.get(m.api_key_env)
        env_val = os.environ.get(m.api_key_env, "").strip()
        own_tail = own_meta["tail"] if own_meta else None
        env_tail = env_val[-4:] if env_val else None
        providers[m.api_key_env] = {
            "env_var": m.api_key_env,
            "provider": m.provider,
            "models": [m.id],
            "configured": bool(own_meta or env_val),
            "source": ("user" if own_meta else ("env" if env_val else None)),
            "tail": own_tail or env_tail,
            "updated_at": (own_meta["updated_at"] if own_meta else None),
            "has_personal": bool(own_meta),
            "has_env_fallback": bool(env_val),
        }
    return {"providers": list(providers.values())}


@app.put("/api/llm/keys/{env_var}")
def upsert_llm_key(
    env_var: str,
    req: ApiKeyRequest,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    """写入当前用户自己的 key。所有登录用户都能操作自己的。"""
    value = (req.value or "").strip()
    if not value:
        raise HTTPException(400, "key 不能为空")
    valid = {m.api_key_env for m in KNOWN_MODELS if m.api_key_env}
    if env_var not in valid:
        raise HTTPException(400, f"未知 env_var: {env_var}")
    STATE.db.upsert_user_api_key(identity.username, env_var, value)
    STATE.db.write_audit(
        username=identity.username, action="set_api_key",
        question=f"env_var={env_var}",
    )
    return {"ok": True, "env_var": env_var, "tail": value[-4:]}


@app.post("/api/llm/keys/{env_var}/test")
def test_llm_key(
    env_var: str,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    """对当前 env_var 关联的某个模型发一次最短调用,验证当前用户的 key 真有效。"""
    valid_envs = {m.api_key_env for m in KNOWN_MODELS if m.api_key_env}
    if env_var not in valid_envs:
        raise HTTPException(400, f"未知 env_var: {env_var}")

    target = next((m for m in KNOWN_MODELS if m.api_key_env == env_var), None)
    if target is None:
        raise HTTPException(400, "未找到关联模型")

    key_val, source = _effective_key(identity.username, env_var)
    if not key_val:
        return {
            "ok": False,
            "error": f"{env_var} 未配置 —— 先在上方点「配置」保存你自己的 key",
            "category": "not_configured",
        }

    import litellm
    t0 = time.monotonic()
    try:
        resp = litellm.completion(
            model=target.id,
            messages=[
                {"role": "system", "content": "你是测试助手,只回复'OK'两个字。"},
                {"role": "user", "content": "ping"},
            ],
            timeout=20,
            max_tokens=8,
            api_key=key_val,                # 显式传,不依赖 os.environ
        )
        dt = int((time.monotonic() - t0) * 1000)
        text = (resp.choices[0].message.content or "").strip()
        return {
            "ok": True,
            "model": target.id,
            "key_source": source,
            "latency_ms": dt,
            "reply": text[:50],
        }
    except Exception as e:                              # noqa: BLE001
        dt = int((time.monotonic() - t0) * 1000)
        msg = str(e)
        cat = "other"
        lower = msg.lower()
        if any(k in lower for k in ("auth", "invalid api key", "401", "unauthorized")):
            cat = "auth"
        elif any(k in lower for k in ("timeout", "connect", "network", "dns", "ssl", "10054", "10060")):
            cat = "network"
        elif any(k in lower for k in ("rate limit", "429", "quota")):
            cat = "rate_limit"
        return {
            "ok": False,
            "model": target.id,
            "key_source": source,
            "latency_ms": dt,
            "error": msg[:500],
            "category": cat,
        }


@app.delete("/api/llm/keys/{env_var}")
def delete_llm_key(
    env_var: str,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    """清除当前用户自己的 key。"""
    STATE.db.delete_user_api_key(identity.username, env_var)
    STATE.db.write_audit(
        username=identity.username, action="delete_api_key",
        question=f"env_var={env_var}",
    )
    return {"ok": True}


@app.post("/api/llm/model")
def switch_llm_model(
    req: SwitchModelRequest,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    """切换全局当前模型 —— 不接受 api_key/api_base (key 走 §私人 keys,base 走 .env)。"""
    info = find_model(req.model)
    if info is None:
        log.warning("切换到注册表外的模型: %s", req.model)
    STATE.llm.switch_model(req.model)
    STATE.db.write_audit(
        username=identity.username, action="switch_model",
        llm_model=req.model,
    )
    # 返回视图:用 list_llm_models 的逻辑,带上当前用户的 ready 视图
    return list_llm_models(identity)


# ============================== /api/skills ==============================

@app.get("/api/skills")
def list_skills_endpoint(
    q: str | None = None,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    keywords = [k for k in (q or "").split() if k]
    skills = STATE.skills.list(keywords=keywords or None)
    return {
        "skills": [s.to_summary() for s in skills],
        "total": len(skills),
    }


@app.get("/api/skills/{skill_id}")
def get_skill_endpoint(
    skill_id: str,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    try:
        s = STATE.skills.get(skill_id)
    except KeyError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"未找到 Skill: {skill_id}")
    return s.to_detail()


@app.post("/api/skills/{skill_id}/run")
def run_skill_endpoint(
    skill_id: str,
    req: RunSkillRequest,
    request: Request,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    t0 = time.monotonic()
    task_row = STATE.db.create_task(
        username=identity.username, source="skill",
        skill_id=skill_id, question=f"[skill] {skill_id}", params=req.params,
    )
    result = STATE.orchestrator.run_skill(
        skill_id, req.params,
        username=identity.username, question=f"[skill] {skill_id}",
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    STATE.db.finish_task(
        task_row, status=result.status, error=result.error,
        row_count=result.row_count, latency_ms=latency_ms,
    )
    if result.status == "done" and result.excel:
        STATE.db.add_task_file(
            task_row, filename=result.excel.path.name,
            path=str(result.excel.path), size_bytes=result.excel.size_bytes,
            preview=result.rows_preview[:50],
        )
    STATE.db.write_audit(
        username=identity.username, action="run_skill",
        task_id=task_row,
        service=result.meta.get("service") if result.meta else None,
        odata_url=result.meta.get("odata_url") if result.meta else None,
        row_count=result.row_count, latency_ms=latency_ms,
        ip=request.client.host if request.client else None,
    )
    return {
        "task_id": task_row,
        "status": result.status,
        "error": result.error,
        "row_count": result.row_count,
        "rows_preview": result.rows_preview[:50],
        "excel": {
            "filename": result.excel.path.name,
            "size_bytes": result.excel.size_bytes,
            "download_url": f"/api/tasks/{task_row}/file",
        } if result.excel else None,
        "meta": result.meta,
    }


# ============================== /api/services ==============================

@app.get("/api/services")
def list_services_endpoint(
    q: str | None = None,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    resp = STATE.bw.list_services(search=q, top=100)
    if resp.error:
        raise HTTPException(500, resp.error)
    return resp.json or {"services": [], "count": 0}


@app.get("/api/services/{service}")
def get_service_endpoint(
    service: str,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    resp = STATE.bw.get_metadata(service)
    if resp.error:
        raise HTTPException(resp.status_code or 500, resp.error)
    return resp.json or {}


# ============================== /api/chat ==============================

@app.post("/api/chat")
def chat_endpoint(
    req: ChatRequest,
    request: Request,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    """LLM 驱动的自由对话 (一期同步,二期 SSE 流式)。

    用当前用户私有的 key 调 LLM。
    """
    t0 = time.monotonic()
    task_id = STATE.db.create_task(
        username=identity.username, source="chat",
        question=req.message,
    )
    # 取当前用户的有效 key (个人 > .env fallback)
    cur_env = next(
        (m.api_key_env for m in KNOWN_MODELS if m.id == STATE.llm.model), ""
    )
    user_key, key_source = (None, None)
    if cur_env:
        user_key, key_source = _effective_key(identity.username, cur_env)
    if cur_env and not user_key:
        STATE.db.finish_task(
            task_id, status="failed",
            error=f"当前模型 {STATE.llm.model} 需要 {cur_env},但你还没配置。请到「我的 API Keys」配置或切到本地模型。",
            row_count=0, latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return {
            "task_id": task_id,
            "answer": "",
            "iterations": 0,
            "tool_calls": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "llm_model": STATE.llm.model,
            "error": f"未配置 {cur_env}",
            "error_category": "not_configured",
            "task": None,
        }

    # per-request LLMClient: 共用全局当前 model,key 用当前用户私有
    user_llm = LLMClient(STATE.settings.llm)
    user_llm.model = STATE.llm.model
    user_llm.api_key = user_key
    user_llm.api_base = STATE.llm.api_base
    agent = Agent(
        settings=STATE.settings, llm=user_llm, bw=STATE.bw,
        skills=STATE.skills, orchestrator=STATE.orchestrator,
    )
    result = agent.run(req.message, username=identity.username)
    latency_ms = int((time.monotonic() - t0) * 1000)
    task_status = "done" if result.task and result.task.status == "done" else "failed"
    STATE.db.finish_task(
        task_id, status=task_status,
        error=(result.task.error if result.task and result.task.status != "done" else None),
        row_count=(result.task.row_count if result.task else 0),
        latency_ms=latency_ms,
        llm_model=STATE.llm.model,
        llm_input_tokens=result.total_input_tokens,
        llm_output_tokens=result.total_output_tokens,
    )
    if result.task and result.task.excel:
        STATE.db.add_task_file(
            task_id, filename=result.task.excel.path.name,
            path=str(result.task.excel.path), size_bytes=result.task.excel.size_bytes,
            preview=result.task.rows_preview[:50],
        )
    STATE.db.write_audit(
        username=identity.username, action="chat",
        task_id=task_id, question=req.message,
        row_count=(result.task.row_count if result.task else 0),
        latency_ms=latency_ms,
        llm_model=STATE.llm.model,
        llm_tokens=result.total_input_tokens + result.total_output_tokens,
        ip=request.client.host if request.client else None,
    )
    return {
        "task_id": task_id,
        "answer": result.final_text,
        "iterations": result.iterations,
        "tool_calls": [
            {"name": t.name, "arguments": t.arguments, "is_error": t.is_error}
            for t in result.traces
        ],
        "input_tokens": result.total_input_tokens,
        "output_tokens": result.total_output_tokens,
        "llm_model": STATE.llm.model,
        "task": {
            "status": result.task.status if result.task else "failed",
            "row_count": result.task.row_count if result.task else 0,
            "rows_preview": result.task.rows_preview[:50] if result.task else [],
            "excel": ({
                "filename": result.task.excel.path.name,
                "size_bytes": result.task.excel.size_bytes,
                "download_url": f"/api/tasks/{task_id}/file",
            } if result.task and result.task.excel else None),
        } if result.task else None,
    }


# ============================== /api/tasks ==============================

@app.get("/api/tasks")
def list_tasks_endpoint(
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    rows = STATE.db.list_tasks(identity.username, limit=50)
    return {"tasks": rows, "total": len(rows)}


@app.get("/api/tasks/{task_id}")
def get_task_endpoint(
    task_id: str,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    row = STATE.db.get_task(task_id)
    if not row or row.get("username") != identity.username:
        raise HTTPException(404, "任务不存在")
    return row


@app.get("/api/tasks/{task_id}/file")
def download_task_file(
    task_id: str,
    identity: Identity = Depends(current_identity),
) -> FileResponse:
    row = STATE.db.get_task(task_id)
    if not row or row.get("username") != identity.username:
        raise HTTPException(404, "任务不存在")
    path = row.get("file_path")
    if not path or not Path(path).exists():
        raise HTTPException(404, "文件已被清理或未生成")
    STATE.db.write_audit(
        username=identity.username, action="export",
        task_id=task_id, row_count=row.get("row_count") or 0,
    )
    return FileResponse(
        path, filename=row.get("filename") or "result.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ============================== /api/audit (admin) ==============================

@app.get("/api/audit")
def audit_endpoint(
    username: str | None = None,
    action: str | None = None,
    limit: int = 200,
    _: Identity = Depends(require_admin),
) -> dict[str, Any]:
    rows = STATE.db.list_audit(username=username, action=action, limit=limit)
    return {"audit": rows, "total": len(rows)}


# ============================== /api/sensitive-fields (admin) ==============================

@app.get("/api/sensitive-fields")
def list_sensitive_fields(_: Identity = Depends(require_admin)) -> dict[str, Any]:
    return {"fields": STATE.db.list_sensitive_fields()}


@app.post("/api/sensitive-fields")
def upsert_sensitive_field(
    req: SensitiveFieldRequest,
    identity: Identity = Depends(require_admin),
) -> dict[str, Any]:
    if req.mask_mode not in ("redact", "partial", "hash"):
        raise HTTPException(400, "mask_mode 必须是 redact / partial / hash")
    STATE.db.upsert_sensitive_field(
        service=req.service, field=req.field, mask_mode=req.mask_mode, added_by=identity.username,
    )
    return {"ok": True}


@app.delete("/api/sensitive-fields/{service}/{field}")
def delete_sensitive_field(
    service: str, field: str,
    _: Identity = Depends(require_admin),
) -> dict[str, Any]:
    STATE.db.delete_sensitive_field(service, field)
    return {"ok": True}


# ============================== /api/admin/skills/reload ==============================

@app.post("/api/admin/skills/reload")
def reload_skills(_: Identity = Depends(require_admin)) -> dict[str, Any]:
    n = STATE.skills.reload()
    return {"loaded": n}


# ============================== 静态前端 ==============================

_web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
if _web_dist.exists():
    # Vite 构建产物
    app.mount("/assets", StaticFiles(directory=_web_dist / "assets"), name="assets")

    @app.get("/")
    def _index() -> FileResponse:
        return FileResponse(_web_dist / "index.html")

    @app.get("/{full_path:path}")
    def _spa_fallback(full_path: str) -> FileResponse:
        # SPA 路由 fallback —— 任何非 /api 路径都返回 index.html
        if full_path.startswith("api/"):
            raise HTTPException(404, "API 不存在")
        target = _web_dist / full_path
        if target.exists() and target.is_file():
            return FileResponse(target)
        return FileResponse(_web_dist / "index.html")
else:
    @app.get("/")
    def _no_frontend() -> dict[str, Any]:
        return {
            "message": "前端尚未构建。请运行 `cd web && npm install && npm run build`",
            "api_status": "/api/status",
        }
