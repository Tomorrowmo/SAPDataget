// 顶栏 + 侧栏导航
import { ReactNode } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import ModelSelector from "./ModelSelector";

export default function Layout({ children }: { children: ReactNode }) {
  const { identity, status, logout } = useAuth();
  const nav = useNavigate();

  const handleLogout = async () => {
    await logout();
    nav("/login");
  };

  const navItems = [
    { to: "/", label: "📊 模板画廊", end: true },
    { to: "/chat", label: "💬 自由对话" },
    { to: "/tasks", label: "📁 我的任务" },
    { to: "/llm-keys", label: "🔑 LLM 设置" },
  ];
  const adminItems = [
    { to: "/admin/skills", label: "🛠 Skill 管理" },
    { to: "/admin/audit", label: "🔍 审计日志" },
    { to: "/admin/sensitive", label: "🔒 敏感字段" },
    { to: "/admin/quota", label: "📊 LLM 配额" },
  ];

  return (
    <div className="flex flex-col h-full">
      {/* 顶栏 */}
      <header className="flex items-center justify-between px-6 py-3 bg-white border-b border-zinc-200 shadow-sm">
        <div className="flex items-center gap-4">
          <Link to="/" className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-brand-600 flex items-center justify-center text-white font-bold">
              BW
            </div>
            <div>
              <div className="font-semibold text-zinc-900 leading-tight">智能取数</div>
              <div className="text-xs text-zinc-500 leading-tight">SAP BW 7.5 自然语言查询</div>
            </div>
          </Link>
          {status && (
            <div className="ml-4 flex items-center gap-2 text-xs">
              {status.bw_mode === "mock" ? (
                <span className="px-2 py-0.5 rounded bg-amber-100 text-amber-700 font-medium">
                  ⚠ MOCK 数据
                </span>
              ) : (
                <span className="px-2 py-0.5 rounded bg-emerald-100 text-emerald-700 font-medium">
                  ● LIVE
                </span>
              )}
              <span className="text-zinc-500">{status.skills_count} 个模板</span>
            </div>
          )}
        </div>

        <div className="flex items-center gap-3">
          <ModelSelector />
          {identity ? (
            <div className="flex items-center gap-3 pl-3 border-l border-zinc-200">
              <div className="text-right">
                <div className="text-sm font-medium text-zinc-800">{identity.display_name}</div>
                <div className="text-xs text-zinc-500">
                  {identity.role === "admin" ? "管理员" : "业务用户"}
                </div>
              </div>
              <button
                onClick={handleLogout}
                className="text-sm text-zinc-500 hover:text-red-600"
              >
                退出
              </button>
            </div>
          ) : (
            <Link to="/login" className="text-sm text-brand-600 hover:underline">
              登录
            </Link>
          )}
        </div>
      </header>

      {/* 主体：左侧导航 + 内容区 */}
      <div className="flex-1 flex overflow-hidden">
        <aside className="w-56 bg-white border-r border-zinc-200 flex flex-col">
          <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
            {navItems.map((it) => (
              <NavLink
                key={it.to}
                to={it.to}
                end={it.end}
                className={({ isActive }) =>
                  `block px-3 py-2 rounded-md text-sm transition ${
                    isActive
                      ? "bg-brand-50 text-brand-700 font-medium"
                      : "text-zinc-700 hover:bg-zinc-50"
                  }`
                }
              >
                {it.label}
              </NavLink>
            ))}
            {identity?.role === "admin" && (
              <>
                <div className="mt-4 px-3 text-xs text-zinc-400 font-medium">管理</div>
                {adminItems.map((it) => (
                  <NavLink
                    key={it.to}
                    to={it.to}
                    className={({ isActive }) =>
                      `block px-3 py-2 rounded-md text-sm transition ${
                        isActive
                          ? "bg-brand-50 text-brand-700 font-medium"
                          : "text-zinc-700 hover:bg-zinc-50"
                      }`
                    }
                  >
                    {it.label}
                  </NavLink>
                ))}
              </>
            )}
          </nav>
          <div className="p-3 border-t border-zinc-100 text-xs text-zinc-400">
            v0.2.0
          </div>
        </aside>

        <main className="flex-1 overflow-y-auto p-6 bg-zinc-50">{children}</main>
      </div>
    </div>
  );
}
