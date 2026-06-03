// 管理员 — 审计日志
import { useEffect, useState } from "react";
import { api } from "../api";
import type { AuditRow } from "../types";

const ACTIONS = ["", "login", "logout", "run_skill", "chat", "export", "switch_model"];

export default function AdminAudit() {
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [username, setUsername] = useState("");
  const [action, setAction] = useState("");
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const { audit } = await api.listAudit({
        username: username || undefined,
        action: action || undefined,
        limit: 200,
      });
      setRows(audit);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, []);

  return (
    <div className="max-w-6xl">
      <h1 className="text-2xl font-semibold text-zinc-900">🔍 审计日志</h1>
      <p className="text-sm text-zinc-500 mt-1">最近 200 条操作记录,可按用户/动作过滤。</p>

      <div className="mt-4 flex gap-2 items-end">
        <label className="text-sm">
          <div className="text-zinc-600">用户</div>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="px-3 py-1.5 border border-zinc-300 rounded-md text-sm w-40"
            placeholder="留空 = 全部"
          />
        </label>
        <label className="text-sm">
          <div className="text-zinc-600">动作</div>
          <select
            value={action}
            onChange={(e) => setAction(e.target.value)}
            className="px-3 py-1.5 border border-zinc-300 rounded-md text-sm w-40"
          >
            {ACTIONS.map((a) => (
              <option key={a} value={a}>{a || "(全部)"}</option>
            ))}
          </select>
        </label>
        <button
          onClick={load}
          className="px-4 py-1.5 bg-brand-600 hover:bg-brand-700 text-white rounded-md text-sm"
        >
          查询
        </button>
      </div>

      <div className="mt-4 bg-white border border-zinc-200 rounded-lg overflow-hidden">
        <table className="w-full text-xs">
          <thead className="bg-zinc-100 text-zinc-700">
            <tr>
              <th className="px-2 py-2 text-left">时间</th>
              <th className="px-2 py-2 text-left">用户</th>
              <th className="px-2 py-2 text-left">动作</th>
              <th className="px-2 py-2 text-left">提问</th>
              <th className="px-2 py-2 text-left">服务</th>
              <th className="px-2 py-2 text-right">行数</th>
              <th className="px-2 py-2 text-right">用时</th>
              <th className="px-2 py-2 text-left">模型</th>
              <th className="px-2 py-2 text-right">tokens</th>
              <th className="px-2 py-2 text-left">IP</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={10} className="px-2 py-6 text-center text-zinc-400">加载中</td></tr>
            ) : rows.length === 0 ? (
              <tr><td colSpan={10} className="px-2 py-6 text-center text-zinc-400">无记录</td></tr>
            ) : rows.map((r) => (
              <tr key={r.id} className="border-t border-zinc-100 hover:bg-zinc-50">
                <td className="px-2 py-1 font-mono whitespace-nowrap">{r.created_at}</td>
                <td className="px-2 py-1">{r.username}</td>
                <td className="px-2 py-1 font-mono">{r.action}</td>
                <td className="px-2 py-1 max-w-xs truncate" title={r.question ?? ""}>{r.question}</td>
                <td className="px-2 py-1 font-mono">{r.service}</td>
                <td className="px-2 py-1 text-right font-mono">{r.row_count ?? ""}</td>
                <td className="px-2 py-1 text-right font-mono">{r.latency_ms ?? ""}</td>
                <td className="px-2 py-1 font-mono">{r.llm_model}</td>
                <td className="px-2 py-1 text-right font-mono">{r.llm_tokens ?? ""}</td>
                <td className="px-2 py-1 font-mono text-zinc-500">{r.ip}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
