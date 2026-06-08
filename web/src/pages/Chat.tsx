// 自由对话页 —— SSE 流式 (JSON-action 协议, 对标 DataAgent)
// 思考逐字可见 + 答案打字机 + 右侧工作台时间线 + Excel 卡;保留多轮、UName 过滤、快捷按钮。
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import type { AgentEvent, ChatTaskPayload, TaskMessage } from "../types";
import DataTable from "../components/DataTable";
import ExcelCard from "../components/ExcelCard";
import WorkbenchPanel from "../components/WorkbenchPanel";
import ChatSessions from "../components/ChatSessions";
import { useAuth } from "../auth";

interface Turn {
  user: string;
  events: AgentEvent[];
  thought: string;          // 实时思考(打字机)
  answer: string;           // 实时答案(打字机)
  task: ChatTaskPayload | null;
  error?: boolean;
  pending?: boolean;
}

function humanizeError(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401) return "登录已过期,请重新登录。";
    if (e.status === 429) return "本月 LLM 配额已用完,请联系管理员。";
    return e.message || `请求失败 (${e.status})`;
  }
  const msg = e instanceof Error ? e.message : String(e);
  if (/network|failed to fetch/i.test(msg)) return "网络中断,请检查连接或代理后重试。";
  return msg;
}

export default function Chat() {
  const { status, identity } = useAuth();
  const { task_id: routeTaskId } = useParams();
  const navigate = useNavigate();
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [activeTask, setActiveTask] = useState<string | null>(routeTaskId || null);
  const abortRef = useRef<AbortController | null>(null);
  const skipLoadRef = useRef<string | null>(null);   // 自己刚导航到的 task,别重复拉历史
  const [sessionsRefresh, setSessionsRefresh] = useState(0);  // 触发会话侧栏刷新

  const llmReady = status?.llm.current_ready ?? false;
  const reportListHint = /报告(清单|列表)|报表(清单|列表)|report\s*list|query\s*list/i.test(input);

  // 路由切换时加载历史(自导航的不重复加载)
  useEffect(() => {
    setActiveTask(routeTaskId || null);
    if (!routeTaskId) {
      if (skipLoadRef.current === null) setTurns([]);
      return;
    }
    if (skipLoadRef.current === routeTaskId) return;   // 流式刚建/续的这条,已有 live turns
    (async () => {
      try {
        const { messages } = await api.taskMessages(routeTaskId);
        setTurns(messagesToTurns(messages));
      } catch {
        setTurns([]);
      }
    })();
  }, [routeTaskId]);

  const patchLast = (fn: (t: Turn) => Turn) =>
    setTurns((ts) => ts.map((t, i) => (i === ts.length - 1 ? fn(t) : t)));

  const submitMessage = async (msg: string) => {
    if (!msg.trim() || busy) return;
    const normalized = msg.trim();
    setInput("");
    setBusy(true);
    setTurns((ts) => [...ts, {
      user: normalized, events: [], thought: "", answer: "", task: null, pending: true,
    }]);

    const ac = new AbortController();
    abortRef.current = ac;
    try {
      const { task_id } = await api.streamChat(
        normalized,
        activeTask || undefined,
        (ev) => handleEvent(ev),
        ac.signal,
      );
      // 流式完成后,首轮把 URL 切到 /chat/:id(可分享);跳过随后的历史重载;刷新会话侧栏。
      if (task_id && task_id !== activeTask) {
        setActiveTask(task_id);
        skipLoadRef.current = task_id;
        if (!routeTaskId) navigate(`/chat/${task_id}`, { replace: true });
        setSessionsRefresh((n) => n + 1);
      }
      patchLast((t) => ({ ...t, pending: false }));
    } catch (e) {
      if ((e as Error)?.name === "AbortError") {
        patchLast((t) => ({ ...t, pending: false, answer: t.answer || "(已停止生成)" }));
      } else {
        patchLast((t) => ({ ...t, pending: false, error: true, answer: humanizeError(e) }));
      }
    } finally {
      setBusy(false);
      abortRef.current = null;
    }
  };

  const handleEvent = (ev: AgentEvent) => {
    const p = ev.payload as Record<string, unknown>;
    if (ev.kind === "meta") {
      const tid = p.task_id as string | undefined;
      if (tid && !activeTask) { setActiveTask(tid); skipLoadRef.current = tid; }
      return;
    }
    if (ev.kind === "thought_delta") {
      patchLast((t) => ({ ...t, thought: String(p.text ?? "") }));
      return;
    }
    if (ev.kind === "answer_delta") {
      patchLast((t) => ({ ...t, answer: String(p.text ?? "") }));
      return;
    }
    if (ev.kind === "task") {
      patchLast((t) => ({ ...t, task: p as ChatTaskPayload, events: [...t.events, ev] }));
      return;
    }
    if (ev.kind === "final") {
      patchLast((t) => ({
        ...t,
        answer: String(p.text ?? t.answer),
        error: p.error === true,
        events: [...t.events, ev],
      }));
      return;
    }
    // progress / thought / tool_call / tool_result → 进工作台
    patchLast((t) => ({ ...t, events: [...t.events, ev] }));
  };

  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    await submitMessage(input);
  };

  const stop = () => abortRef.current?.abort();

  const reset = () => {
    skipLoadRef.current = null;
    setTurns([]);
    setActiveTask(null);
    navigate("/chat");
  };

  const filterRowsByLoginUser = (rows: Record<string, unknown>[]) => {
    if (!rows.length || !identity?.username) return rows;
    const unameKey = Object.keys(rows[0]).find((k) => k.toLowerCase() === "uname");
    if (!unameKey) return rows;
    const normalizeUser = (value: unknown) =>
      String(value ?? "").toUpperCase().replace(/\s+/g, "").trim();
    const loginName = normalizeUser(identity.username);
    return rows.filter((row) => normalizeUser(row[unameKey]) === loginName);
  };

  const lastTurn = turns[turns.length - 1];

  return (
    <div className="flex gap-4 h-full">
      {/* 最左:会话历史侧栏(抄 DataAgent) */}
      <ChatSessions
        activeId={activeTask}
        onSelect={(id) => { skipLoadRef.current = null; navigate(`/chat/${id}`); }}
        onNew={reset}
        refreshKey={sessionsRefresh}
      />

      {/* 中:对话区 */}
      <div className="flex-1 min-w-0 flex flex-col max-w-4xl">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h1 className="text-2xl font-semibold text-zinc-900">💬 自由对话</h1>
            <p className="text-sm text-zinc-500 mt-1">
              用大白话描述你想要的数据,智能体会自动找模板或拼 OData 查询、生成 Excel —— 思考与执行实时可见。
            </p>
          </div>
          <div className="flex items-center gap-2">
            {activeTask && (
              <Link
                to={`/tasks/${activeTask}`}
                className="text-sm text-zinc-500 hover:text-brand-600 px-3 py-1.5 border border-zinc-300 rounded-md"
              >
                📁 查看任务详情
              </Link>
            )}
            {/* "新对话"已移到左侧会话侧栏顶部,头部不再重复 */}
          </div>
        </div>

        {!llmReady && (
          <div className="mb-4 px-4 py-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
            ⚠ 当前模型 <span className="font-mono">{status?.llm.current || "(未设置)"}</span> 尚未就绪。
            请到 <Link to="/llm-keys" className="text-brand-600 hover:underline">🔑 LLM 设置</Link> 配置 API key + Base URL + Model（或在 <code>.env</code> 设默认）。
            仍可直接输入“报告清单”“报告列表”等关键字,系统会走内置 OData 查询。
          </div>
        )}

        <div className="mb-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => { void submitMessage("报告清单"); }}
            disabled={busy}
            className="px-3 py-1.5 text-sm rounded-md border border-zinc-300 bg-white hover:bg-zinc-50 text-zinc-700 disabled:opacity-50"
          >
            快速查询: 报告清单
          </button>
          <button
            type="button"
            onClick={() => { void submitMessage("报告清单前100条"); }}
            disabled={busy}
            className="px-3 py-1.5 text-sm rounded-md border border-zinc-300 bg-white hover:bg-zinc-50 text-zinc-700 disabled:opacity-50"
          >
            快速查询: 报告清单前100条
          </button>
        </div>

        {/* 对话气泡 */}
        <div className="flex-1 overflow-y-auto space-y-4 pb-4">
          {turns.length === 0 && (
            <div className="bg-white rounded-lg border border-zinc-200 p-6">
              <div className="text-sm text-zinc-500 mb-3">💡 试试这些提问:</div>
              <div className="space-y-2">
                {[
                  "报告清单",
                  "报告列表",
                  "上月华东大区销售情况",
                  "本月各工厂良率,低于 95% 的重点标记",
                  "找出 2026 年 5 月销售额最高的 10 个客户",
                  "ZBW_SALES_SRV 服务里有哪些字段?",
                ].map((q) => (
                  <button
                    key={q}
                    onClick={() => setInput(q)}
                    className="block w-full text-left px-3 py-2 rounded-md bg-zinc-50 hover:bg-brand-50 text-sm text-zinc-700"
                  >
                    {q}
                  </button>
                ))}
              </div>
            </div>
          )}

          {turns.map((t, i) => (
            <div key={i}>
              <div className="flex justify-end">
                <div className="max-w-2xl px-4 py-2 bg-brand-600 text-white rounded-lg rounded-br-sm whitespace-pre-wrap">
                  {t.user}
                </div>
              </div>

              {/* 思考 chip(有内容才显示) */}
              {t.thought && (!t.answer || t.pending) && (
                <div className="mt-2 flex">
                  <div className="px-3 py-1.5 bg-zinc-100 border border-zinc-200 rounded-lg text-xs text-zinc-500 italic max-w-2xl">
                    💭 {t.thought}
                  </div>
                </div>
              )}

              {/* 助手气泡 */}
              {(t.answer || t.task || (t.pending && !t.thought)) && (
                <div className={`mt-2 bg-white border rounded-lg p-4 ${t.error ? "border-red-200" : "border-zinc-200"}`}>
                  {t.pending && !t.answer && (
                    <div className="text-zinc-500 text-sm">
                      <span className="inline-block animate-pulse">智能体处理中…</span>
                    </div>
                  )}
                  {t.answer && (
                    <div className={`whitespace-pre-wrap ${t.error ? "text-red-700" : "text-zinc-800"}`}>
                      {t.answer}
                      {t.pending && <span className="inline-block w-1.5 h-4 ml-0.5 align-middle bg-brand-400 animate-pulse" />}
                    </div>
                  )}

                  {/* Excel 卡 */}
                  {t.task?.excel && (
                    <div className="mt-3">
                      <ExcelCard
                        filename={t.task.excel.filename}
                        size_bytes={t.task.excel.size_bytes}
                        download_url={t.task.excel.download_url}
                        row_count={t.task.row_count}
                      />
                    </div>
                  )}

                  {/* 预览表(仅当前登录账号 UName) */}
                  {t.task?.rows_preview && t.task.rows_preview.length > 0 && (
                    <details className="mt-3">
                      <summary className="cursor-pointer text-xs text-zinc-500">
                        📊 数据预览 (仅显示当前登录账号 UName，预览 {filterRowsByLoginUser(t.task.rows_preview).length} 行)
                      </summary>
                      <div className="mt-2">
                        <DataTable rows={filterRowsByLoginUser(t.task.rows_preview)} maxRows={20} />
                      </div>
                    </details>
                  )}

                  {/* 本轮工作台(完成后折叠) */}
                  {t.events.filter((e) => !["thought_delta", "answer_delta", "meta", "final"].includes(e.kind)).length > 0 && (
                    <details className="mt-3">
                      <summary className="cursor-pointer text-xs text-zinc-500">
                        ⚙ 执行过程({t.events.filter((e) => e.kind === "tool_call").length} 次工具调用)
                      </summary>
                      <div className="mt-1 border border-zinc-100 rounded-md">
                        <WorkbenchPanel events={t.events} live={false} />
                      </div>
                    </details>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>

        {/* 输入框 */}
        <form onSubmit={send} className="flex gap-2 sticky bottom-0 bg-zinc-50 pt-3">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                send(e as unknown as React.FormEvent);
              }
            }}
            placeholder={llmReady || reportListHint ? "输入你的取数需求...(Enter 发送, Shift+Enter 换行)" : "可直接输入“报告清单”，其余需求需先配置 LLM API key"}
            disabled={busy}
            rows={2}
            className="flex-1 px-3 py-2 border border-zinc-300 rounded-md text-sm resize-none focus:outline-none focus:ring-2 focus:ring-brand-500 disabled:bg-zinc-100"
          />
          {busy ? (
            <button
              type="button"
              onClick={stop}
              className="px-5 py-2 bg-red-500 hover:bg-red-600 text-white rounded-md font-medium"
            >
              停止
            </button>
          ) : (
            <button
              type="submit"
              disabled={!input.trim() || (!llmReady && !reportListHint)}
              className="px-5 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-md font-medium disabled:opacity-50"
            >
              发送
            </button>
          )}
        </form>
      </div>

      {/* 右:实时工作台(宽屏显示当前轮) */}
      <aside className="hidden lg:flex w-80 shrink-0 flex-col">
        <div className="sticky top-0">
          <div className="flex items-center justify-between px-3 py-2 border-b border-zinc-200">
            <span className="text-sm font-medium text-zinc-700">🛠 工作台</span>
            {busy && <span className="text-xs text-brand-600">运行中…</span>}
          </div>
          <div className="bg-white border border-zinc-200 rounded-b-lg max-h-[calc(100vh-12rem)] overflow-y-auto">
            <WorkbenchPanel events={lastTurn?.events ?? []} live={!!busy} />
          </div>
        </div>
      </aside>
    </div>
  );
}


// 历史消息 → Turn[]:优先用 blocks.events 复原工作台,否则退回旧 tool_calls/task。
function messagesToTurns(messages: TaskMessage[]): Turn[] {
  const turns: Turn[] = [];
  let pending: Turn | null = null;
  for (const m of messages) {
    if (m.role === "user") {
      if (pending) turns.push(pending);
      pending = { user: m.text || "", events: [], thought: "", answer: "", task: null };
    } else if (m.role === "assistant" && pending) {
      const blocks = m.blocks || {};
      const events = blocks.events || [];
      let task: ChatTaskPayload | null = null;
      // 从事件流里捞 task(含完整 excel + 预览)
      for (const ev of events) {
        if (ev.kind === "task") task = ev.payload as ChatTaskPayload;
      }
      // 旧消息 / 报告清单快捷:从 blocks.task 重建下载卡
      if (!task && blocks.task && blocks.task.excel_filename) {
        task = {
          task_id: m.task_id,
          status: blocks.task.status,
          row_count: blocks.task.row_count,
          rows_preview: [],
          excel: {
            filename: blocks.task.excel_filename,
            size_bytes: 0,
            download_url: `/api/tasks/${m.task_id}/file`,
          },
        };
      }
      pending.answer = m.text || "";
      pending.events = events;
      pending.task = task;
      turns.push(pending);
      pending = null;
    }
  }
  if (pending) turns.push(pending);
  return turns;
}
