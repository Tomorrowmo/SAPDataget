"""LLM 智能体 —— tool-use 编排循环 (§9.4)。

可用工具:
  list_skills(keywords?)      — 优先：找匹配的模板
  load_skill(skill_id)        — 读模板详情
  run_skill(skill_id, params) — 用模板出 Excel
  list_bw_services(search?)   — 自由模式：列服务
  get_service_metadata(svc)   — 自由模式：读元数据
  execute_odata_query(...)    — 自由模式：跑查询（结果中样本 5 行送回 LLM）
  build_excel(service, entity_set, filter, select, orderby, top)
                              — 自由模式收尾：触发 BW 重查 + 出 Excel
  ask_user(question, options?)
                              — 参数不全时追问（前端弹按钮；CLI 模式直接 input）

设计要点:
  * tool 入参 schema 严格，防止 LLM 漏字段
  * 工具调用全程记录到 TaskTrace,供审计/调试
  * 主循环最多 max_iters 轮，防失控
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from app.bw.interface import BWClient
from app.config import Settings
from app.llm import LLMClient, assistant_message_from, tool_message
from app.orchestrator import TaskOrchestrator, TaskResult
from app.skills.registry import SkillRegistry
from app.skills.schema import SkillNotFound

log = logging.getLogger(__name__)


# ============================== Tool Schemas (OpenAI 格式) ==============================

def _tool(name: str, description: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": params,
        },
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "list_skills",
        "列出可用的 BW 取数模板 (Skills)。返回 [{id,title,description,keywords,params}]。"
        "查询前**必须**先调一次此工具看是否有现成模板。",
        {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选关键词,模糊匹配标题/描述/keywords",
                },
            },
        },
    ),
    _tool(
        "load_skill",
        "加载某个 Skill 的完整定义,看它的提示、参数、字段列表。命中模板后调用。",
        {
            "type": "object",
            "properties": {"skill_id": {"type": "string"}},
            "required": ["skill_id"],
        },
    ),
    _tool(
        "run_skill",
        "用模板取数并出 Excel。如果你已经收齐了 Skill 的所有必填参数,就调它。",
        {
            "type": "object",
            "properties": {
                "skill_id": {"type": "string"},
                "params":   {"type": "object", "description": "Skill 要求的参数 (键=参数名)"},
            },
            "required": ["skill_id", "params"],
        },
    ),
    _tool(
        "ask_user",
        "向用户追问缺失参数 (例如月份)。仅在没有合理默认时使用。",
        {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options":  {"type": "array", "items": {"type": "string"}, "description": "可选项"},
            },
            "required": ["question"],
        },
    ),
    _tool(
        "list_bw_services",
        "列出 BW 上所有 OData 服务 (没匹配模板时使用)。",
        {
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "可选关键词"},
            },
        },
    ),
    _tool(
        "get_service_metadata",
        "读取某个 BW 服务的元数据 (EntitySet + 字段列表)。",
        {
            "type": "object",
            "properties": {"service": {"type": "string"}},
            "required": ["service"],
        },
    ),
    _tool(
        "execute_odata_query",
        "执行 OData V2 查询,返回前 5 行样本 + 总行数 (完整数据后续由 build_excel 输出)。",
        {
            "type": "object",
            "properties": {
                "service":    {"type": "string"},
                "entity_set": {"type": "string"},
                "filter":     {"type": "string", "description": "$filter,如 \"Region eq 'HD'\""},
                "select":     {"type": "string", "description": "$select,逗号分隔"},
                "orderby":    {"type": "string"},
                "top":        {"type": "integer", "default": 100},
                "apply":      {"type": "string", "description": "$apply,如 groupby/aggregate"},
            },
            "required": ["service", "entity_set"],
        },
    ),
    _tool(
        "build_excel",
        "自由模式收尾：触发 BW 重查相同参数并把全量结果出 Excel 给用户下载。",
        {
            "type": "object",
            "properties": {
                "service":     {"type": "string"},
                "entity_set":  {"type": "string"},
                "filter":      {"type": "string"},
                "select":      {"type": "string"},
                "orderby":     {"type": "string"},
                "top":         {"type": "integer", "default": 1000},
                "apply":       {"type": "string"},
                "sheet_title": {"type": "string", "description": "Excel 主 sheet 名称"},
            },
            "required": ["service", "entity_set"],
        },
    ),
]


SYSTEM_PROMPT = """你是 SAP BW 智能取数助手。用户用自然语言（中文为主）提出取数需求，
你的目标是产出**一个可直接下载的 Excel 文件**给用户。

