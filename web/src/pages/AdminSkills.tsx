// 管理员 — Skill 管理：列表 + 详情查看 + 新建/编辑/删除 + reload
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { SkillSummary, SkillDetail } from "../types";

export default function AdminSkills() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const load = async () => {
    const { skills } = await api.listSkills();
    setSkills(skills);
  };

  useEffect(() => { load(); }, []);

  const reload = async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.reloadSkills();
      setMsg(`✅ 已重新加载 ${r.loaded} 个 Skill`);
      await load();
    } catch (e) {
      setMsg(`❌ ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="max-w-6xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-zinc-900">🛠 Skill 管理</h1>
          <p className="text-sm text-zinc-500 mt-1">
            Skill 文件放在 <code>data/skills/&lt;id&gt;/</code> 下;支持网页编辑、上传模板、试运行。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            to="/admin/skills/_new"
            className="px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-md"
          >
            + 新建 Skill
          </Link>
          <button
            onClick={reload}
            disabled={busy}
            className="px-4 py-2 border border-zinc-300 hover:bg-zinc-50 text-sm rounded-md disabled:opacity-50"
          >
            {busy ? "..." : "🔄 重新加载"}
          </button>
        </div>
      </div>

      {msg && (
        <div className="mt-4 px-3 py-2 rounded-md bg-zinc-100 text-sm">{msg}</div>
      )}

      <div className="mt-6 grid grid-cols-12 gap-6">
        <div className="col-span-5">
          <div className="bg-white border border-zinc-200 rounded-lg divide-y divide-zinc-100">
            {skills.map((s) => (
              <div key={s.id} className={`p-4 hover:bg-zinc-50 transition ${detail?.id === s.id ? "bg-brand-50" : ""}`}>
                <button
                  onClick={() => api.getSkill(s.id).then(setDetail)}
                  className="w-full text-left"
                >
                  <div className="flex items-center justify-between">
                    <div className="font-medium text-zinc-900">{s.title}</div>
                    {s.status && s.status !== "active" && (
                      <span className={`text-xs px-1.5 py-0.5 rounded ${
                        s.status === "draft" ? "bg-amber-100 text-amber-700" :
                        s.status === "deprecated" ? "bg-zinc-200 text-zinc-600" :
                        "bg-red-100 text-red-700"
                      }`}>{s.status}</span>
                    )}
                  </div>
                  <div className="text-xs text-zinc-400 font-mono">{s.id}</div>
                  <div className="text-xs text-zinc-500 mt-1">{s.params.length} 参数</div>
                </button>
                <div className="mt-2 flex gap-2">
                  <Link
                    to={`/admin/skills/${s.id}`}
                    className="text-xs text-brand-600 hover:underline"
                  >
                    ✏️ 编辑/试运行
                  </Link>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="col-span-7">
          {detail ? (
            <div className="bg-white border border-zinc-200 rounded-lg p-5 text-sm">
              <div className="font-semibold text-zinc-900 text-lg">{detail.title}</div>
              <div className="text-xs text-zinc-400 font-mono">{detail.id}</div>
              <p className="mt-3 text-zinc-700">{detail.description}</p>

              <div className="mt-4">
                <div className="text-xs text-zinc-500 font-medium mb-1">数据源</div>
                <div className="font-mono text-xs bg-zinc-50 p-2 rounded">
                  {detail.service} / {detail.entity_set}
                </div>
              </div>

              <div className="mt-4">
                <div className="text-xs text-zinc-500 font-medium mb-1">$filter 模板</div>
                <pre className="font-mono text-xs bg-zinc-50 p-2 rounded whitespace-pre-wrap">{detail.filter_template}</pre>
              </div>

              <div className="mt-4">
                <div className="text-xs text-zinc-500 font-medium mb-1">参数</div>
                <table className="w-full text-xs border border-zinc-200 rounded">
                  <thead className="bg-zinc-100">
                    <tr>
                      <th className="px-2 py-1 text-left">名称</th>
                      <th className="px-2 py-1 text-left">必填</th>
                      <th className="px-2 py-1 text-left">默认</th>
                      <th className="px-2 py-1 text-left">说明</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.params.map((p) => (
                      <tr key={p.name} className="border-t border-zinc-100">
                        <td className="px-2 py-1 font-mono">{p.name}</td>
                        <td className="px-2 py-1">{p.required ? "✓" : ""}</td>
                        <td className="px-2 py-1">{p.default !== undefined && p.default !== null ? String(p.default) : ""}</td>
                        <td className="px-2 py-1 text-zinc-600">{p.description}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {detail.instructions && (
                <div className="mt-4">
                  <div className="text-xs text-zinc-500 font-medium mb-1">给 LLM 的指引 (SKILL.md 正文)</div>
                  <pre className="text-xs text-zinc-700 bg-zinc-50 p-3 rounded whitespace-pre-wrap">{detail.instructions}</pre>
                </div>
              )}
            </div>
          ) : (
            <div className="text-zinc-400 text-sm p-6">点击左侧选择 Skill 查看详情</div>
          )}
        </div>
      </div>
    </div>
  );
}
