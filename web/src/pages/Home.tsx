// 模板画廊 —— 首页
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { SkillSummary } from "../types";

export default function Home() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);

  const load = async (kw?: string) => {
    setLoading(true);
    try {
      const { skills } = await api.listSkills(kw);
      setSkills(skills);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    load(q.trim() || undefined);
  };

  return (
    <div className="max-w-6xl">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-semibold text-zinc-900">模板画廊</h1>
          <p className="text-sm text-zinc-500 mt-1">
            选择常见报表模板,填几个参数,30 秒拿到 Excel。或者切到「自由对话」用自然语言提问。
          </p>
        </div>
        <form onSubmit={onSubmit} className="flex gap-2">
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="搜索模板,例如「销售」「良率」"
            className="px-3 py-2 border border-zinc-300 rounded-md text-sm w-64 focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
          <button
            type="submit"
            className="px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-md"
          >
            搜索
          </button>
        </form>
      </div>

      {loading ? (
        <div className="text-zinc-500">加载中...</div>
      ) : skills.length === 0 ? (
        <div className="text-zinc-500">未找到匹配的模板</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {skills.map((s) => (
            <Link
              key={s.id}
              to={`/skills/${encodeURIComponent(s.id)}`}
              className="block bg-white rounded-lg border border-zinc-200 p-5 hover:border-brand-500 hover:shadow-md transition"
            >
              <div className="flex items-start justify-between mb-2">
                <div className="text-3xl">📊</div>
              </div>
              <div className="font-semibold text-zinc-900 text-lg">{s.title}</div>
              <div className="mt-1 text-xs text-zinc-400 font-mono">{s.id}</div>
              <div className="mt-3 text-sm text-zinc-600 line-clamp-3" style={{
                display: "-webkit-box",
                WebkitLineClamp: 3,
                WebkitBoxOrient: "vertical",
                overflow: "hidden",
              }}>
                {s.description}
              </div>
              {s.keywords && s.keywords.length > 0 && (
                <div className="mt-3 flex flex-wrap gap-1">
                  {s.keywords.slice(0, 5).map((k) => (
                    <span key={k} className="text-xs px-2 py-0.5 rounded bg-zinc-100 text-zinc-600">
                      {k}
                    </span>
                  ))}
                </div>
              )}
              <div className="mt-4 text-xs text-zinc-500">
                {s.params.length} 个参数 · 点击打开
              </div>
            </Link>
          ))}
        </div>
      )}

      <div className="mt-8 p-4 bg-brand-50 border border-brand-200 rounded-lg">
        <div className="font-medium text-brand-900">没有合适的模板?</div>
        <div className="mt-1 text-sm text-brand-700">
          切到「自由对话」用大白话提问,智能体会自己找数据源、拼查询、出 Excel。
        </div>
        <Link
          to="/chat"
          className="mt-3 inline-block px-3 py-1.5 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-md"
        >
          打开自由对话 →
        </Link>
      </div>
    </div>
  );
}
