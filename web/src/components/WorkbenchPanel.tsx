// 工作台面板 —— 把一轮对话的事件流渲染成活动日志 / 工具时间线 (对标 DataAgent)。
// 每个 LLM 调用、工具执行、错误都对应一行;llm_call/tool_start 配对到 *_done 后停转 spinner。
import { useMemo, useState } from "react";
import type { AgentEvent } from "../types";

// 把"已配对完成"的起始事件下标算出来 —— 决定哪些 spinner 该停。
export function resolvedStarts(events: AgentEvent[]): Set<number> {
  const resolved = new Set<number>();
  const openLlm: number[] = [];
  const openTool: number[] = [];
  events.forEach((ev, i) => {
    if (ev.kind !== "progress") return;
    const phase = String((ev.payload as { phase?: string }).phase ?? "");
    if (phase === "llm_call") openLlm.push(i);
    else if (phase === "tool_start") openTool.push(i);
    else if (phase === "llm_done") {
      const s = openLlm.pop();
      if (s !== undefined) resolved.add(s);
    } else if (phase === "tool_done") {
      const s = openTool.pop();
      if (s !== undefined) resolved.add(s);
    }
  });
  return resolved;
}

function Spinner() {
  return (
    <span className="inline-block w-3 h-3 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
  );
}

interface RowProps {
  ev: AgentEvent;
  idx: number;
  spinning: boolean;
}

function EventRow({ ev, idx, spinning }: RowProps) {
  const [open, setOpen] = useState(false);
  const p = ev.payload as Record<string, unknown>;

  if (ev.kind === "progress") {
    const phase = String(p.phase ?? "");
    // *_done 事件不单独占行(它只是去停对应的 *_call/start 的 spinner)
    if (phase === "llm_done" || phase === "tool_done") return null;
    const ok = p.ok !== false;
    return (
      <div className="flex items-center gap-2 text-xs text-zinc-600 py-1">
        {spinning ? <Spinner /> : <span className={ok ? "text-emerald-500" : "text-red-500"}>{ok ? "✓" : "✕"}</span>}
        <span className="truncate">{String(p.msg ?? phase)}</span>
      </div>
    );
  }

  if (ev.kind === "tool_call") {
    return (
      <div className="py-1">
        <button
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-2 text-xs font-mono text-zinc-700 hover:text-brand-600 w-full text-left"
        >
          <span className="text-zinc-400">{open ? "▾" : "▸"}</span>
          <span className="px-1.5 py-0.5 rounded bg-brand-50 text-brand-700">{String(p.action)}</span>
        </button>
        {open && (
          <pre className="mt-1 ml-5 text-[11px] bg-zinc-50 rounded p-2 overflow-x-auto text-zinc-600">
            {JSON.stringify(p.args, null, 2)}
          </pre>
        )}
      </div>
    );
  }

  if (ev.kind === "tool_result") {
    const result = p.result as { status?: string } | undefined;
    const isErr = result?.status === "error";
    return (
      <div className="py-1">
        <button
          onClick={() => setOpen((o) => !o)}
          className={`flex items-center gap-2 text-xs w-full text-left ${isErr ? "text-red-600" : "text-emerald-600"}`}
        >
          <span className="text-zinc-400">{open ? "▾" : "▸"}</span>
          {isErr ? "✕ 结果(错误)" : "✓ 结果"}
        </button>
        {open && (
          <pre className="mt-1 ml-5 text-[11px] bg-zinc-50 rounded p-2 overflow-x-auto max-h-48 text-zinc-600">
            {JSON.stringify(p.result, null, 2)}
          </pre>
        )}
      </div>
    );
  }

  if (ev.kind === "thought") {
    return (
      <div className="py-1 text-xs text-zinc-500 italic flex gap-2">
        <span className="text-zinc-400">💭</span>
        <span>{String(p.text)}</span>
      </div>
    );
  }

  if (ev.kind === "task") {
    const ex = p.excel as { filename?: string } | null | undefined;
    return (
      <div className="py-1 text-xs text-emerald-700 flex items-center gap-2">
        <span>📄</span>
        <span className="truncate">已生成 {ex?.filename ?? "Excel"}（{String(p.row_count ?? "?")} 行）</span>
      </div>
    );
  }

  if (ev.kind === "final") {
    const err = p.error === true;
    return (
      <div className={`py-1 text-xs flex items-center gap-2 ${err ? "text-red-600" : "text-zinc-500"}`}>
        <span>{err ? "⚠" : "🏁"}</span>
        <span>{err ? "出错结束" : "完成"}</span>
      </div>
    );
  }

  // thought_delta / answer_delta / meta 不进工作台(已在主气泡里渲染)
  void idx;
  return null;
}

export default function WorkbenchPanel({
  events,
  live,
}: {
  events: AgentEvent[];
  live: boolean;
}) {
  const resolved = useMemo(() => resolvedStarts(events), [events]);

  const visible = events.filter(
    (e) => !["thought_delta", "answer_delta", "meta"].includes(e.kind),
  );

  if (visible.length === 0) {
    return (
      <div className="text-xs text-zinc-400 px-3 py-4">
        {live ? "等待智能体动作…" : "暂无活动记录"}
      </div>
    );
  }

  return (
    <div className="px-3 py-2 space-y-0.5">
      {events.map((ev, i) => {
        if (["thought_delta", "answer_delta", "meta"].includes(ev.kind)) return null;
        // 起始事件且未配对 → 还在转
        const phase = ev.kind === "progress" ? String((ev.payload as { phase?: string }).phase ?? "") : "";
        const isStart = phase === "llm_call" || phase === "tool_start";
        const spinning = live && isStart && !resolved.has(i);
        return <EventRow key={i} ev={ev} idx={i} spinning={spinning} />;
      })}
    </div>
  );
}
