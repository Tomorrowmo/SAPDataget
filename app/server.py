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

import asyncio
import json
import logging
import os
import re
import time
from xml.etree import ElementTree as ET
from pathlib import Path
from typing import Any

import requests
from fastapi import (
    Cookie, Depends, FastAPI, File, HTTPException, Request,
    Response, UploadFile, status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.agent import Agent
from app.agent_stream import StreamAgent
from app.task_bus import BUS as TASK_BUS
from app.auth import (
    AuthError, Identity, clear_credentials, decode_jwt, get_credentials,
    issue_jwt, save_credentials,
)
from app.bw.live import LiveBWClient
from app.bw.factory import make_bw_client
from app.config import BWSettings, load_settings, Settings
from app.db import DB
from app.llm import LLMClient, KNOWN_MODELS, find_model, model_ready
from app.orchestrator import TaskOrchestrator
from app.query_limits import parse_requested_top
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
    STATE.db = DB(settings.output_dir.parent / "app.sqlite3")

    def _resolve_sensitive(svc: str) -> dict[str, str]:
        # 从 DB 取该 service 的所有敏感字段 → {field: mask_mode}
        out: dict[str, str] = {}
        for row in STATE.db.list_sensitive_fields():
            if row.get("service") == svc:
                out[row["field"]] = row["mask_mode"]
        return out

    STATE.orchestrator = TaskOrchestrator(
        settings, STATE.bw, STATE.skills,
        sensitive_fields_resolver=_resolve_sensitive,
    )
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
REPORT_LIST_SERVICE = os.environ.get("REPORT_LIST_SERVICE", "ZBW_QUERY_LIST_SRV")
REPORT_LIST_ENTITY_SET = os.environ.get("REPORT_LIST_ENTITY_SET", "LtResultSet")
REPORT_LIST_SKILL_ID = os.environ.get("REPORT_LIST_SKILL_ID", "report_list")
REPORT_LIST_URL = os.environ.get(
    "REPORT_LIST_URL",
    "http://sapbd1app01.cn.schneider-electric.com:8000/sap/opu/odata/sap/ZBW_QUERY_LIST_SRV/LtResultSet",
)

try:
    REPORT_LIST_DEFAULT_TOP = max(1, int(os.environ.get("REPORT_LIST_DEFAULT_TOP", "200")))
except ValueError:
    REPORT_LIST_DEFAULT_TOP = 200
try:
    REPORT_LIST_MAX_TOP = max(REPORT_LIST_DEFAULT_TOP, int(os.environ.get("REPORT_LIST_MAX_TOP", "2000")))
except ValueError:
    REPORT_LIST_MAX_TOP = 2000

REPORT_LIST_QUERY_RE = re.compile(
    os.environ.get(
        "REPORT_LIST_QUERY_RE",
        r"报告(清单|列表)|报表(清单|列表)|report\s*list|query\s*list",
    ),
    re.IGNORECASE,
)


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


def _is_report_list_query(message: str) -> bool:
    return bool(REPORT_LIST_QUERY_RE.search(message.strip()))


def _extract_report_list_top(message: str) -> int:
    return parse_requested_top(
        message,
        default_top=REPORT_LIST_DEFAULT_TOP,
        max_top=REPORT_LIST_MAX_TOP,
    )


def _parse_report_list_xml(xml_text: str) -> tuple[list[dict[str, Any]], int | None]:
    """Parse OData Atom/XML rows and optional total count."""
    text = (xml_text or "").strip()
    if not text.startswith("<"):
        return [], None
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return [], None

    rows: list[dict[str, Any]] = []
    total_count: int | None = None

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    for node in root.iter():
        local = _local(node.tag)
        if local == "count" and total_count is None:
            try:
                total_count = int((node.text or "").strip())
            except ValueError:
                total_count = None
        if local != "entry":
            continue
        props = None
        for child in node.iter():
            if _local(child.tag) == "properties":
                props = child
                break
        if props is None:
            continue

        row: dict[str, Any] = {}
        for prop in list(props):
            key = _local(prop.tag)
            is_null = any(
                attr_name.endswith("}null") and str(attr_val).lower() == "true"
                for attr_name, attr_val in prop.attrib.items()
            )
            row[key] = None if is_null else (prop.text or "")
        if row:
            rows.append(row)

    return rows, total_count


def _fixed_odata_query_params() -> dict[str, str]:
    params: dict[str, str] = {}
    if STATE.settings.bw.client:
        params["sap-client"] = STATE.settings.bw.client
    if STATE.settings.bw.language:
        params["sap-language"] = STATE.settings.bw.language
    return params


def _get_fixed_odata_with_fallback(username: str, password: str):
    params = _fixed_odata_query_params()
    headers = {"Accept": "application/atom+xml,application/xml,text/xml"}
    timeout = STATE.settings.bw.timeout
    verify = STATE.settings.bw.verify_ssl

    r = requests.get(
        REPORT_LIST_URL,
        auth=(username, password),
        headers=headers,
        params=params,
        timeout=timeout,
        verify=verify,
    )
    # 兼容某些系统不接受 sap-client 的情况：失败时去掉 sap-client 再试一次。
    if "sap-client" in params and r.status_code in (401, 403, 404):
        fallback = {k: v for k, v in params.items() if k != "sap-client"}
        r = requests.get(
            REPORT_LIST_URL,
            auth=(username, password),
            headers=headers,
            params=fallback,
            timeout=timeout,
            verify=verify,
        )
    return r


def _bw_client_for_identity(identity: Identity):
    if STATE.settings.bw.mode != "live":
        return STATE.bw
    password = get_credentials(identity.username, cred_id=identity.cred_id, db=STATE.db)
    if not password:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "BW 凭据已失效,请重新登录")
    settings = BWSettings(
        mode="live",
        mock_data_dir=STATE.settings.bw.mock_data_dir,
        mock_latency_ms=STATE.settings.bw.mock_latency_ms,
        base_url=STATE.settings.bw.base_url,
        username=identity.username,
        password=password,
        client=STATE.settings.bw.client,
        language=STATE.settings.bw.language,
        verify_ssl=STATE.settings.bw.verify_ssl,
        timeout=STATE.settings.bw.timeout,
        client_fallback=STATE.settings.bw.client_fallback,
        max_export_rows=STATE.settings.bw.max_export_rows,
    )
    return LiveBWClient(settings)


