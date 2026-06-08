// 会话历史侧栏 —— 抄 DataAgent Sidebar 的会话列表交互(头像色块 + 高亮 + 重命名 + 删除)。
// SAPDataget 里"一个 chat 任务 = 一个会话",数据源 /api/chat/sessions。
import { useEffect, useState } from "react";
import { api } from "../api";
import type { ChatSession } from "../types";

const COLORS = [
  "bg-blue-100 text-blue-700", "bg-violet-100 text-violet-700", "bg-emerald-100 text-emerald-700",
  "bg-amber-100 text-amber-700", "bg-rose-100 text-rose-700", "bg-cyan-100 text-cyan-700",
  "bg-indigo-100 text-indigo-700", "bg-fuchsia-100 text-fuchsia-700",
];
function hashColor(id: string): string {
  let h = 0;
  for (const ch of id) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return COLORS[h % COLORS.length];
}
const avatarChar = (t: string) => (t.trim()[0] ?? "·").toUpperCase();

export default function ChatSessions({
  activeId,
  onSelect,
  onNew,
  refreshKey,
}: {
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  refreshKey: number;
}) {
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");

  const load = async () => {
    try {
      const { sessions } = await api.listChatSessions();
      setSessions(sessions);
    } catch {
      // ignore
    }
  };

  useEffect(() => { load(); }, [refreshKey]);

  const startRename = (s: ChatSession) => { setEditingId(s.id); setEditTitle(s.title); };
  const saveRename = async (s: ChatSession) => {
    const title = editTitle.trim();
    setEditingId(null);
    if (!title || title === s.title) return;
    try {
      await api.renameChatSession(s.id, title);
      setSessions((cs) => cs.map((x) => (x.id === s.id ? { ...x, title } : x)));
    } catch {
      // ignore
    }
  };
  const del = async (s: ChatSession) => {
    if (!confirm(`删除会话「${s.title}」？此操作不可撤销。`)) return;
    try {
      await api.deleteTask(s.id);
      setSessions((cs) => cs.filter((x) => x.id !== s.id));
      if (activeId === s.id) onNew();
    } catch {
      // ignore
    }
  };

  return (
    <aside className="hidden md:flex w-56 shrink-0 flex-col border-r border-zinc-200 bg-zinc-50/60">
      <div className="p-2">
        <button
          onClick={onNew}
          className="w-full flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-brand-600 hover:bg-brand-700 text-white text-sm font-medium"
        >
          ＋ 新对话
        </button>
      </div>
      <div className="px-3 pt-1 pb-1 text-[11px] font-medium text-zinc-400 uppercase tracking-wide">
        会话历史
      </div>
      <div className="flex-1 overflow-y-auto px-2 pb-2 space-y-0.5">
        {sessions.length === 0 && (
          <div className="px-2 py-3 text-xs text-zinc-400">还没有会话，发一条消息开始吧。</div>
        )}
        {sessions.map((s) => {
          const active = activeId === s.id;
          return (
            <div
              key={s.id}
              className={`group flex items-center gap-2 rounded-lg px-1.5 py-1.5 ${
                active ? "bg-brand-50 ring-1 ring-brand-200" : "hover:bg-white"
              }`}
            >
              <span className={`shrink-0 w-7 h-7 rounded-lg grid place-items-center text-xs font-semibold ${hashColor(s.id)}`}>
                {avatarChar(s.title)}
              </span>
              {editingId === s.id ? (
                <input
                  autoFocus
                  value={editTitle}
                  onChange={(e) => setEditTitle(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter") saveRename(s); else if (e.key === "Escape") setEditingId(null); }}
                  onBlur={() => saveRename(s)}
                  className="flex-1 min-w-0 text-sm px-1.5 py-1 rounded border border-brand-300 focus:outline-none bg-white"
                />
              ) : (
                <button
                  onClick={() => onSelect(s.id)}
                  className={`flex-1 min-w-0 text-left text-sm truncate ${active ? "text-brand-700 font-medium" : "text-zinc-700"}`}
                  title={s.title}
                >
                  {s.title}
                </button>
              )}
              {editingId !== s.id && (
                <div className="shrink-0 flex items-center opacity-0 group-hover:opacity-100">
                  <button onClick={() => startRename(s)} title="重命名" className="p-1 text-zinc-400 hover:text-brand-600 text-xs">✎</button>
                  <button onClick={() => del(s)} title="删除会话" className="p-1 text-zinc-400 hover:text-red-600 text-xs">🗑</button>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </aside>
  );
}
