// 自由对话页 —— 走 LLM (P1-11: 支持多轮 /chat/:task_id)
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import type { ChatResponse, TaskMessage } from "../types";
import DataTable from "../components/DataTable";
import ExcelCard from "../components/ExcelCard";
import { useAuth } from "../auth";

interface Turn {
  user: string;
  resp?: ChatResponse;
  err?: string;
  pending?: boolean;
}

export default function Chat() {
  const { status } = useAuth();
  const { task_id: routeTaskId } = useParams();
  const navigate = useNavigate();
  const [input, setInput] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [activeTask, setActiveTask] = useState<string | null>(routeTaskId || null);

  const llmReady = status?.llm.current_ready ?? false;

  // 路由切换时加载历史
  useEffect(() => {
    setActiveTask(routeTaskId || null);
    if (!routeTaskId) { setTurns([]); return; }
    (async () => {
      try {
        const { messages } = await api.taskMessages(routeTaskId);
        setTurns(messagesToTurns(messages));
      } catch {
        setTurns([]);
      }
    })();
  }, [routeTaskId]);

  const send = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || busy) return;
    const msg = input.trim();
    setInput("");
    setBusy(true);
    setTurns((ts) => [...ts, { user: msg, pending: true }]);
    try {
      const r = await api.chat(msg, activeTask || undefined);
      // 首次回复 → 把 url 切到 /chat/:task_id (沉淀对话)
      if (!activeTask && r.task_id) {
        setActiveTask(r.task_id);
        navigate(`/chat/${r.task_id}`, { replace: true });
      }
      setTurns((ts) =>
        ts.map((t, i) => (i === ts.length - 1 ? { ...t, resp: r, pending: false } : t)),
      );
    } catch (e2) {
      const msgErr = e2 instanceof Error ? e2.message : String(e2);
      setTurns((ts) =>
        ts.map((t, i) =>
          i === ts.length - 1 ? { ...t, err: msgErr, pending: false } : t,
        ),
      );
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    setTurns([]);
    setActiveTask(null);
    navigate("/chat");
  };

  return (
    <div className="max-w-4xl flex flex-col h-full">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-2xl font-semibold text-zinc-900">💬 自由对话</h1>
          <p className="text-sm text-zinc-500 mt-1">
            用大白话描述你想要的数据,智能体会自动找模板或拼 OData 查询、生成 Excel。
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
          {turns.length > 0 && (
            <button
              onClick={reset}
              className="text-sm text-zinc-500 hover:text-red-600 px-3 py-1.5 border border-zinc-300 rounded-md"
            >
              新对话
            </button>
          )}
        </div>
      </div>

      {!llmReady && (
        <div className="mb-4 px-4 py-3 bg-amber-50 border border-amber-200 rounded-lg text-sm text-amber-800">
          ⚠ 当前模型 <span className="font-mono">{status?.llm.current}</span> 尚未配置 API key。
          请在 <code>.env</code> 设置对应的 <code>*_API_KEY</code>,或点右上角模型选择器换一个已配置的模型。
        </div>
      )}

      {/* 对话区 */}
      <div className="flex-1 overflow-y-auto space-y-4 pb-4">
        {turns.length === 0 && (
          <div className="bg-white rounded-lg border border-zinc-200 p-6">
            <div className="text-sm text-zinc-500 mb-3">💡 试试这些提问:</div>
            <div className="space-y-2">
              {[
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
              <div className="max-w-2xl px-4 py-2 bg-brand-600 text-white rounded-lg rounded-br-sm">
                {t.user}
              </div>
            </div>

            {t.pending && (
              <div className="mt-3 flex">
                <div className="px-4 py-2 bg-white border border-zinc-200 rounded-lg text-zinc-500 text-sm">
                  <span className="inline-block animate-pulse">智能体思考中...</span>
                </div>
              </div>
            )}

            {t.err && (
              <div className="mt-3 px-4 py-2 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
                ❌ {t.err}
              </div>
            )}

            {t.resp && (
              <div className="mt-3 bg-white border border-zinc-200 rounded-lg p-4">
                {/* 工具调用过程(可折叠) */}
                {t.resp.tool_calls.length > 0 && (
                  <details className="mb-3">
                    <summary className="cursor-pointer text-xs text-zinc-500">
                      ⚙ 工具调用 ({t.resp.tool_calls.length} 次,迭代 {t.resp.iterations} 轮)
                    </summary>
                    <div className="mt-2 space-y-1">
                      {t.resp.tool_calls.map((tc, j) => (
                        <div key={j} className={`px-3 py-1 rounded text-xs font-mono ${
                          tc.is_error ? "bg-red-50 text-red-700" : "bg-zinc-50 text-zinc-700"
                        }`}>
                          {tc.is_error && "❌ "}{tc.name}({JSON.stringify(tc.arguments)})
                        </div>
                      ))}
                    </div>
                  </details>
                )}

                {/* 答复正文 */}
                <div className="whitespace-pre-wrap text-zinc-800">{t.resp.answer}</div>

                {/* Excel 卡 */}
                {t.resp.task?.excel && (
                  <div className="mt-3">
                    <ExcelCard
                      {...t.resp.task.excel}
                      row_count={t.resp.task.row_count}
                    />
                  </div>
                )}

                {/* 预览表 */}
                {t.resp.task?.rows_preview && t.resp.task.rows_preview.length > 0 && (
                  <details className="mt-3">
                    <summary className="cursor-pointer text-xs text-zinc-500">
                      📊 数据预览 ({t.resp.task.row_count} 行)
                    </summary>
                    <div className="mt-2">
                      <DataTable rows={t.resp.task.rows_preview} maxRows={20} />
                    </div>
                  </details>
                )}

                <div className="mt-3 text-xs text-zinc-400">
                  {t.resp.llm_model} · in {t.resp.input_tokens} / out {t.resp.output_tokens} tokens
                </div>
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
          placeholder={llmReady ? "输入你的取数需求...(Enter 发送, Shift+Enter 换行)" : "请先配置 LLM API key"}
          disabled={!llmReady || busy}
          rows={2}
          className="flex-1 px-3 py-2 border border-zinc-300 rounded-md text-sm resize-none focus:outline-none focus:ring-2 focus:ring-brand-500 disabled:bg-zinc-100"
        />
        <button
          type="submit"
          disabled={!llmReady || busy || !input.trim()}
          className="px-5 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-md font-medium disabled:opacity-50"
        >
          {busy ? "..." : "发送"}
        </button>
      </form>
    </div>
  );
}


function messagesToTurns(messages: TaskMessage[]): Turn[] {
  const turns: Turn[] = [];
  let pending: Turn | null = null;
  for (const m of messages) {
    if (m.role === "user") {
      if (pending) turns.push(pending);
      pending = { user: m.text || "" };
    } else if (m.role === "assistant" && pending) {
      // 把 assistant 消息当作 resp 装回去 (尽量保留信息,无 tokens 等)
      const blocks = m.blocks || {};
      pending.resp = {
        task_id: m.task_id,
        answer: m.text || "",
        iterations: 0,
        tool_calls: blocks.tool_calls || [],
        input_tokens: 0,
        output_tokens: 0,
        llm_model: "",
        task: blocks.task ? {
          status: blocks.task.status as "done" | "failed",
          row_count: blocks.task.row_count || 0,
          rows_preview: [],
          excel: blocks.task.excel_filename ? {
            filename: blocks.task.excel_filename,
            size_bytes: 0,
            download_url: `/api/tasks/${m.task_id}/file`,
          } : null,
        } : null,
      };
      turns.push(pending);
      pending = null;
    }
  }
  if (pending) turns.push(pending);
  return turns;
}
