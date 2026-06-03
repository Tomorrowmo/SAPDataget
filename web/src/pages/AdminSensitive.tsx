// 管理员 — 敏感字段配置
import { useEffect, useState } from "react";
import { api } from "../api";
import type { SensitiveField } from "../types";

export default function AdminSensitive() {
  const [rows, setRows] = useState<SensitiveField[]>([]);
  const [service, setService] = useState("");
  const [field, setField] = useState("");
  const [mask, setMask] = useState<SensitiveField["mask_mode"]>("redact");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = async () => {
    const { fields } = await api.listSensitive();
    setRows(fields);
  };

  useEffect(() => { load(); }, []);

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!service || !field) return;
    setBusy(true);
    setMsg(null);
    try {
      await api.upsertSensitive(service, field, mask);
      setMsg(`✅ 已添加 ${service}.${field}`);
      setField("");
      await load();
    } catch (e2) {
      setMsg(`❌ ${e2 instanceof Error ? e2.message : e2}`);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (svc: string, f: string) => {
    if (!confirm(`确认删除 ${svc}.${f}?`)) return;
    await api.deleteSensitive(svc, f);
    await load();
  };

  return (
    <div className="max-w-5xl">
      <h1 className="text-2xl font-semibold text-zinc-900">🔒 敏感字段配置</h1>
      <p className="text-sm text-zinc-500 mt-1">
        登记的字段在发往 LLM 前会按 mask_mode 脱敏 (§14)。
      </p>

      <form onSubmit={add} className="mt-6 bg-white border border-zinc-200 rounded-lg p-4 flex gap-3 items-end">
        <label>
          <div className="text-xs text-zinc-600">服务</div>
          <input
            type="text"
            value={service}
            onChange={(e) => setService(e.target.value)}
            placeholder="ZBW_HR_SRV"
            className="px-3 py-1.5 border border-zinc-300 rounded-md text-sm w-48 font-mono"
          />
        </label>
        <label>
          <div className="text-xs text-zinc-600">字段</div>
          <input
            type="text"
            value={field}
            onChange={(e) => setField(e.target.value)}
            placeholder="SALARY_BASE"
            className="px-3 py-1.5 border border-zinc-300 rounded-md text-sm w-48 font-mono"
          />
        </label>
        <label>
          <div className="text-xs text-zinc-600">脱敏模式</div>
          <select
            value={mask}
            onChange={(e) => setMask(e.target.value as SensitiveField["mask_mode"])}
            className="px-3 py-1.5 border border-zinc-300 rounded-md text-sm w-32"
          >
            <option value="redact">redact (***)</option>
            <option value="partial">partial</option>
            <option value="hash">hash</option>
          </select>
        </label>
        <button
          type="submit"
          disabled={busy}
          className="px-4 py-1.5 bg-brand-600 hover:bg-brand-700 text-white rounded-md text-sm"
        >
          {busy ? "..." : "添加 / 更新"}
        </button>
      </form>

      {msg && <div className="mt-3 px-3 py-2 bg-zinc-100 rounded text-sm">{msg}</div>}

      <div className="mt-6 bg-white border border-zinc-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-zinc-100 text-zinc-700">
            <tr>
              <th className="px-3 py-2 text-left">服务</th>
              <th className="px-3 py-2 text-left">字段</th>
              <th className="px-3 py-2 text-left">脱敏模式</th>
              <th className="px-3 py-2 text-left">添加人</th>
              <th className="px-3 py-2 text-left">添加时间</th>
              <th className="px-3 py-2 text-center">操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr><td colSpan={6} className="px-3 py-6 text-center text-zinc-400">尚未配置敏感字段</td></tr>
            ) : rows.map((r) => (
              <tr key={`${r.service}|${r.field}`} className="border-t border-zinc-100 hover:bg-zinc-50">
                <td className="px-3 py-2 font-mono">{r.service}</td>
                <td className="px-3 py-2 font-mono">{r.field}</td>
                <td className="px-3 py-2">{r.mask_mode}</td>
                <td className="px-3 py-2">{r.added_by}</td>
                <td className="px-3 py-2 font-mono text-xs">{r.created_at}</td>
                <td className="px-3 py-2 text-center">
                  <button
                    onClick={() => remove(r.service, r.field)}
                    className="text-red-600 hover:underline text-xs"
                  >
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
