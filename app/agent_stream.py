"""流式 LLM 智能体 —— JSON-action ReAct 协议 (对标 DataAgent)。

与 app/agent.py 的原生 function-calling 不同,这里 LLM 每轮只吐**一个 JSON 对象**:

    { "thought": "<一句话推理>", "action": "<动作>", "args": { ... } }

好处:可以**边收 token 边用正则抠出半成品 thought / answer 推给前端**做打字机效果,
且模型无关(弱 function-calling 的模型也能用)。代价:失去原生参数 schema 校验 →
用健壮 JSON 解析 + 三层自愈 + 断路器补偿。

run_turn 是异步生成器,持续 yield AgentStep 事件,server 据此发 SSE:
  progress       — 心跳 (phase ∈ llm_call/llm_done/tool_start/tool_done)
  thought_delta  — 流式半成品思考 (打字机)
  answer_delta   — 流式半成品答案 (打字机)
  thought        — 本轮定稿的 thought
  tool_call      — 即将执行的动作 + 参数
  tool_result    — 动作执行结果 (前端工作台展示)
  task           — 产出了 Excel (前端渲染下载卡 + 预览表)
  final          — 终态答案

BW 客户端与 orchestrator 是同步阻塞的,在 async 生成器里一律用 asyncio.to_thread
包裹,避免堵死事件循环。
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from app.bw.interface import BWClient
from app.config import Settings
from app.llm import LLMClient
from app.orchestrator import TaskOrchestrator, TaskResult
from app.skills.registry import SkillRegistry
from app.skills.schema import SkillNotFound

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是 SAP BW 智能取数助手。用户用自然语言(中文为主)提取数需求,你的目标是\
产出**一个可直接下载的 Excel 文件** + 一段简短中文摘要。

你**永远**只回复一个 JSON 对象,前后不要有任何别的文字。结构:
{
  "thought": "<一句话推理,说明这一步要做什么>",
  "action": "answer | list_skills | load_skill | run_skill | ask_user | list_bw_services | get_service_metadata | execute_odata_query | build_excel",
  "args": { ... }
}

各动作的 args 契约:
- answer            : { "text": "<给用户的 Markdown 答复,≤120 字,含 1-3 条关键发现>" }
- list_skills       : { "keywords": ["可选","关键词"] }                      —— 查有没有现成模板
- load_skill        : { "skill_id": "<模板 id>" }                            —— 看模板的参数与字段
- run_skill         : { "skill_id": "<模板 id>", "params": { ... } }         —— 用模板取数并出 Excel
- ask_user          : { "question": "<追问>", "options": ["可选项"] }        —— 缺必填参数且无合理默认时
- list_bw_services  : { "search": "<可选关键词>" }                           —— 没匹配模板时列服务
- get_service_metadata : { "service": "<服务名>" }                           —— 读 EntitySet + 字段
- execute_odata_query  : { "service","entity_set","filter","select","orderby","top","apply" } —— 验证查询(回 5 行样本)
- build_excel       : { "service","entity_set","filter","select","orderby","top","apply","sheet_title" } —— 收尾出全量 Excel

工作流程(严格遵守):
1. **先 list_skills** 找匹配模板。模板是预制好的查询,优先用。
2. 命中模板 → load_skill 看参数 → 缺参数就 ask_user 或自己合理推断 → 收齐后 run_skill。
3. 没匹配模板 → list_bw_services → get_service_metadata → execute_odata_query 验证 → build_excel 出文件。
4. **永远不要在 answer 里贴大段数据**。Excel 已经给用户了,你只写 ≤120 字摘要 + 关键发现。
5. 用户说"上月/本月/去年同期"等相对时间,自己换算成 YYYYMM。今天的日期会在下文给你。
6. 拼 OData 时:字符串值用单引号 'X';多条件用 and/or;默认 top=100,用户要"全部"或要聚合再调大;
   字段名严格从 metadata 取,不要臆造。
7. **动作要果断**:用户给了明确取数需求时,直接走流程,不要先 answer 解释"我将要查…",那是浪费一轮。
   只有在真的缺关键信息、或需要向用户确认时才 ask_user / 用 answer 反问。
8. 拿到 run_skill / build_excel 的成功结果后,**务必**用一个 answer 动作给用户解读(摘要 + 发现),
   不要把用户晾在原始数字上。

自愈与语言:
- 工具返回 error 时读懂错误、改参数重试;同一个错误连续失败就向用户说清楚具体问题,不要空转。
- answer.text 用与用户**同一种语言**(用户中文就全中文),技术词(字段名/服务名/数字)保持原样。
- thought 只写一句话。answer 的篇幅按问题复杂度,不要硬塞空话,也不要自我介绍。
"""