工作流程（严格遵守）:
1. **先调 list_skills**：找匹配的模板。模板是预制好的查询逻辑,优先用模板。
2. 命中模板 → load_skill 看参数 → 缺参数就 ask_user 追问 → 收齐后 run_skill。
3. 没匹配模板 → list_bw_services → get_service_metadata → execute_odata_query 验证 → build_excel 出文件。
4. **永远不要在最终回复里贴大段数据**,Excel 已经给用户了,你只需要写一段 ≤80 字的中文摘要 +
   关键发现 (1-3 条)。
5. 用户说"上月" / "本月" / "去年同期" 等相对时间,自己换算成 YYYYMM 格式。
6. 自由模式拼 OData 时:
   - 字符串值用单引号 'X'
   - 多条件用 and / or
   - 默认 top=100,用户要"全部"或要聚合再调大
   - 字段名严格从 metadata 取,不要臆造

错误处理:
- 工具返回 error 时,读懂错误,改参数重试,或向用户解释。
- 同一个错误连续 2 次仍失败,就直接告诉用户具体问题。
"""


# ============================== Trace 与 Tool Dispatcher ==============================


@dataclass
class ToolCallTrace:
    name: str
    arguments: dict[str, Any]
    output: str
    is_error: bool


@dataclass
class AgentResult:
    final_text: str
    task: TaskResult | None = None
    traces: list[ToolCallTrace] = field(default_factory=list)
    iterations: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0


class Agent:
    """LLM tool-use 编排器。"""

    def __init__(
        self,
        settings: Settings,
        llm: LLMClient,
        bw: BWClient,
        skills: SkillRegistry,
        orchestrator: TaskOrchestrator,
        ask_user_callback: Callable[[str, list[str] | None], str] | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.settings = settings
        self.llm = llm
        self.bw = bw
        self.skills = skills
        self.orchestrator = orchestrator
        self.ask_user_callback = ask_user_callback
        self.on_event = on_event or (lambda kind, payload: None)

    # ---------- 主入口 ----------
    def run(self, user_message: str, *, username: str = "cli_user") -> AgentResult:
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ]
        traces: list[ToolCallTrace] = []
        result = AgentResult(final_text="", traces=traces)
        last_task: TaskResult | None = None

        for iteration in range(self.settings.llm.max_iters):
            self.on_event("iter_start", {"iteration": iteration + 1})
            try:
                resp = self.llm.complete(messages=messages, tools=TOOLS)
            except Exception as e:                          # noqa: BLE001
                result.final_text = f"LLM 调用失败: {e}"
                return result
            result.total_input_tokens += resp.input_tokens
            result.total_output_tokens += resp.output_tokens
            result.iterations = iteration + 1

            messages.append(assistant_message_from(resp))

            # 无 tool_calls → 终态
            if not resp.tool_calls:
                result.final_text = resp.text or "(无回复)"
                result.task = last_task
                return result

            # 处理工具调用
            for tc in resp.tool_calls:
                self.on_event("tool_call", {"name": tc.name, "arguments": tc.arguments})
                output_str, is_error, maybe_task = self._dispatch_tool(
                    tc.name, tc.arguments, username=username, user_message=user_message,
                )
                traces.append(ToolCallTrace(
                    name=tc.name, arguments=tc.arguments,
                    output=_truncate(output_str, 4000), is_error=is_error,
                ))
                messages.append(tool_message(tc.id, output_str))
                if maybe_task is not None:
                    last_task = maybe_task

        result.final_text = "(已达到最大 LLM 迭代次数,请缩小问题再试)"
        result.task = last_task
        return result

    # ---------- 工具分发 ----------
    def _dispatch_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        username: str,
        user_message: str,
    ) -> tuple[str, bool, TaskResult | None]:
        try:
            if name == "list_skills":
                skills = self.skills.list(keywords=args.get("keywords"))
                return _ok({"skills": [s.to_summary() for s in skills], "count": len(skills)})

            if name == "load_skill":
                skill = self.skills.get(args["skill_id"])
                return _ok(skill.to_detail())

            if name == "run_skill":
                task = self.orchestrator.run_skill(
                    args["skill_id"], args.get("params") or {},
                    username=username, question=user_message,
                )
                if task.status == "failed":
                    return _err(task.error or "run_skill 失败")
                return (
                    _ok({
                        "status": "done",
                        "row_count": task.row_count,
                        "excel": str(task.excel.path) if task.excel else None,
                        "preview_rows": task.rows_preview[:5],
                    })[0],
                    False,
                    task,
                )

            if name == "ask_user":
                question = args["question"]
                options = args.get("options")
                if self.ask_user_callback is None:
                    return _err("当前模式不支持追问,请用户在初始问题里直接给出所有参数")
                answer = self.ask_user_callback(question, options)
                return _ok({"answer": answer})

            if name == "list_bw_services":
                resp = self.bw.list_services(search=args.get("search"), top=50)
                return _from_response(resp)

            if name == "get_service_metadata":
                resp = self.bw.get_metadata(args["service"])
                return _from_response(resp)

            if name == "execute_odata_query":
                resp = self.bw.execute_query(
                    service=args["service"],
                    entity_set=args["entity_set"],
                    filter=args.get("filter"),
                    select=args.get("select"),
                    orderby=args.get("orderby"),
                    top=args.get("top", 100),
                    apply=args.get("apply"),
                    count=True,
                )
                if not resp.error and resp.json:
                    # 防爆: 只回 5 行给 LLM
                    rows = (resp.json.get("rows") or [])[:5]
                    sample = {
                        "row_count_total": resp.json.get("row_count_total"),
                        "row_count_returned": resp.json.get("row_count_returned"),
                        "sample_rows": rows,
                        "url": resp.url,
                    }
                    return _ok(sample)
                return _from_response(resp)

            if name == "build_excel":
                resp = self.bw.execute_query(
                    service=args["service"],
                    entity_set=args["entity_set"],
                    filter=args.get("filter"),
                    select=args.get("select"),
                    orderby=args.get("orderby"),
                    top=args.get("top", 1000),
                    apply=args.get("apply"),
                    count=True,
                )
                if resp.error or not resp.json:
                    return _from_response(resp)
                rows = resp.json.get("rows") or []
                if not rows:
                    return _err("查询无数据,无法生成 Excel")
                columns = (args.get("select").split(",") if args.get("select") else list(rows[0].keys()))
                columns = [c.strip() for c in columns]
                info = {
                    "username": username,
                    "question": user_message,
                    "service": args["service"],
                    "entity_set": args["entity_set"],
                    "odata_url": resp.url,
                    "row_count": resp.json.get("row_count_total") or len(rows),
                }
                task = self.orchestrator.run_free_query(
                    service=args["service"],
                    entity_set=args["entity_set"],
                    columns=columns,
                    rows=rows,
                    info=info,
                    sheet_title=args.get("sheet_title", "数据"),
                    username=username,
                )
                return (
                    _ok({
                        "status": "done",
                        "row_count": task.row_count,
                        "excel": str(task.excel.path) if task.excel else None,
                        "preview_rows": task.rows_preview[:5],
                    })[0],
                    False,
                    task,
                )

            return _err(f"未知工具: {name}")

        except SkillNotFound as e:
            return _err(f"Skill 不存在: {e}")
        except KeyError as e:
            return _err(f"参数缺失: {e}")
        except Exception as e:                              # noqa: BLE001
            log.exception("工具 %s 异常", name)
            return _err(f"工具执行异常: {e}")


# ============================== 响应辅助 ==============================


def _ok(data: Any) -> tuple[str, bool, None]:
    return json.dumps({"ok": True, "data": data}, ensure_ascii=False, default=_default), False, None


def _err(msg: str) -> tuple[str, bool, None]:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False), True, None


def _from_response(resp: Any) -> tuple[str, bool, None]:
    payload = {
        "ok": not bool(resp.error),
        "status_code": resp.status_code,
        "url": resp.url,
    }
    if resp.error:
        payload["error"] = resp.error
    if resp.json is not None:
        payload["data"] = resp.json
    elif resp.text:
        payload["text"] = resp.text[:1000]
    return json.dumps(payload, ensure_ascii=False, default=_default), bool(resp.error), None


def _default(o: Any) -> Any:
    if isinstance(o, (dt.datetime, dt.date)):
        return o.isoformat()
    return str(o)


def _truncate(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[: max_len - 30] + f"...(已截断,原长 {len(s)})"
