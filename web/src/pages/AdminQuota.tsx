// Admin LLM token quota 管理 (P2-20)
import { useEffect, useState } from "react";
import { api } from "../api";
import type { AdminQuotaRow } from "../types";

export default function AdminQuota() {
  const [rows, setRows] = useState<AdminQuotaRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [newUser, setNewUser] = useState("");
  const [newLimit, setNewLimit] = useState<string>("");

  const load = async () => {
    const r = await api.adminListQuota();
    setRows(r.quota);
  };

  useEffect(() => { void load(); }, []);

  const setLimit = async (user: string, limit: number | null) => {
    setBusy(true);
    try {
      await api.adminSetQuota(user, limit);
      await load();
    } catch (e) {
      alert("失败: " + (e instanceof Error ? e.message : e));
    } finally { setBusy(false); }
  };

  return (
    <div className="max-w-5xl">
      <h1 className="text-2xl font-semibold text-zinc-900">📊 LLM Token 配额</h1>
      <p className="text-sm text-zinc-500 mt-1">
        当月各用户 LLM 用量 + 月度上限。达到上限时 /api/chat 会返回 429。
      </p>

      <div className="mt-6 p-4 bg-white border border-zinc-200 rounded-lg">
        <div className="text-sm font-medium text-zinc-700 mb-2">设置某用户配额</div>
        <div className="flex gap-2 items-end">
          <div>
            <label className="block text-xs text-zinc-500">用户名</label>
            <input
              value={newUser}
              onChange={(e) => setNewUser(e.target.value)}
              className="px-2 py-1.5 border border-zinc-300 rounded text-sm font-mono w-40"
            />
          </div>
          <div>
            <label className="block text-xs text-zinc-500">月 token 上限 (留空=无限)</label>
            <input
              value={newLimit}
              onChange={(e) => setNewLimit(e.target.value)}
              placeholder="例: 100000"
              className="px-2 py-1.5 border border-zinc-300 rounded text-sm font-mono w-40"
            />
          </div>
          <button
            onClick={() => {
              if (!newUser) return;
              const v = newLimit.trim() === "" ? null : Number(newLimit);
              if (v != null && (Number.isNaN(v) || v < 0)) { alert("上限必须是非负整数"); return; }
              setLimit(newUser, v).then(() => { setNewUser(""); setNewLimit(""); });
            }}
            disabled={busy || !newUser}
            className="px-3 py-1.5 bg-brand-600 text-white rounded text-sm hover:bg-brand-700 disabled:opacity-50"
          >
            保存
          </button>
        </div>
      </div>

      <table className="mt-6 w-full text-sm bg-white border border-zinc-200 rounded-lg">
        <thead className="bg-zinc-100 text-zinc-700">
          <tr>
            <th className="px-3 py-2 text-left">用户</th>
            <th className="px-3 py-2 text-right">输入 tokens</th>
            <th className="px-3 py-2 text-right">输出 tokens</th>
            <th className="px-3 py-2 text-right">调用次数</th>
            <th className="px-3 py-2 text-right">上限</th>
            <th className="px-3 py-2 text-center">操作</th>
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr><td colSpan={6} className="text-zinc-400 text-center py-6">本月暂无 LLM 用量</td></tr>
          ) : rows.map((r) => {
            const total = r.input_tokens + r.output_tokens;
            const pct = r.limit_tokens ? Math.min(100, Math.round((total / r.limit_tokens) * 100)) : 0;
            return (
              <tr key={r.username} className="border-t border-zinc-100 hover:bg-zinc-50">
                <td className="px-3 py-2 font-mono">{r.username}</td>
                <td className="px-3 py-2 text-right font-mono">{r.input_tokens.toLocaleString()}</td>
                <td className="px-3 py-2 text-right font-mono">{r.output_tokens.toLocaleString()}</td>
                <td className="px-3 py-2 text-right font-mono">{r.call_count}</td>
                <td className="px-3 py-2 text-right font-mono">
                  {r.limit_tokens != null ? (
                    <div>
                      {r.limit_tokens.toLocaleString()}
                      <div className="text-xs text-zinc-500">{pct}% 已用</div>
                    </div>
                  ) : (
                    <span className="text-zinc-400">∞</span>
                  )}
                </td>
                <td className="px-3 py-2 text-center">
                  <button
                    onClick={() => setLimit(r.username, null)}
                    className="text-xs text-red-600 hover:underline"
                  >
                    取消限制
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