# ── 流式事件单元 ──
@dataclass
class AgentStep:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


# ── 半成品解析:对"还没收完"的字符串做宽松正则,边流边显示;最终判定用严格 JSON ──
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_THOUGHT_PARTIAL_RE = re.compile(r'"thought"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL)
_ANSWER_PARTIAL_RE = re.compile(r'"text"\s*:\s*"((?:[^"\\]|\\.)*)', re.DOTALL)


def _unescape(s: str) -> str:
    return s.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"').replace("\\\\", "\\")


def _extract_partial_thought(raw: str) -> str:
    m = _THOUGHT_PARTIAL_RE.search(raw)
    return _unescape(m.group(1)) if m else ""


def _extract_partial_answer(raw: str) -> str:
    m = _ANSWER_PARTIAL_RE.search(raw)
    return _unescape(m.group(1)) if m else ""


def _safe_parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text)
        text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _JSON_RE.search(text)
        if m:
            return json.loads(m.group(0))
        raise


def _skills_brief(skills: SkillRegistry, role: str | None = None) -> str:
    rows = skills.list(role=role)
    if not rows:
        return "(当前没有任何模板,只能走自由模式:list_bw_services → … → build_excel)"
    out: list[str] = []
    for s in rows:
        summ = s.to_summary()
        params = ", ".join(
            (p["name"] + ("*" if p.get("required") else "")) for p in summ.get("params", [])
        )
        out.append(f"- `{summ['id']}` — {summ['title']}：{summ['description']}（参数: {params or '无'}）")
    return "\n".join(out)


# 喂回 LLM 的结果视图:execute_odata_query 已只回 5 行;metadata 可能很大,裁一下。
def _llm_result_view(result: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict):
        return result
    return result


