// 模板画廊 —— 首页 (P2-17: 收藏 / 状态标记 / 自由对话入口)
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import type { SkillSummary } from "../types";

export default function Home() {
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"all" | "favorites">("all");

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

  const toggleFav = async (e: React.MouseEvent, s: SkillSummary) => {
    e.preventDefault(); e.stopPropagation();
    try {
      if (s.favorite) await api.removeFavorite("skill", s.id);
      else await api.addFavorite("skill", s.id);
      setSkills(skills.map((x) => x.id === s.id ? { ...x, favorite: !x.favorite } : x));
    } catch (err) {
      alert("收藏失败: " + (err instanceof Error ? err.message : err));
    }
  };

  const display = tab === "favorites" ? skills.filter((s) => s.favorite) : skills;

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

      {/* tab */}
      <div className="mb-4 flex gap-1 border-b border-zinc-200">
        {([
          { id: "all", label: "全部模板", count: skills.length },
          { id: "favorites", label: "★ 我的收藏", count: skills.filter((s) => s.favorite).length },
        ] as const).map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-4 py-2 text-sm border-b-2 -mb-px transition ${
              tab === t.id
                ? "border-brand-600 text-brand-700 font-medium"
                : "border-transparent text-zinc-500 hover:text-zinc-700"
            }`}
          >
            {t.label} <span className="text-zinc-400">({t.count})</span>
          </button>
        ))}
        <div className="ml-auto pb-2">
          <Link
            to="/chat"
            className="text-sm px-3 py-1.5 rounded-md text-brand-600 hover:bg-brand-50"
          >
            💬 自由对话 →
          </Link>
        </div>
      </div>

      {loading ? (
        <div className="text-zinc-500">加载中...</div>
      ) : display.length === 0 ? (
        <div className="text-zinc-500">
          {tab === "favorites" ? "还没有收藏的模板,在卡片上点 ☆ 添加。" : "未找到匹配的模板"}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {display.map((s) => (
            <Link
              key={s.id}
              to={`/skills/${encodeURIComponent(s.id)}`}
              className={`block bg-white rounded-lg border p-5 hover:border-brand-500 hover:shadow-md transition ${
                s.status === "deprecated" ? "border-zinc-300 opacity-75" :
                s.status === "draft" ? "border-amber-300" : "border-zinc-200"
              }`}
            >
              <div className="flex items-start justify-between mb-2">
                <div className="text-3xl">📊</div>
                <div className="flex items-center gap-2">
                  {s.status && s.status !== "active" && (
                    <span className={`text-xs px-1.5 py-0.5 rounded ${
                      s.status === "draft" ? "bg-amber-100 text-amber-700" :
                      s.status === "deprecated" ? "bg-zinc-200 text-zinc-600" : ""
                    }`}>{s.status}</span>
                  )}
                  <button
                    onClick={(e) => toggleFav(e, s)}
                    title={s.favorite ? "取消收藏" : "收藏"}
                    className={`text-lg ${s.favorite ? "text-yellow-500" : "text-zinc-300 hover:text-yellow-500"}`}
                  >
                    {s.favorite ? "★" : "☆"}
                  </button>
                </div>
              </div>
              <div className="font-semibold text-zinc-900 text-lg">{s.title}</div>
              <div className="mt-1 text-xs text-zinc-400 font-mono">{s.id}</div>
              <div className="mt-3 text-sm text-zinc-600" style={{
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