def _run_report_list_shortcut(
    *,
    task_id: str,
    req: ChatRequest,
    request: Request,
    identity: Identity,
    t0: float,
) -> dict[str, Any]:
    top_n = _extract_report_list_top(req.message)
    bw_client = _bw_client_for_identity(identity)

    # live 模式: 按用户要求固定调用该 URL,先解析 OData XML,再取 5 条。
    if STATE.settings.bw.mode == "live":
        password = get_credentials(identity.username, cred_id=identity.cred_id, db=STATE.db)
        if not password:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "BW 凭据已失效,请重新登录")

        try:
            raw_resp = _get_fixed_odata_with_fallback(identity.username, password)
        except requests.RequestException as e:
            raw_resp = None
            raw_error = f"请求异常: {e}"
        else:
            raw_error = None

        latency_ms = int((time.monotonic() - t0) * 1000)
        if raw_resp is None:
            STATE.db.finish_task(
                task_id,
                status="failed",
                error=raw_error,
                row_count=0,
                latency_ms=latency_ms,
            )
            return {
                "task_id": task_id,
                "answer": f"查询报告清单失败: {raw_error}",
                "iterations": 1,
                "tool_calls": [{
                    "name": "fetch_report_list_fixed_url",
                    "arguments": {"url": REPORT_LIST_URL},
                    "is_error": True,
                }],
                "input_tokens": 0,
                "output_tokens": 0,
                "llm_model": "builtin/report-list",
                "task": {
                    "status": "failed",
                    "row_count": 0,
                    "rows_preview": [],
                    "excel": None,
                },
            }

        if raw_resp.status_code in (401, 403):
            msg = f"HTTP {raw_resp.status_code}（用户名或密码无效，或无权限访问该 OData）"
            STATE.db.finish_task(
                task_id,
                status="failed",
                error=msg,
                row_count=0,
                latency_ms=latency_ms,
            )
            return {
                "task_id": task_id,
                "answer": f"查询报告清单失败: {msg}",
                "iterations": 1,
                "tool_calls": [{
                    "name": "fetch_report_list_fixed_url",
                    "arguments": {"url": REPORT_LIST_URL},
                    "is_error": True,
                }],
                "input_tokens": 0,
                "output_tokens": 0,
                "llm_model": "builtin/report-list",
                "task": {
                    "status": "failed",
                    "row_count": 0,
                    "rows_preview": [],
                    "excel": None,
                },
            }

        rows_all, total_count = _parse_report_list_xml(raw_resp.text)
        if not rows_all:
            msg = f"固定 URL 返回非预期内容，无法解析 XML（HTTP {raw_resp.status_code}）"
            STATE.db.finish_task(
                task_id,
                status="failed",
                error=msg,
                row_count=0,
                latency_ms=latency_ms,
            )
            return {
                "task_id": task_id,
                "answer": f"查询报告清单失败: {msg}",
                "iterations": 1,
                "tool_calls": [{
                    "name": "fetch_report_list_fixed_url",
                    "arguments": {"url": REPORT_LIST_URL},
                    "is_error": True,
                }],
                "input_tokens": 0,
                "output_tokens": 0,
                "llm_model": "builtin/report-list",
                "task": {
                    "status": "failed",
                    "row_count": 0,
                    "rows_preview": [],
                    "excel": None,
                },
            }

        # P2-4: 用解析到的 top_n 截断(与 mock 路径一致),而非硬编码 5 条。
        rows = rows_all[:top_n]
        row_count_total = total_count if total_count is not None else len(rows_all)
        columns = list(rows[0].keys()) if rows else ["ReportID", "ReportDescription"]
        free_result = STATE.orchestrator.run_free_query(
            service=REPORT_LIST_SERVICE,
            entity_set=REPORT_LIST_ENTITY_SET,
            columns=columns,
            rows=rows,
            info={
                "question": req.message,
                "service": REPORT_LIST_SERVICE,
                "entity_set": REPORT_LIST_ENTITY_SET,
                "odata_url": REPORT_LIST_URL,
                "row_count": len(rows),
                "row_count_total": row_count_total,
                "latency_ms": latency_ms,
            },
            sheet_title="报告清单",
            username=identity.username,
        )
        answer = (
            f"固定 OData 链接解析完成，共 {row_count_total} 条，"
            f"现返回其中 {len(rows)} 条，并已生成 Excel。"
        )
        STATE.db.finish_task(
            task_id,
            status="done",
            error=None,
            row_count=len(rows),
            latency_ms=latency_ms,
            llm_model="builtin/report-list",
            llm_input_tokens=0,
            llm_output_tokens=0,
        )
        if free_result.excel:
            STATE.db.add_task_file(
                task_id,
                filename=free_result.excel.path.name,
                path=str(free_result.excel.path),
                size_bytes=free_result.excel.size_bytes,
                preview=rows,
            )
        STATE.db.add_task_message(
            task_id,
            role="assistant",
            text=answer,
            blocks={"tool_calls": [{
                "name": "fetch_report_list_fixed_url",
                "arguments": {"url": REPORT_LIST_URL},
                "is_error": False,
            }], "task": {
                "status": "done",
                "excel_filename": free_result.excel.path.name if free_result.excel else None,
                "row_count": len(rows),
            }},
        )
        TASK_BUS.publish(task_id, {
            "type": "assistant_message",
            "text": answer,
            "iterations": 1,
        })
        STATE.db.write_audit(
            username=identity.username,
            action="chat",
            task_id=task_id,
            question=req.message,
            service=REPORT_LIST_SERVICE,
            odata_url=REPORT_LIST_URL,
            row_count=len(rows),
            latency_ms=latency_ms,
            llm_model="builtin/report-list",
            llm_tokens=0,
            ip=request.client.host if request.client else None,
        )
        return {
            "task_id": task_id,
            "answer": answer,
            "iterations": 1,
            "tool_calls": [{
                "name": "fetch_report_list_fixed_url",
                "arguments": {"url": REPORT_LIST_URL},
                "is_error": False,
            }],
            "input_tokens": 0,
            "output_tokens": 0,
            "llm_model": "builtin/report-list",
            "task": {
                "status": "done",
                "row_count": len(rows),
                "rows_preview": rows,
                "excel": ({
                    "filename": free_result.excel.path.name,
                    "size_bytes": free_result.excel.size_bytes,
                    "download_url": f"/api/tasks/{task_id}/file",
                } if free_result.excel else None),
            },
        }

    # 优先走 Skill（统一参数校验、Excel、审计字段），若未配置 report_list skill 再回退直查。
    result = None
    try:
        scoped_orchestrator = TaskOrchestrator(
            STATE.settings,
            bw_client,
            STATE.skills,
            sensitive_fields_resolver=STATE.orchestrator._sensitive_resolver,
        )
        result = scoped_orchestrator.run_skill(
            REPORT_LIST_SKILL_ID,
            {"top_n": top_n},
            username=identity.username,
            question=req.message,
        )
    except Exception as e:                              # noqa: BLE001
        log.warning("report_list skill 执行异常,回退直查: %s", e)

    latency_ms = int((time.monotonic() - t0) * 1000)
    if result and result.status == "done" and result.excel:
        preview_rows = result.rows_preview[:50]
        row_count = result.row_count or len(preview_rows)
        answer = (
            f"已查询到报告清单，共 {row_count} 条，"
            f"已生成 Excel，下面展示前 {len(preview_rows)} 条。"
        )
        STATE.db.finish_task(
            task_id,
            status="done",
            error=None,
            row_count=row_count,
            latency_ms=latency_ms,
            llm_model="builtin/report-list",
            llm_input_tokens=0,
            llm_output_tokens=0,
        )
        STATE.db.add_task_file(
            task_id,
            filename=result.excel.path.name,
            path=str(result.excel.path),
            size_bytes=result.excel.size_bytes,
            preview=preview_rows,
        )
        STATE.db.add_task_message(
            task_id,
            role="assistant",
            text=answer,
            blocks={"tool_calls": [{
                "name": "run_skill",
                "arguments": {"skill_id": REPORT_LIST_SKILL_ID, "params": {"top_n": top_n}},
                "is_error": False,
            }], "task": {
                "status": "done",
                "excel_filename": result.excel.path.name,
                "row_count": row_count,
            }},
        )
        TASK_BUS.publish(task_id, {
            "type": "assistant_message",
            "text": answer,
            "iterations": 1,
        })
        STATE.db.write_audit(
            username=identity.username,
            action="chat",
            task_id=task_id,
            question=req.message,
            service=result.meta.get("service") if result.meta else REPORT_LIST_SERVICE,
            odata_url=result.meta.get("odata_url") if result.meta else None,
            row_count=row_count,
            latency_ms=latency_ms,
            llm_model="builtin/report-list",
            llm_tokens=0,
            ip=request.client.host if request.client else None,
        )
        return {
            "task_id": task_id,
            "answer": answer,
            "iterations": 1,
            "tool_calls": [{
                "name": "run_skill",
                "arguments": {"skill_id": REPORT_LIST_SKILL_ID, "params": {"top_n": top_n}},
                "is_error": False,
            }],
            "input_tokens": 0,
            "output_tokens": 0,
            "llm_model": "builtin/report-list",
            "task": {
                "status": "done",
                "row_count": row_count,
                "rows_preview": preview_rows,
                "excel": {
                    "filename": result.excel.path.name,
                    "size_bytes": result.excel.size_bytes,
                    "download_url": f"/api/tasks/{task_id}/file",
                },
            },
        }

    resp = bw_client.execute_query(
        REPORT_LIST_SERVICE,
        REPORT_LIST_ENTITY_SET,
        top=top_n,
        count=True,
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    if not resp.ok:
        STATE.db.finish_task(
            task_id,
            status="failed",
            error=resp.error or f"HTTP {resp.status_code}",
            row_count=0,
            latency_ms=latency_ms,
        )
        STATE.db.add_task_message(
            task_id,
            role="assistant",
            text=f"查询报告清单失败: {resp.error or f'HTTP {resp.status_code}'}",
            blocks={"tool_calls": [{
                "name": "fetch_report_list",
                "arguments": {"service": REPORT_LIST_SERVICE, "entity_set": REPORT_LIST_ENTITY_SET, "top": top_n},
                "is_error": True,
            }]},
        )
        return {
            "task_id": task_id,
            "answer": f"查询报告清单失败: {resp.error or f'HTTP {resp.status_code}'}",
            "iterations": 1,
            "tool_calls": [{
                "name": "fetch_report_list",
                "arguments": {"service": REPORT_LIST_SERVICE, "entity_set": REPORT_LIST_ENTITY_SET, "top": top_n},
                "is_error": True,
            }],
            "input_tokens": 0,
            "output_tokens": 0,
            "llm_model": "builtin/report-list",
            "task": {
                "status": "failed",
                "row_count": 0,
                "rows_preview": [],
                "excel": None,
            },
        }

    payload = resp.json or {}
    rows = payload.get("rows", [])
    row_count = int(payload.get("row_count_total") or payload.get("row_count_returned") or len(rows))
    columns = list(rows[0].keys()) if rows else ["ReportID", "ReportDescription"]
    free_result = STATE.orchestrator.run_free_query(
        service=REPORT_LIST_SERVICE,
        entity_set=REPORT_LIST_ENTITY_SET,
        columns=columns,
        rows=rows,
        info={
            "question": req.message,
            "service": REPORT_LIST_SERVICE,
            "entity_set": REPORT_LIST_ENTITY_SET,
            "odata_url": resp.url,
            "row_count": row_count,
            "latency_ms": latency_ms,
        },
        sheet_title="报告清单",
        username=identity.username,
    )
    preview_rows = rows[:50]
    answer = (
        f"已查询到报告清单，共 {row_count} 条，"
        f"已生成 Excel，下面展示前 {len(preview_rows)} 条。"
    )
    STATE.db.finish_task(
        task_id,
        status="done",
        error=None,
        row_count=row_count,
        latency_ms=latency_ms,
        llm_model="builtin/report-list",
        llm_input_tokens=0,
        llm_output_tokens=0,
    )
    if free_result.excel:
        STATE.db.add_task_file(
            task_id,
            filename=free_result.excel.path.name,
            path=str(free_result.excel.path),
            size_bytes=free_result.excel.size_bytes,
            preview=preview_rows,
        )
    STATE.db.add_task_message(
        task_id,
        role="assistant",
        text=answer,
        blocks={"tool_calls": [{
            "name": "fetch_report_list",
            "arguments": {"service": REPORT_LIST_SERVICE, "entity_set": REPORT_LIST_ENTITY_SET, "top": top_n},
            "is_error": False,
        }], "task": {
            "status": "done",
            "excel_filename": free_result.excel.path.name if free_result.excel else None,
            "row_count": row_count,
        }},
    )
    TASK_BUS.publish(task_id, {
        "type": "assistant_message",
        "text": answer,
        "iterations": 1,
    })
    STATE.db.write_audit(
        username=identity.username,
        action="chat",
        task_id=task_id,
        question=req.message,
        service=REPORT_LIST_SERVICE,
        odata_url=resp.url,
        row_count=row_count,
        latency_ms=latency_ms,
        llm_model="builtin/report-list",
        llm_tokens=0,
        ip=request.client.host if request.client else None,
    )
    return {
        "task_id": task_id,
        "answer": answer,
        "iterations": 1,
        "tool_calls": [{
            "name": "fetch_report_list",
            "arguments": {"service": REPORT_LIST_SERVICE, "entity_set": REPORT_LIST_ENTITY_SET, "top": top_n},
            "is_error": False,
        }],
        "input_tokens": 0,
        "output_tokens": 0,
        "llm_model": "builtin/report-list",
        "task": {
            "status": "done",
            "row_count": row_count,
            "rows_preview": preview_rows,
            "excel": ({
                "filename": free_result.excel.path.name,
                "size_bytes": free_result.excel.size_bytes,
                "download_url": f"/api/tasks/{task_id}/file",
            } if free_result.excel else None),
        },
    }


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
    task_id: str | None = None       # 提供则继续该 task 的多轮 (P1-11)


class SensitiveFieldRequest(BaseModel):
    service: str
    field: str
    mask_mode: str           # redact | partial | hash


class ApiKeyRequest(BaseModel):
    value: str               # 明文 key,后端 base64 暂存


class LlmSettingsRequest(BaseModel):
    """DataAgent 式三元组。api_key: None=保持原值, ""=清空, 非空=更新。"""
    api_key: str | None = None
    base_url: str = ""
    model: str = ""


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
    # 登录阶段不做 SAP 权限校验，仅创建本地会话并保存凭据；
    # 真实 OData 请求时再使用该凭据访问 SAP。
    username = (req.username or "").strip() or "demo"

    identity = Identity(
        username=username,
        display_name=username,
        role="admin" if username == "admin" else "user",
    )
    cred_id = save_credentials(identity.username, req.password, db=STATE.db)
    identity.cred_id = cred_id
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
    clear_credentials(identity.username, cred_id=identity.cred_id, db=STATE.db)
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

# ---------- 每用户 LLM 配置 (DataAgent 式 BYOK: key + base_url + model 三元组) ----------

def _resolve_user_llm(identity: Identity | None) -> dict[str, Any]:
    """解析当前用户的有效 LLM 配置:用户三元组优先,留空字段回退 .env 默认。

    .env 默认来自 Settings.llm(LLM_MODEL / LLM_API_KEY / LLM_API_BASE)。
    ready = 有 model 且 (有 key 或 有 base_url —— 本地 ollama 等无 key 端点也算就绪)。
    """
    env_model = STATE.settings.llm.model or ""
    env_key = STATE.settings.llm.api_key or ""
    env_base = STATE.settings.llm.api_base or ""

    user = STATE.db.get_user_llm_settings(identity.username) if identity else None
    u_key = (user or {}).get("api_key") or ""
    u_base = (user or {}).get("base_url") or ""
    u_model = (user or {}).get("model") or ""

    model = u_model or env_model
    base_url = u_base or env_base
    if u_key:
        api_key, key_source = u_key, "user"
    elif env_key:
        api_key, key_source = env_key, "env"
    else:
        api_key, key_source = "", None

    return {
        "model": model,
        "api_key": api_key,
        "base_url": base_url,
        "key_source": key_source,
        "ready": bool(model) and (bool(api_key) or bool(base_url)),
        "has_user_key": bool(u_key),
        "user_model": u_model,
        "user_base_url": u_base,
        "env_has_key": bool(env_key),
        "env_model": env_model,
        "env_base_url": env_base,
        "updated_at": (user or {}).get("updated_at"),
    }


def _build_user_llm(identity: Identity | None) -> LLMClient:
    """据 _resolve_user_llm 造一个 per-request LLMClient。"""
    r = _resolve_user_llm(identity)
    c = LLMClient(STATE.settings.llm)
    c.model = r["model"]
    c.api_key = r["api_key"] or None
    c.api_base = r["base_url"] or None
    return c


def _user_llm_status(identity: Identity | None) -> dict[str, Any]:
    """`/api/status` 用的 LLM 状态块(沿用旧字段名,前端无需改 current/current_ready)。
    models 已弃用(取消模型注册表/选择器),固定空列表。"""
    r = _resolve_user_llm(identity)
    return {
        "current": r["model"],
        "current_display": r["model"] or "(未配置)",
        "current_ready": r["ready"],
        "models": [],
    }


@app.get("/api/llm/settings")
def get_llm_settings(identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    """当前用户的 LLM 设置三元组 + .env 兜底信息 + 模型示例(供下拉)。
    key 只回 has_key 布尔,绝不回真值。"""
    r = _resolve_user_llm(identity)
    return {
        "has_key": r["has_user_key"],
        "base_url": r["user_base_url"],
        "model": r["user_model"],
        "effective_model": r["model"],
        "effective_ready": r["ready"],
        "key_source": r["key_source"],
        "env_has_key": r["env_has_key"],
        "env_model": r["env_model"],
        "env_base_url": r["env_base_url"],
        "updated_at": r["updated_at"],
        "suggestions": [
            {"id": m.id, "display": m.display, "notes": m.notes,
             "location": m.location, "cost": m.cost}
            for m in KNOWN_MODELS
        ],
    }


@app.put("/api/llm/settings")
def put_llm_settings(
    req: LlmSettingsRequest,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    """保存当前用户的三元组。api_key=None→保持原值;""→清空;非空→更新。
    base_url / model 总是按表单值写入(空串=清空,回退 .env)。"""
    STATE.db.set_user_llm_settings(
        identity.username,
        api_key=req.api_key,
        base_url=req.base_url,
        model=req.model,
    )
    STATE.db.write_audit(
        username=identity.username, action="set_llm_settings",
        question=f"model={req.model or '(env)'} base_url={'set' if req.base_url else '(env)'}",
    )
    return get_llm_settings(identity)


@app.post("/api/llm/settings/test")
def test_llm_settings(identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    """用当前生效配置发一次最短调用,验证 key/端点/模型真能用。"""
    r = _resolve_user_llm(identity)
    if not r["ready"]:
        return {
            "ok": False, "category": "not_configured",
            "error": "尚未配置:至少需要 Model + (API key 或 Base URL)。",
        }
    client = _build_user_llm(identity)
    t0 = time.monotonic()
    try:
        resp = client.complete(
            messages=[
                {"role": "system", "content": "你是测试助手,只回复'OK'两个字。"},
                {"role": "user", "content": "ping"},
            ],
            max_tokens=8,
        )
        dt = int((time.monotonic() - t0) * 1000)
        return {
            "ok": True, "model": r["model"], "key_source": r["key_source"],
            "latency_ms": dt, "reply": (resp.text or "").strip()[:50],
        }
    except Exception as e:                              # noqa: BLE001
        dt = int((time.monotonic() - t0) * 1000)
        msg = str(e); lower = msg.lower(); cat = "other"
        if any(k in lower for k in ("auth", "invalid api key", "401", "unauthorized")):
            cat = "auth"
        elif any(k in lower for k in ("timeout", "connect", "network", "dns", "ssl", "10054", "10060", "getaddrinfo")):
            cat = "network"
        elif any(k in lower for k in ("rate limit", "429", "quota")):
            cat = "rate_limit"
        return {
            "ok": False, "model": r["model"], "key_source": r["key_source"],
            "latency_ms": dt, "error": msg[:500], "category": cat,
        }


# ============================== /api/skills ==============================

@app.get("/api/skills")
def list_skills_endpoint(
    q: str | None = None,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    keywords = [k for k in (q or "").split() if k]
    # admin 看全部;非 admin 按 role 过滤 visible_to (§9.2 / P1-12)
    role = None if identity.role == "admin" else identity.role
    skills = STATE.skills.list(keywords=keywords or None, role=role)
    # 注入 status + favorite 标记
    statuses = {(r["skill_id"], r["version"]): r["status"]
                for r in STATE.db.list_skill_statuses()}
    fav_ids = {f["ref_id"] for f in STATE.db.list_favorites(identity.username, "skill")}
    items: list[dict[str, Any]] = []
    for s in skills:
        st = statuses.get((s.id, s.version), "active")
        # 非 admin 不展示 archived
        if st == "archived" and identity.role != "admin":
            continue
        d = s.to_summary()
        d["status"] = st
        d["favorite"] = s.id in fav_ids
        items.append(d)
    return {"skills": items, "total": len(items)}


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


def _run_skill_task(
    *,
    skill_id: str,
    params: dict[str, Any],
    username: str,
    source: str = "skill",
    parent_task_id: str | None = None,
    ip: str | None = None,
) -> dict[str, Any]:
    """共用工作流: 创建 task → 跑 → 写文件 → 写审计 → 返回响应 dict。"""
    t0 = time.monotonic()
    task_id = STATE.db.create_task(
        username=username, source=source,
        skill_id=skill_id, question=f"[skill] {skill_id}", params=params,
    )
    # parent_task_id 落库 (用于重跑追溯)
    if parent_task_id:
        with STATE.db.cursor() as cur:
            cur.execute("UPDATE tasks SET parent_task_id=? WHERE id=?",
                        (parent_task_id, task_id))

    TASK_BUS.publish(task_id, {"type": "started", "skill_id": skill_id, "params": params})
    TASK_BUS.publish(task_id, {"type": "progress", "step": "running_skill"})

    result = STATE.orchestrator.run_skill(
        skill_id, params,
        username=username, question=f"[skill] {skill_id}",
    )
    latency_ms = int((time.monotonic() - t0) * 1000)
    STATE.db.finish_task(
        task_id, status=result.status, error=result.error,
        row_count=result.row_count, latency_ms=latency_ms,
    )
    if result.status == "done" and result.excel:
        STATE.db.add_task_file(
            task_id, filename=result.excel.path.name,
            path=str(result.excel.path), size_bytes=result.excel.size_bytes,
            preview=result.rows_preview[:50],
        )
    STATE.db.write_audit(
        username=username, action="run_skill",
        task_id=task_id,
        service=result.meta.get("service") if result.meta else None,
        odata_url=result.meta.get("odata_url") if result.meta else None,
        row_count=result.row_count, latency_ms=latency_ms,
        ip=ip,
    )

    payload = {
        "task_id": task_id,
        "status": result.status,
        "error": result.error,
        "row_count": result.row_count,
        "rows_preview": result.rows_preview[:50],
        "excel": {
            "filename": result.excel.path.name,
            "size_bytes": result.excel.size_bytes,
            "download_url": f"/api/tasks/{task_id}/file",
        } if result.excel else None,
        "meta": result.meta,
    }
    TASK_BUS.publish(task_id, {
        "type": result.status,
        "row_count": result.row_count,
        "error": result.error,
        "excel_filename": result.excel.path.name if result.excel else None,
    })
    return payload


@app.post("/api/skills/{skill_id}/run")
def run_skill_endpoint(
    skill_id: str,
    req: RunSkillRequest,
    request: Request,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    return _run_skill_task(
        skill_id=skill_id, params=req.params,
        username=identity.username,
        ip=request.client.host if request.client else None,
    )


# ============================== /api/services ==============================

@app.get("/api/services")
def list_services_endpoint(
    q: str | None = None,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    bw_client = _bw_client_for_identity(identity)
    resp = bw_client.list_services(search=q, top=100)
    if resp.error:
        raise HTTPException(500, resp.error)
    return resp.json or {"services": [], "count": 0}


@app.get("/api/services/{service}")
def get_service_endpoint(
    service: str,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    bw_client = _bw_client_for_identity(identity)
    resp = bw_client.get_metadata(service)
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
    """LLM 驱动的自由对话 (P0-6 / P1-11)。

    多轮:若 ``task_id`` 已提供且属于当前用户 → 在同一 task 上追加;否则新建 task。
    每条 user / assistant 消息都落 ``task_messages`` 表,供 GET /api/tasks/{id}/messages 查询。
    """
    t0 = time.monotonic()
    # 配额拦截 (P2-20)
    from datetime import UTC, datetime as _dt
    month = _dt.now(UTC).strftime("%Y-%m")
    limit = STATE.db.get_user_quota_limit(identity.username)
    if limit is not None:
        used = STATE.db.get_user_quota_usage(identity.username, month)
        if used["input_tokens"] + used["output_tokens"] >= limit:
            raise HTTPException(429, f"本月 LLM token 配额已用完 ({limit})。请联系管理员调整。")

    # 多轮: 若用户带了 task_id 且属于自己,沿用;否则新建
    task_id: str | None = None
    if req.task_id:
        row = STATE.db.get_task(req.task_id)
        if row and row.get("username") == identity.username and row.get("source") == "chat":
            task_id = req.task_id
    if not task_id:
        task_id = STATE.db.create_task(
            username=identity.username, source="chat",
            question=req.message,
        )

    # 落用户消息
    STATE.db.add_task_message(task_id, role="user", text=req.message)
    TASK_BUS.publish(task_id, {"type": "user_message", "text": req.message})
    if _is_report_list_query(req.message):
        return _run_report_list_shortcut(
            task_id=task_id,
            req=req,
            request=request,
            identity=identity,
            t0=t0,
        )
    # 取当前用户的有效 LLM 配置 (用户三元组 > .env 兜底)
    cfg = _resolve_user_llm(identity)
    if not cfg["ready"]:
        STATE.db.finish_task(
            task_id, status="failed",
            error=f"模型 {cfg['model'] or '(未设置)'} 未就绪:请到「🔑 LLM 设置」配置 API key + Base URL + Model,或在 .env 设默认。",
            row_count=0, latency_ms=int((time.monotonic() - t0) * 1000),
        )
        return {
            "task_id": task_id,
            "answer": "",
            "iterations": 0,
            "tool_calls": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "llm_model": cfg["model"],
            "error": "LLM 未配置",
            "error_category": "not_configured",
            "task": None,
        }

    # per-request LLMClient: 用当前用户的三元组
    user_llm = _build_user_llm(identity)
    # 加载历史 (排除刚加进去的当前 user 消息)
    prior_msgs = STATE.db.list_task_messages(task_id)
    history_text: list[tuple[str, str]] = []
    for m in prior_msgs[:-1]:                         # 最后一条是刚加的 user message
        if m.get("text"):
            history_text.append((m["role"], m["text"]))

    agent = Agent(
        settings=STATE.settings, llm=user_llm, bw=STATE.bw,
        skills=STATE.skills, orchestrator=STATE.orchestrator,
        on_event=lambda kind, payload: TASK_BUS.publish(task_id, {"type": kind, **payload}),
    )
    result = agent.run(req.message, username=identity.username, history=history_text)
    latency_ms = int((time.monotonic() - t0) * 1000)
    task_status = "done" if result.task and result.task.status == "done" else "failed"
    STATE.db.finish_task(
        task_id, status=task_status,
        error=(result.task.error if result.task and result.task.status != "done" else None),
        row_count=(result.task.row_count if result.task else 0),
        latency_ms=latency_ms,
        llm_model=user_llm.model,
        llm_input_tokens=result.total_input_tokens,
        llm_output_tokens=result.total_output_tokens,
    )
    if result.task and result.task.excel:
        STATE.db.add_task_file(
            task_id, filename=result.task.excel.path.name,
            path=str(result.task.excel.path), size_bytes=result.task.excel.size_bytes,
            preview=result.task.rows_preview[:50],
        )

    # 落 assistant 消息 (含 tool_calls 块)
    STATE.db.add_task_message(
        task_id, role="assistant", text=result.final_text,
        blocks={"tool_calls": [
            {"name": t.name, "arguments": t.arguments, "is_error": t.is_error}
            for t in result.traces
        ], "task": {
            "status": result.task.status if result.task else "failed",
            "excel_filename": result.task.excel.path.name if result.task and result.task.excel else None,
            "row_count": result.task.row_count if result.task else 0,
        } if result.task else None},
    )
    TASK_BUS.publish(task_id, {
        "type": "assistant_message", "text": result.final_text,
        "iterations": result.iterations,
    })

    # 配额累加
    STATE.db.add_user_quota_usage(
        identity.username, month,
        input_tokens=result.total_input_tokens,
        output_tokens=result.total_output_tokens,
    )

    STATE.db.write_audit(
        username=identity.username, action="chat",
        task_id=task_id, question=req.message,
        row_count=(result.task.row_count if result.task else 0),
        latency_ms=latency_ms,
        llm_model=user_llm.model,
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
        "llm_model": user_llm.model,
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


# ============================== /api/chat/stream (SSE, JSON-action) ==============================

# 让 SSE 逐 token 直达浏览器,不被反代/CDN 缓冲成一坨。X-Accel-Buffering 是 nginx
# 唯一逐响应生效的开关;no-transform 阻止压缩(压缩会重新缓冲)。
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _diagnostic_hint(exc: Exception) -> str:
    """把常见失败映射成一句人话提示(指向最可能的原因,不是排障树)。"""
    name = type(exc).__name__
    msg = str(exc).lower()
    if "apikey" in msg or "api_key" in msg or "unauthorized" in msg or "authentication" in msg or name == "AuthenticationError":
        return "LLM 拒绝了凭据。请到「我的 API Keys」检查 key 是否正确。"
    if "timeout" in msg or name in ("TimeoutError", "ReadTimeout", "ConnectTimeout"):
        return "LLM 调用超时。供应商可能较慢 —— 重试,或换一个更快的模型。"
    if "rate" in msg and "limit" in msg:
        return "调用过于频繁被限流。等几秒再试。"
    if "connection" in msg or "getaddrinfo" in msg or "name or service" in msg:
        return "连不上 LLM 端点。检查网络与 base URL(国内直连模型/代理是否正常)。"
    if "json" in msg and ("decode" in msg or "parse" in msg):
        return "模型返回了非法 JSON。再发一次通常会自我纠正。"
    return ""


def _register_excel_cb(task_id: str):
    """给 StreamAgent 的 on_excel 回调:把产出的 Excel 登记进 DB,返回前端可用 payload。"""
    def cb(task: TaskResult) -> dict[str, Any]:
        if task.excel:
            STATE.db.add_task_file(
                task_id, filename=task.excel.path.name, path=str(task.excel.path),
                size_bytes=task.excel.size_bytes, preview=task.rows_preview[:50],
            )
        return {
            "task_id": task_id,
            "status": task.status,
            "row_count": task.row_count,
            "rows_preview": task.rows_preview[:50],
            "excel": ({
                "filename": task.excel.path.name,
                "size_bytes": task.excel.size_bytes,
                "download_url": f"/api/tasks/{task_id}/file",
            } if task.excel else None),
        }
    return cb


@app.post("/api/chat/stream")
async def chat_stream_endpoint(
    req: ChatRequest,
    request: Request,
    identity: Identity = Depends(current_identity),
):
    """LLM 驱动的自由对话 —— SSE 流式 (JSON-action 协议, 对标 DataAgent)。

    与 /api/chat 同源:配额 / 建-续 task / 落消息 / 个人 key / 报告清单快捷都复用,
    只是把"跑完一次性返回"换成"逐事件 yield"。前端用 fetch+getReader 解析。
    """
    from fastapi.responses import StreamingResponse

    t0 = time.monotonic()
    from datetime import UTC, datetime as _dt
    month = _dt.now(UTC).strftime("%Y-%m")
    limit = STATE.db.get_user_quota_limit(identity.username)
    if limit is not None:
        used = STATE.db.get_user_quota_usage(identity.username, month)
        if used["input_tokens"] + used["output_tokens"] >= limit:
            raise HTTPException(429, f"本月 LLM token 配额已用完 ({limit})。请联系管理员调整。")

    # 多轮:带 task_id 且属于自己的 chat task → 续;否则新建。
    task_id: str | None = None
    if req.task_id:
        row = STATE.db.get_task(req.task_id)
        if row and row.get("username") == identity.username and row.get("source") == "chat":
            task_id = req.task_id
    if not task_id:
        task_id = STATE.db.create_task(
            username=identity.username, source="chat", question=req.message,
        )

    STATE.db.add_task_message(task_id, role="user", text=req.message)
    TASK_BUS.publish(task_id, {"type": "user_message", "text": req.message})

    is_report = _is_report_list_query(req.message)

    # 当前用户的有效 LLM 配置(报告清单快捷不需要 LLM)。
    cfg = _resolve_user_llm(identity)
    llm_model = cfg["model"]

    history_text: list[tuple[str, str]] = []
    prior_msgs = STATE.db.list_task_messages(task_id)
    for m in prior_msgs[:-1]:                       # 最后一条是刚加的 user message
        if m.get("text"):
            history_text.append((m["role"], m["text"]))

    ip = request.client.host if request.client else None

    async def event_stream():
        events: list[dict[str, Any]] = []
        final_text = ""
        final_error = False
        assistant_persisted = False

        def sse(ev: dict[str, Any]) -> str:
            return "data: " + json.dumps(ev, ensure_ascii=False, default=str) + "\n\n"

        # 探活:先 flush 一个 SSE 注释,逼反代立即打开到客户端的管道。
        yield ": sap-stream-open\n\n"
        # 首个事件携带 task_id,让前端立刻知道这轮归属哪个 task(多轮续聊用)。
        meta_ev = {"kind": "meta", "payload": {"task_id": task_id}}
        events.append(meta_ev)
        yield sse(meta_ev)

        try:
            # ── 报告清单快捷:不走 LLM,包成事件流(快捷函数自己已落 task+消息) ──
            if is_report:
                ev = {"kind": "progress", "payload": {
                    "phase": "tool_start", "action": "report_list",
                    "msg": "读取固定报告清单 OData…",
                }}
                events.append(ev)
                yield sse(ev)
                result = await asyncio.to_thread(
                    _run_report_list_shortcut,
                    task_id=task_id, req=req, request=request, identity=identity, t0=t0,
                )
                assistant_persisted = True                  # 快捷函数已落 assistant 消息
                tb = result.get("task")
                if tb and tb.get("excel"):
                    tev = {"kind": "task", "payload": {**tb, "task_id": task_id}}
                    events.append(tev)
                    yield sse(tev)
                final_text = result.get("answer", "")
                fev = {"kind": "final", "payload": {"text": final_text}}
                events.append(fev)
                yield sse(fev)
                return

            # ── 未配置:友好失败 ──
            if not cfg["ready"]:
                final_text = (
                    f"模型 {llm_model or '(未设置)'} 未就绪 —— 请到「🔑 LLM 设置」"
                    "填好 API key + Base URL + Model(或在 .env 设默认)。"
                )
                final_error = True
                fev = {"kind": "final", "payload": {
                    "text": final_text, "error": True, "error_category": "not_configured",
                }}
                events.append(fev)
                yield sse(fev)
                return

            # ── 正常 LLM 链路 ──
            user_llm = _build_user_llm(identity)

            agent = StreamAgent(
                settings=STATE.settings, llm=user_llm, bw=STATE.bw,
                skills=STATE.skills, orchestrator=STATE.orchestrator,
                username=identity.username, role=identity.role,
                on_excel=_register_excel_cb(task_id),
                sensitive_resolver=STATE.orchestrator._resolve_sensitive,  # 样本喂 LLM 前脱敏(P1-3)
            )
            try:
                async for step in agent.run_turn(req.message, history=history_text):
                    ev = {"kind": step.kind, "payload": step.payload}
                    events.append(ev)
                    yield sse(ev)
                    TASK_BUS.publish(task_id, {"type": step.kind, **step.payload})
                    if step.kind == "final":
                        final_text = step.payload.get("text", "")
                        final_error = bool(step.payload.get("error"))
            except asyncio.CancelledError:
                final_text = "(客户端在智能体完成前断开了连接)"
                events.append({"kind": "final", "payload": {"text": final_text, "cancelled": True}})
                raise
            except Exception as e:                          # noqa: BLE001
                hint = _diagnostic_hint(e)
                final_text = f"**智能体出错:** {type(e).__name__}: {e}"
                if hint:
                    final_text += f"\n\n💡 {hint}"
                final_error = True
                log.exception("agent stream crash task=%s msg=%r", task_id, req.message[:80])
                fev = {"kind": "final", "payload": {
                    "text": final_text, "error": True, "exc_type": type(e).__name__,
                }}
                events.append(fev)
                yield sse(fev)

            # 落 assistant 消息 + 收尾 task + 配额 + 审计。
            latency_ms = int((time.monotonic() - t0) * 1000)
            last_task = agent.last_task
            STATE.db.finish_task(
                task_id,
                status=("failed" if final_error else "done"),
                error=(final_text if final_error else None),
                row_count=(last_task.row_count if last_task else 0),
                latency_ms=latency_ms,
                llm_model=llm_model,
                llm_input_tokens=agent.total_input_tokens,
                llm_output_tokens=agent.total_output_tokens,
            )
            STATE.db.add_task_message(
                task_id, role="assistant", text=final_text,
                blocks={
                    "events": events,
                    "task": ({
                        "status": last_task.status,
                        "excel_filename": last_task.excel.path.name if last_task.excel else None,
                        "row_count": last_task.row_count,
                    } if last_task else None),
                },
            )
            assistant_persisted = True
            STATE.db.add_user_quota_usage(
                identity.username, month,
                input_tokens=agent.total_input_tokens,
                output_tokens=agent.total_output_tokens,
            )
            STATE.db.write_audit(
                username=identity.username, action="chat",
                task_id=task_id, question=req.message,
                row_count=(last_task.row_count if last_task else 0),
                latency_ms=latency_ms, llm_model=llm_model,
                llm_tokens=agent.total_input_tokens + agent.total_output_tokens,
                ip=ip,
            )
        finally:
            # 兜底:即便中途断开,也尽量留下一条 assistant 记录(否则历史里这轮丢失)。
            if not assistant_persisted:
                try:
                    STATE.db.add_task_message(
                        task_id, role="assistant",
                        text=final_text or "(无输出)",
                        blocks={"events": events, "cancelled": True},
                    )
                except Exception:                           # noqa: BLE001
                    log.exception("流式收尾落 assistant 消息失败 task=%s", task_id)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)


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


# ============================== /api/tasks: rerun / delete / messages / stream ==============================


class RerunRequest(BaseModel):
    params: dict[str, Any] | None = None       # 可空 → 沿用原参数


@app.post("/api/tasks/{task_id}/rerun")
def rerun_task_endpoint(
    task_id: str,
    req: RerunRequest,
    request: Request,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    """重新跑一遍历史 Skill 任务,可选改参数 (F5)。"""
    row = STATE.db.get_task(task_id)
    if not row or row.get("username") != identity.username:
        raise HTTPException(404, "任务不存在")
    if not row.get("skill_id"):
        raise HTTPException(400, "只支持基于 Skill 的任务重跑")
    # 合并原参 + 覆盖
    try:
        original_params = json.loads(row.get("params") or "{}")
    except Exception:
        original_params = {}
    final_params = {**original_params, **(req.params or {})}
    return _run_skill_task(
        skill_id=row["skill_id"],
        params=final_params,
        username=identity.username,
        source="rerun",
        parent_task_id=task_id,
        ip=request.client.host if request.client else None,
    )


@app.delete("/api/tasks/{task_id}")
def delete_task_endpoint(
    task_id: str,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    """删除自己的任务 (含 messages / files cascade)。"""
    ok = STATE.db.delete_task(task_id, identity.username)
    if not ok:
        raise HTTPException(404, "任务不存在")
    return {"ok": True}


@app.get("/api/tasks/{task_id}/messages")
def list_task_messages_endpoint(
    task_id: str,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    """加载某个 chat 任务的多轮历史 (P1-10)。"""
    row = STATE.db.get_task(task_id)
    if not row or row.get("username") != identity.username:
        raise HTTPException(404, "任务不存在")
    msgs = STATE.db.list_task_messages(task_id)
    return {"task_id": task_id, "messages": msgs}


@app.get("/api/tasks/{task_id}/stream")
def stream_task_endpoint(
    task_id: str,
    identity: Identity = Depends(current_identity),
):
    """SSE 进度流 (P1-13)。"""
    from fastapi.responses import StreamingResponse
    row = STATE.db.get_task(task_id)
    if not row or row.get("username") != identity.username:
        raise HTTPException(404, "任务不存在")

    def _gen():
        # 终态任务: 一次性把 DB 状态发完即关
        if row.get("status") in ("done", "failed"):
            payload = {
                "type": row["status"],
                "row_count": row.get("row_count") or 0,
                "error": row.get("error"),
                "excel_filename": row.get("filename"),
            }
            yield f"event: {payload['type']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            return
        # 跑中: 走 TaskBus
        for ev in TASK_BUS.stream(task_id, timeout_seconds=120, idle_tick=10):
            etype = ev.get("type", "message")
            yield f"event: {etype}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


# ============================== /api/favorites (P1-9) ==============================


class FavoriteRequest(BaseModel):
    kind: str                                # skill | task
    ref_id: str


@app.get("/api/favorites")
def list_favorites_endpoint(
    kind: str | None = None,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    rows = STATE.db.list_favorites(identity.username, kind=kind)
    return {"favorites": rows, "total": len(rows)}


@app.post("/api/favorites")
def add_favorite_endpoint(
    req: FavoriteRequest,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    if req.kind not in ("skill", "task"):
        raise HTTPException(400, "kind 必须是 skill 或 task")
    STATE.db.add_favorite(identity.username, req.kind, req.ref_id)
    return {"ok": True}


@app.delete("/api/favorites/{kind}/{ref_id}")
def remove_favorite_endpoint(
    kind: str, ref_id: str,
    identity: Identity = Depends(current_identity),
) -> dict[str, Any]:
    STATE.db.remove_favorite(identity.username, kind, ref_id)
    return {"ok": True}


# ============================== /api/admin/skills CRUD (P1-7) ==============================


class SkillCreateRequest(BaseModel):
    id: str
    skill_md: str
    service_yaml: str


class SkillUpdateRequest(BaseModel):
    skill_md: str | None = None
    service_yaml: str | None = None


class SkillTestRunRequest(BaseModel):
    params: dict[str, Any]


_SAFE_SKILL_ID = re.compile(r"^[a-z][a-z0-9_]{1,40}$")


def _skill_folder(skill_id: str) -> Path:
    if not _SAFE_SKILL_ID.match(skill_id):
        raise HTTPException(400, "skill id 必须 ^[a-z][a-z0-9_]{1,40}$")
    return STATE.settings.skills_dir / skill_id


@app.post("/api/admin/skills")
def admin_create_skill(
    req: SkillCreateRequest,
    _: Identity = Depends(require_admin),
) -> dict[str, Any]:
    folder = _skill_folder(req.id)
    if folder.exists():
        raise HTTPException(409, f"Skill {req.id} 已存在")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(req.skill_md, encoding="utf-8")
    (folder / "service.yaml").write_text(req.service_yaml, encoding="utf-8")
    STATE.skills.reload()
    return {"ok": True, "id": req.id}


@app.put("/api/admin/skills/{skill_id}")
def admin_update_skill(
    skill_id: str,
    req: SkillUpdateRequest,
    _: Identity = Depends(require_admin),
) -> dict[str, Any]:
    folder = _skill_folder(skill_id)
    if not folder.exists():
        raise HTTPException(404, "Skill 不存在")
    if req.skill_md is not None:
        (folder / "SKILL.md").write_text(req.skill_md, encoding="utf-8")
    if req.service_yaml is not None:
        (folder / "service.yaml").write_text(req.service_yaml, encoding="utf-8")
    STATE.skills.reload()
    return {"ok": True}


@app.delete("/api/admin/skills/{skill_id}")
def admin_delete_skill(
    skill_id: str,
    _: Identity = Depends(require_admin),
) -> dict[str, Any]:
    folder = _skill_folder(skill_id)
    if not folder.exists():
        raise HTTPException(404, "Skill 不存在")
    import shutil
    shutil.rmtree(folder)
    STATE.skills.reload()
    return {"ok": True}


@app.get("/api/admin/skills/{skill_id}/source")
def admin_get_skill_source(
    skill_id: str,
    _: Identity = Depends(require_admin),
) -> dict[str, Any]:
    """读 SKILL.md + service.yaml 原文 (供编辑器初始化)。"""
    folder = _skill_folder(skill_id)
    if not folder.exists():
        raise HTTPException(404, "Skill 不存在")
    skill_md = (folder / "SKILL.md").read_text(encoding="utf-8") if (folder / "SKILL.md").exists() else ""
    service_yaml = (folder / "service.yaml").read_text(encoding="utf-8") if (folder / "service.yaml").exists() else ""
    has_template = (folder / "template.xlsx").exists()
    has_chart = (folder / "chart.json").exists()
    return {
        "id": skill_id, "skill_md": skill_md, "service_yaml": service_yaml,
        "has_template": has_template, "has_chart": has_chart,
    }


@app.post("/api/admin/skills/{skill_id}/files/template")
async def admin_upload_template(
    skill_id: str,
    file: UploadFile = File(...),
    _: Identity = Depends(require_admin),
) -> dict[str, Any]:
    """上传 template.xlsx;先做安全扫描 (拒绝 VBA) (P2-16)。"""
    folder = _skill_folder(skill_id)
    if not folder.exists():
        raise HTTPException(404, "Skill 不存在")
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "只接受 .xlsx (.xlsm 含宏将被拒绝)")
    target = folder / "template.xlsx"
    content = await file.read()
    target.write_bytes(content)
    try:
        from app.excel.builder import scan_template_safety, TemplateScanError
        scan_template_safety(target)
    except TemplateScanError as e:
        target.unlink(missing_ok=True)
        raise HTTPException(400, str(e))
    return {"ok": True, "size_bytes": len(content)}


@app.delete("/api/admin/skills/{skill_id}/files/template")
def admin_delete_template(
    skill_id: str,
    _: Identity = Depends(require_admin),
) -> dict[str, Any]:
    folder = _skill_folder(skill_id)
    p = folder / "template.xlsx"
    if p.exists():
        p.unlink()
    return {"ok": True}


class ChartJsonRequest(BaseModel):
    chart: dict[str, Any]


@app.put("/api/admin/skills/{skill_id}/chart")
def admin_set_chart(
    skill_id: str,
    req: ChartJsonRequest,
    _: Identity = Depends(require_admin),
) -> dict[str, Any]:
    folder = _skill_folder(skill_id)
    if not folder.exists():
        raise HTTPException(404, "Skill 不存在")
    (folder / "chart.json").write_text(
        json.dumps(req.chart, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return {"ok": True}


@app.delete("/api/admin/skills/{skill_id}/chart")
def admin_delete_chart(
    skill_id: str,
    _: Identity = Depends(require_admin),
) -> dict[str, Any]:
    p = _skill_folder(skill_id) / "chart.json"
    if p.exists():
        p.unlink()
    return {"ok": True}


@app.post("/api/admin/skills/{skill_id}/test-run")
def admin_test_run_skill(
    skill_id: str,
    req: SkillTestRunRequest,
    request: Request,
    identity: Identity = Depends(require_admin),
) -> dict[str, Any]:
    """管理员试运行,不写审计 (不污染统计),但走完整生成流程。"""
    if skill_id not in {s.id for s in STATE.skills.list()}:
        STATE.skills.reload()
    if skill_id not in {s.id for s in STATE.skills.list()}:
        raise HTTPException(404, f"未找到 Skill: {skill_id}")
    result = STATE.orchestrator.run_skill(
        skill_id, req.params,
        username=f"_test_{identity.username}",
        question=f"[test-run] {skill_id}",
    )
    return {
        "status": result.status,
        "error": result.error,
        "row_count": result.row_count,
        "rows_preview": result.rows_preview[:20],
        "warnings": result.excel.warnings if result.excel else [],
        "meta": result.meta,
    }


class SkillStatusRequest(BaseModel):
    status: str                                # active | deprecated | archived | draft


@app.patch("/api/admin/skills/{skill_id}/status")
def admin_set_skill_status(
    skill_id: str,
    req: SkillStatusRequest,
    identity: Identity = Depends(require_admin),
) -> dict[str, Any]:
    """切换 Skill 生命周期状态 (§9.5, P2-19)。"""
    if req.status not in ("draft", "active", "deprecated", "archived"):
        raise HTTPException(400, "status 必须是 draft / active / deprecated / archived")
    try:
        s = STATE.skills.get(skill_id)
    except KeyError:
        raise HTTPException(404, "Skill 不存在")
    STATE.db.set_skill_status(s.id, s.version, req.status, changed_by=identity.username)
    return {"ok": True, "id": s.id, "version": s.version, "status": req.status}


# ============================== /api/admin/quota (P2-20) ==============================


class QuotaLimitRequest(BaseModel):
    monthly_tokens: int | None                 # None = 取消限制


@app.get("/api/admin/quota")
def admin_list_quota(_: Identity = Depends(require_admin)) -> dict[str, Any]:
    rows = STATE.db.list_quota_status()
    return {"quota": rows, "total": len(rows)}


@app.put("/api/admin/quota/{username}")
def admin_set_quota(
    username: str,
    req: QuotaLimitRequest,
    identity: Identity = Depends(require_admin),
) -> dict[str, Any]:
    if req.monthly_tokens is not None and req.monthly_tokens < 0:
        raise HTTPException(400, "monthly_tokens 不可为负")
    STATE.db.set_user_quota_limit(username, req.monthly_tokens, set_by=identity.username)
    return {"ok": True}


@app.get("/api/quota/me")
def my_quota_endpoint(identity: Identity = Depends(current_identity)) -> dict[str, Any]:
    from datetime import UTC, datetime as _dt
    month = _dt.now(UTC).strftime("%Y-%m")
    usage = STATE.db.get_user_quota_usage(identity.username, month)
    limit = STATE.db.get_user_quota_limit(identity.username)
    return {
        "month": month,
        "usage": usage,
        "limit_tokens": limit,
        "remaining": (max(0, limit - usage["input_tokens"] - usage["output_tokens"])
                      if limit is not None else None),
    }


# ============================== 静态前端 ==============================

_web_dist = Path(__file__).resolve().parent.parent / "web" / "dist"
_web_root = Path(__file__).resolve().parent.parent / "web"
_web_dist_index = _web_dist / "index.html"
_web_index = _web_dist_index if _web_dist_index.exists() else _web_root / "index.html"

if (_web_dist / "assets").exists():
    # Vite 构建产物
    app.mount("/assets", StaticFiles(directory=_web_dist / "assets"), name="assets")


@app.get("/")
def _index():
    if _web_index.exists():
        return FileResponse(_web_index)
    return {
        "message": "前端尚未构建。请运行 `cd web && npm install && npm run build`",
        "api_status": "/api/status",
    }


@app.get("/{full_path:path}")
def _spa_fallback(full_path: str) -> FileResponse:
    # SPA 路由 fallback —— 任何非 /api 路径都返回 index.html
    if full_path.startswith("api/"):
        raise HTTPException(404, "API 不存在")
    target = _web_dist / full_path
    if target.exists() and target.is_file():
        return FileResponse(target)
    if _web_index.exists():
        return FileResponse(_web_index)
    raise HTTPException(404, "前端未就绪")