class StreamAgent:
    """JSON-action 流式编排器。"""

    def __init__(
        self,
        settings: Settings,
        llm: LLMClient,
        bw: BWClient,
        skills: SkillRegistry,
        orchestrator: TaskOrchestrator,
        *,
        username: str = "cli_user",
        role: str | None = None,
        on_excel: Callable[[TaskResult], dict[str, Any]] | None = None,
    ) -> None:
        """on_excel: 产出 Excel 时的回调 (server 注入) —— 负责把文件登记进 DB 并返回
        前端可用的 task payload (含 download_url)。CLI 不传则用本地路径兜底。"""
        self.settings = settings
        self.llm = llm
        self.bw = bw
        self.skills = skills
        self.orchestrator = orchestrator
        self.username = username
        self.role = role
        self.on_excel = on_excel
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self.last_task: TaskResult | None = None

    async def run_turn(
        self,
        user_message: str,
        *,
        history: list[tuple[str, str]] | None = None,
    ) -> AsyncIterator[AgentStep]:
        today = dt.date.today().strftime("%Y-%m-%d")
        system_msg = (
            SYSTEM_PROMPT
            + f"\n\n## 今天的日期\n{today}（相对时间据此换算）"
            + "\n\n## 可用模板 (list_skills 的内容,skill_id 必须从这里选,不要臆造)\n"
            + _skills_brief(self.skills, role=self.role)
        )
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_msg}]
        for role, text in (history or []):
            if role in ("user", "assistant") and text:
                messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": user_message})

        model_label = getattr(self.llm, "model", None) or "LLM"
        reformat_attempted = False
        empty_answer_retried = False
        fail_counts: dict[str, int] = {}

        for step_no in range(self.settings.llm.max_iters):
            # 阶段1 ── 心跳:调用 LLM 前先发,UI 立刻显示"正在调用"。
            yield AgentStep("progress", {
                "phase": "llm_call", "step": step_no + 1, "model": model_label,
                "msg": f"调用 {model_label} (第 {step_no + 1} 步)",
            })
            t0 = time.monotonic()
            raw = ""
            last_thought = ""
            last_answer = ""
            try:
                # 阶段2 ── 流式收取:边收边抠半成品 thought / answer 做打字机。
                async for delta in self.llm.stream(messages, temperature=0.2, max_tokens=2000):
                    raw += delta
                    partial = _extract_partial_thought(raw)
                    if partial and partial != last_thought:
                        last_thought = partial
                        yield AgentStep("thought_delta", {"text": partial, "step": step_no + 1})
                    ans = _extract_partial_answer(raw)
                    if ans and ans != last_answer:
                        last_answer = ans
                        yield AgentStep("answer_delta", {"text": ans, "step": step_no + 1})
            except Exception as e:                                 # noqa: BLE001
                dt_s = time.monotonic() - t0
                yield AgentStep("progress", {
                    "phase": "llm_done", "step": step_no + 1, "model": model_label,
                    "ok": False, "duration_s": round(dt_s, 2), "error": f"{type(e).__name__}: {e}",
                    "msg": f"{model_label} 调用失败 ({dt_s:.1f}s): {type(e).__name__}",
                })
                yield AgentStep("final", {
                    "text": f"**LLM 调用失败:** {type(e).__name__}: {e}\n\n"
                            f"请检查模型配置(API key / base URL),或在右上角换一个已配置的模型。",
                    "error": True,
                })
                return
            self.total_input_tokens += getattr(self.llm, "last_input_tokens", 0) or 0
            self.total_output_tokens += getattr(self.llm, "last_output_tokens", 0) or 0

            dt_s = time.monotonic() - t0
            yield AgentStep("progress", {
                "phase": "llm_done", "step": step_no + 1, "model": model_label,
                "ok": True, "duration_s": round(dt_s, 2), "chars": len(raw or ""),
                "msg": f"{model_label} 返回 {len(raw or '')} 字符,用时 {dt_s:.1f}s",
            })

            # 阶段3 ── 解析 JSON。失败进入自愈第①②层。
            try:
                parsed = _safe_parse_json(raw)
            except Exception:                                      # noqa: BLE001
                # 纯散文(无 JSON、无动作意图)→ 直接当答案,省一轮往返。
                if "{" not in raw and not re.search(
                    r"\b(list_skills|load_skill|run_skill|list_bw_services|get_service_metadata|execute_odata_query|build_excel)\b",
                    raw,
                ):
                    yield AgentStep("final", {"text": raw.strip()})
                    return
                # 半结构化坏 JSON → 催一次重发,还坏就把散文原样给用户。
                if not reformat_attempted:
                    reformat_attempted = True
                    yield AgentStep("progress", {
                        "phase": "tool_done", "step": step_no + 1, "ok": True,
                        "msg": "回复不是合法 JSON —— 让模型重发",
                    })
                    messages.append({"role": "assistant", "content": raw})
                    messages.append({"role": "user", "content": (
                        "你上一条回复不是要求的单个 JSON 对象。请用相同内容(相同语言)重发为:"
                        '{"thought":"...","action":"answer","args":{"text":"<你的答复>"}} '
                        "—— 只输出该 JSON,别的都不要。"
                    )})
                    continue
                yield AgentStep("final", {"text": raw.strip()})
                return

            action = parsed.get("action", "answer")
            thought = parsed.get("thought", "")
            if thought:
                yield AgentStep("thought", {"text": thought, "step": step_no + 1})

            # 阶段4 ── 终止:answer 收尾。空答案进入自愈第③层。
            if action == "answer":
                answer_text = (parsed.get("args", {}) or {}).get("text", "") or ""
                if not answer_text.strip():
                    if not empty_answer_retried:
                        empty_answer_retried = True
                        yield AgentStep("progress", {
                            "phase": "tool_done", "step": step_no + 1, "ok": True,
                            "msg": "模型返回了空答案 —— 让它重新作答",
                        })
                        messages.append({"role": "assistant", "content": raw})
                        messages.append({"role": "user", "content": (
                            "你上一条回复的 answer.text 是空的。请用与用户相同的语言给出有实际内容的"
                            '回答(JSON: {"thought":"...","action":"answer","args":{"text":"..."}});'
                            "若需求不清楚就在 text 里用一句话反问。只输出该 JSON。"
                        )})
                        continue
                    yield AgentStep("final", {
                        "text": "我没太理解你的需求 —— 能再说具体点吗?比如要哪个区/哪个月、哪些字段。",
                    })
                    return
                yield AgentStep("final", {"text": answer_text})
                return

            # 阶段5 ── 执行动作(同步 BW/orchestrator 放线程池,别堵事件循环)。
            args = parsed.get("args", {}) or {}
            yield AgentStep("tool_call", {"action": action, "args": args})
            yield AgentStep("progress", {
                "phase": "tool_start", "step": step_no + 1, "action": action,
                "msg": f"执行 {action}…",
            })
            t1 = time.monotonic()
            try:
                tool_result, task = await asyncio.to_thread(
                    self._dispatch, action, args, user_message,
                )
            except Exception as e:                                 # noqa: BLE001
                tdt = time.monotonic() - t1
                yield AgentStep("progress", {
                    "phase": "tool_done", "step": step_no + 1, "action": action,
                    "ok": False, "duration_s": round(tdt, 2), "error": f"{type(e).__name__}: {e}",
                    "msg": f"{action} 异常({tdt:.1f}s)",
                })
                tool_result, task = {"status": "error", "error": f"{type(e).__name__}: {e}"}, None
            else:
                tdt = time.monotonic() - t1
                yield AgentStep("progress", {
                    "phase": "tool_done", "step": step_no + 1, "action": action,
                    "ok": tool_result.get("status") != "error", "duration_s": round(tdt, 2),
                    "msg": f"{action} 完成,用时 {tdt:.1f}s",
                })
            yield AgentStep("tool_result", {"action": action, "result": tool_result})

            # 产出了 Excel → 登记 + 发 task 事件给前端渲染下载卡。
            if task is not None and task.excel:
                self.last_task = task
                if self.on_excel is not None:
                    task_payload = await asyncio.to_thread(self.on_excel, task)
                else:
                    task_payload = {
                        "status": task.status,
                        "row_count": task.row_count,
                        "rows_preview": task.rows_preview[:50],
                        "excel": {
                            "filename": task.excel.path.name,
                            "size_bytes": task.excel.size_bytes,
                            "download_url": str(task.excel.path),
                        },
                    }
                yield AgentStep("task", task_payload)

            # 断路器:同一 (action, key) 连错 3 次直接停,第 2 次给警告。
            nudge = ""
            if isinstance(tool_result, dict) and tool_result.get("status") == "error":
                key = args.get("skill_id") or args.get("service") or ""
                fkey = f"{action}:{key}"
                fail_counts[fkey] = fail_counts.get(fkey, 0) + 1
                if fail_counts[fkey] >= 3:
                    yield AgentStep("final", {
                        "text": (
                            f"「{action}」连续失败了 {fail_counts[fkey]} 次"
                            f"({str(tool_result.get('error', ''))[:120]}),我先停下来避免空转。"
                            "可能参数对不上或该数据不存在 —— 换个问法或告诉我更具体的目标再试。"
                        ),
                        "error": True,
                    })
                    return
                if fail_counts[fkey] >= 2:
                    nudge = (
                        f"\n\n⚠ `{action}` 已用同样方式失败 {fail_counts[fkey]} 次,不要再用相同参数重试 —— "
                        "换个工具、大改参数,或直接基于已有信息作答。"
                    )

            # 阶段6 ── 回灌结果(裁剪后)继续下一步。
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": (
                "工具结果:\n" + json.dumps(_llm_result_view(tool_result), ensure_ascii=False, default=str)[:8000]
                + nudge + "\n\n现在给出下一个 JSON 动作。"
            )})

        # 跑满 max_iters 仍没 answer → 兜底。
        yield AgentStep("final", {
            "text": "我执行了多步但还没给出最终结论(达到步数上限)。右侧工作台能看到卡在哪一步。"
                    "可以换个问法,或确认所需数据/参数后重试。",
            "error": True,
        })

    # ---------- 同步工具分发(在 to_thread 里跑) ----------
    def _dispatch(
        self, action: str, args: dict[str, Any], user_message: str,
    ) -> tuple[dict[str, Any], TaskResult | None]:
        try:
            if action == "list_skills":
                rows = self.skills.list(keywords=args.get("keywords"), role=self.role)
                return {"status": "ok", "skills": [s.to_summary() for s in rows], "count": len(rows)}, None

            if action == "load_skill":
                skill = self.skills.get(args["skill_id"])
                return {"status": "ok", **skill.to_detail()}, None

            if action == "run_skill":
                task = self.orchestrator.run_skill(
                    args["skill_id"], args.get("params") or {},
                    username=self.username, question=user_message,
                )
                if task.status == "failed":
                    return {"status": "error", "error": task.error or "run_skill 失败"}, None
                return {
                    "status": "done", "row_count": task.row_count,
                    "excel_filename": task.excel.path.name if task.excel else None,
                    "preview_rows": task.rows_preview[:5],
                }, task

            if action == "ask_user":
                # 流式模式本期不支持中断式追问,降级为提示 LLM 用 answer 反问。
                return {
                    "status": "error",
                    "error": "当前模式不支持弹窗追问。请改用 answer 动作,在 text 里直接向用户提出这个问题。",
                }, None

            if action == "list_bw_services":
                resp = self.bw.list_services(search=args.get("search"), top=50)
                return _from_response(resp), None

            if action == "get_service_metadata":
                resp = self.bw.get_metadata(args["service"])
                return _from_response(resp), None

            if action == "execute_odata_query":
                resp = self.bw.execute_query(
                    service=args["service"], entity_set=args["entity_set"],
                    filter=args.get("filter"), select=args.get("select"),
                    orderby=args.get("orderby"), top=args.get("top", 100),
                    apply=args.get("apply"), count=True,
                )
                if not resp.error and resp.json:
                    rows = (resp.json.get("rows") or [])[:5]
                    return {
                        "status": "ok",
                        "row_count_total": resp.json.get("row_count_total"),
                        "row_count_returned": resp.json.get("row_count_returned"),
                        "sample_rows": rows,
                        "url": resp.url,
                    }, None
                return _from_response(resp), None

            if action == "build_excel":
                resp = self.bw.execute_query(
                    service=args["service"], entity_set=args["entity_set"],
                    filter=args.get("filter"), select=args.get("select"),
                    orderby=args.get("orderby"), top=args.get("top", 1000),
                    apply=args.get("apply"), count=True,
                )
                if resp.error or not resp.json:
                    return _from_response(resp), None
                rows = resp.json.get("rows") or []
                if not rows:
                    return {"status": "error", "error": "查询无数据,无法生成 Excel"}, None
                columns = (args.get("select").split(",") if args.get("select") else list(rows[0].keys()))
                columns = [c.strip() for c in columns]
                info = {
                    "username": self.username, "question": user_message,
                    "service": args["service"], "entity_set": args["entity_set"],
                    "odata_url": resp.url,
                    "row_count": resp.json.get("row_count_total") or len(rows),
                }
                task = self.orchestrator.run_free_query(
                    service=args["service"], entity_set=args["entity_set"],
                    columns=columns, rows=rows, info=info,
                    sheet_title=args.get("sheet_title", "数据"), username=self.username,
                )
                return {
                    "status": "done", "row_count": task.row_count,
                    "excel_filename": task.excel.path.name if task.excel else None,
                    "preview_rows": task.rows_preview[:5],
                }, task

            return {"status": "error", "error": f"未知动作: {action}"}, None

        except SkillNotFound as e:
            return {"status": "error", "error": f"Skill 不存在: {e}"}, None
        except KeyError as e:
            return {"status": "error", "error": f"参数缺失: {e}"}, None
        except Exception as e:                                     # noqa: BLE001
            log.exception("动作 %s 异常", action)
            return {"status": "error", "error": f"动作执行异常: {e}"}, None


def _from_response(resp: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "status": "error" if resp.error else "ok",
        "status_code": resp.status_code,
        "url": resp.url,
    }
    if resp.error:
        payload["error"] = resp.error
    if resp.json is not None:
        payload["data"] = resp.json
    elif resp.text:
        payload["text"] = resp.text[:1000]
    return payload
