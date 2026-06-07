// Skill 详情 + 参数表单 + 跑结果
import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api } from "../api";
import type { SkillDetail, SkillRunResponse } from "../types";
import DataTable from "../components/DataTable";
import ExcelCard from "../components/ExcelCard";

export default function SkillDetailPage() {
  const { skill_id } = useParams<{ skill_id: string }>();
  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const [params, setParams] = useState<Record<string, string>>({});
  const [result, setResult] = useState<SkillRunResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!skill_id) return;
    api.getSkill(skill_id).then((s) => {
      setSkill(s);
      const init: Record<string, string> = {};
      for (const p of s.params) {
        if (p.default !== undefined && p.default !== null) {
          init[p.name] = String(p.default);
        }
      }
      setParams(init);
    }).catch((e) => setErr(String(e)));
  }, [skill_id]);

  const run = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!skill_id) return;
    setErr(null);
    setBusy(true);
    setResult(null);
    try {
      // 转参: 空 string -> undefined; 数字字符串 -> 数字
      const cleaned: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(params)) {
        if (v === "" || v === undefined) continue;
        const n = Number(v);
        cleaned[k] = !Number.isNaN(n) && /^-?\d+(\.\d+)?$/.test(v) ? n : v;
      }
      const r = await api.runSkill(skill_id, cleaned);
      setResult(r);
      if (r.status !== "done") setErr(r.error || "未知错误");
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  };

  if (!skill) {
    return <div className="text-zinc-500">{err ?? "加载中..."}</div>;
  }

  return (
    <div className="max-w-5xl">
      <Link to="/" className="text-sm text-brand-600 hover:underline">← 返回画廊</Link>
      <h1 className="mt-3 text-2xl font-semibold text-zinc-900 flex items-center gap-2">
        📊 {skill.title}
      </h1>
      <div className="mt-1 text-xs text-zinc-400 font-mono">{skill.id} · {skill.service}/{skill.entity_set}</div>
      <p className="mt-3 text-sm text-zinc-700">{skill.description}</p>

      <form onSubmit={run} className="mt-6 bg-white rounded-lg border border-zinc-200 p-5">
        <div className="text-sm font-medium text-zinc-700 mb-3">参数</div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {skill.params.map((p) => (
            <label key={p.name} className="block">
              <div className="flex items-center gap-2 text-sm">
                <span className="text-zinc-700">{p.name}</span>
                {p.required && <span className="text-red-500 text-xs">*必填</span>}
                {p.default !== undefined && p.default !== null && (
                  <span className="text-zinc-400 text-xs">默认: {String(p.default)}</span>
                )}
              </div>
              <div className="text-xs text-zinc-500 mb-1">{p.description}</div>
              {p.enum && p.enum.length > 0 ? (
                <select
                  value={params[p.name] ?? ""}
                  onChange={(e) => setParams({ ...params, [p.name]: e.target.value })}
                  className="w-full px-3 py-1.5 border border-zinc-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
                >
                  <option value="">— 选择 —</option>
                  {p.enum.map((v) => (
                    <option key={v} value={v}>{v}</option>
                  ))}
                </select>
              ) : (
                <input
                  type="text"
                  value={params[p.name] ?? ""}
                  onChange={(e) => setParams({ ...params, [p.name]: e.target.value })}
                  placeholder={p.default !== undefined && p.default !== null ? String(p.default) : ""}
                  className="w-full px-3 py-1.5 border border-zinc-300 rounded-md text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
                />
              )}
            </label>
          ))}
        </div>

        {err && (
          <div className="mt-4 px-3 py-2 rounded-md bg-red-50 border border-red-200 text-sm text-red-700">
            {err}
          </div>
        )}

        <div className="mt-4 flex gap-3">
          <button
            type="submit"
            disabled={busy}
            className="px-5 py-2 bg-brand-600 hover:bg-brand-700 text-white rounded-md font-medium disabled:opacity-50"
          >
            {busy ? "生成中..." : "✨ 生成 Excel"}
          </button>
        </div>
      </form>

      {result && result.status === "done" && (
        <div className="mt-6 bg-white rounded-lg border border-zinc-200 p-5">
          <div className="flex items-center justify-between mb-3">
            <div className="font-medium text-zinc-900">
              ✅ 查询完成 — {result.row_count} 行
            </div>
            {result.excel && (
              <ExcelCard {...result.excel} row_count={result.row_count} />
            )}
          </div>
          <DataTable rows={result.rows_preview} />
        </div>
      )}

      {skill.instructions && (
        <details className="mt-6 bg-zinc-100 rounded-lg p-4">
          <summary className="cursor-pointer text-sm text-zinc-600">📝 模板内部说明 (给 LLM 的指引)</summary>
          <pre className="mt-3 text-xs text-zinc-700 whitespace-pre-wrap">{skill.instructions}</pre>
        </details>
      )}
    </div>
  );
}
