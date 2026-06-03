// 我的任务历史
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { TaskListItem } from "../types";

const STATUS_BADGE: Record<string, string> = {
  done: "bg-emerald-100 text-emerald-700",
  failed: "bg-red-100 text-red-700",
  running: "bg-amber-100 text-amber-700",
};

export default function Tasks() {
  const [rows, setRows] = useState<TaskListItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.listTasks()
      .then(({ tasks }) => setRows(tasks))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="max-w-6xl">
      <h1 className="text-2xl font-semibold text-zinc-900">📁 我的任务</h1>
      <p className="text-sm text-zinc-500 mt-1">所有取数历史,Excel 可重新下载。</p>

      {loading ? (
        <div className="mt-6 text-zinc-500">加载中...</div>
      ) : rows.length === 0 ? (
        <div className="mt-6 px-4 py-8 bg-white border border-zinc-200 rounded-lg text-center">
          <div className="text-zinc-500">还没有任务,去 <Link to="/" className="text-brand-600">画廊</Link> 跑一个吧</div>
        </div>
      ) : (
        <div className="mt-6 bg-white border border-zinc-200 rounded-lg overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-zinc-100 text-zinc-700">
              <tr>
                <th className="px-3 py-2 text-left font-medium">时间</th>
                <th className="px-3 py-2 text-left font-medium">类型</th>
                <th className="px-3 py-2 text-left font-medium">提问</th>
                <th className="px-3 py-2 text-right font-medium">行数</th>
                <th className="px-3 py-2 text-right font-medium">用时(ms)</th>
                <th className="px-3 py-2 text-center font-medium">状态</th>
                <th className="px-3 py-2 text-center font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-t border-zinc-100 hover:bg-zinc-50">
                  <td className="px-3 py-2 text-zinc-600 font-mono text-xs whitespace-nowrap">
                    {r.created_at}
                  </td>
                  <td className="px-3 py-2 text-zinc-600">
                    {r.source === "skill" ? "📊 模板" : r.source === "chat" ? "💬 对话" : r.source}
                    {r.skill_id && <div className="text-xs text-zinc-400 font-mono">{r.skill_id}</div>}
                  </td>
                  <td className="px-3 py-2 text-zinc-800 max-w-md truncate" title={r.question}>
                    {r.question}
                  </td>
                  <td className="px-3 py-2 text-right text-zinc-700 font-mono">
                    {r.row_count ?? "—"}
                  </td>
                  <td className="px-3 py-2 text-right text-zinc-500 font-mono">
                    {r.latency_ms ?? "—"}
                  </td>
                  <td className="px-3 py-2 text-center">
                    <span className={`inline-block px-2 py-0.5 rounded text-xs ${STATUS_BADGE[r.status] ?? "bg-zinc-100 text-zinc-700"}`}>
                      {r.status}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-center">
                    {r.file_path ? (
                      <a
                        href={api.downloadTaskUrl(r.id)}
                        download={r.filename ?? undefined}
                        className="text-brand-600 hover:underline text-xs"
                      >
                        ⬇ 下载
                      </a>
                    ) : (
                      <span className="text-zinc-300 text-xs">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
