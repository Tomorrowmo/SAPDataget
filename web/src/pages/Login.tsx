import { useState } from "react";
import { useNavigate, Navigate } from "react-router-dom";
import { useAuth } from "../auth";

export default function Login() {
  const { identity, status, login } = useAuth();
  const nav = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (identity) return <Navigate to="/" replace />;

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await login(username || "demo", password || "demo");
      nav("/");
    } catch (e2) {
      setErr(e2 instanceof Error ? e2.message : String(e2));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-zinc-100">
      <form
        onSubmit={submit}
        className="w-[400px] bg-white rounded-xl shadow-lg border border-zinc-200 p-8"
      >
        <div className="flex flex-col items-center mb-6">
          <div className="w-14 h-14 rounded-2xl bg-brand-600 flex items-center justify-center text-white font-bold text-xl">
            BW
          </div>
          <h1 className="mt-3 text-xl font-semibold text-zinc-900">BW 智能取数</h1>
          <p className="mt-1 text-sm text-zinc-500">
            {status?.bw_mode === "mock"
              ? "MOCK 模式 — 用户名密码可任意填(例如 demo / admin)"
              : "请使用您的 SAP BW 账号登录"}
          </p>
        </div>

        <label className="block">
          <span className="text-sm text-zinc-700">BW 用户名</span>
          <input
            type="text"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder={status?.bw_mode === "mock" ? "demo (admin 可获管理员权限)" : "ZUSER01"}
            autoFocus
            className="mt-1 w-full px-3 py-2 border border-zinc-300 rounded-md focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
          />
        </label>
        <label className="block mt-4">
          <span className="text-sm text-zinc-700">密码</span>
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={status?.bw_mode === "mock" ? "(mock 模式可空)" : ""}
            className="mt-1 w-full px-3 py-2 border border-zinc-300 rounded-md focus:outline-none focus:ring-2 focus:ring-brand-500 focus:border-transparent"
          />
        </label>

        {err && (
          <div className="mt-4 px-3 py-2 rounded-md bg-red-50 border border-red-200 text-sm text-red-700">
            {err}
          </div>
        )}

        <button
          type="submit"
          disabled={busy}
          className="mt-6 w-full px-4 py-2.5 bg-brand-600 hover:bg-brand-700 text-white rounded-md font-medium disabled:opacity-50"
        >
          {busy ? "登录中..." : "登录"}
        </button>

        <div className="mt-4 text-xs text-zinc-500 text-center">
          {status && (
            <>BW: {status.bw_mode} · {status.skills_count} 个模板 · LLM: {status.llm.current_display}</>
          )}
        </div>
      </form>
    </div>
  );
}
