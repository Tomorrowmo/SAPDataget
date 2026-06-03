// 我的 LLM API Keys —— 每用户独立配置
import { useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";

interface ProviderKeyMeta {
  env_var: string;
  provider: string;
  models: string[];
  configured: boolean;
  source: "user" | "env" | null;
  tail: string | null;
  updated_at: string | null;
  has_personal: boolean;
  has_env_fallback: boolean;
}

interface TestResult {
  ok: boolean;
  model?: string;
  key_source?: string;
  latency_ms?: number;
  reply?: string;
  error?: string;
  category?: string;
}

export default function MyLlmKeys() {
  const { identity, refreshStatus } = useAuth();
  const [rows, setRows] = useState<ProviderKeyMeta[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [keyInput, setKeyInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, TestResult>>({});

  const load = async () => {
    const { providers } = await api.listLlmKeys();
    setRows(providers);
  };

  useEffect(() => { load(); }, []);

  const save = async (envVar: string) => {
    if (!keyInput.trim()) return;
    setBusy(true);
    setMsg(null);
    try {
      const r = await api.setLlmKey(envVar, keyInput.trim());
      setMsg(`✅ ${envVar} 已保存(末 4 位 …${r.tail})。建议点「🧪 测试」立即验证。`);
      setEditing(null);
      setKeyInput("");
      await load();
      await refreshStatus();
    } catch (e) {
      setMsg(`❌ ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };

  const remove = async (envVar: string) => {
    if (!confirm(`确认清除你自己的 ${envVar}?\n(系统级 .env fallback 不受影响)`)) return;
    await api.deleteLlmKey(envVar);
    setTestResults((t) => { const { [envVar]: _, ...rest } = t; return rest; });
    await load();
    await refreshStatus();
  };

  const test = async (envVar: string) => {
    setTesting(envVar);
    try {
      const r = await api.testLlmKey(envVar);
      setTestResults((t) => ({ ...t, [envVar]: r }));
    } catch (e) {
      setTestResults((t) => ({
        ...t,
        [envVar]: { ok: false, error: e instanceof Error ? e.message : String(e) },
      }));
    } finally {
      setTesting(null);
    }
  };

  return (
    <div className="max-w-5xl">
      <h1 className="text-2xl font-semibold text-zinc-900">🔑 我的 API Keys</h1>
      <p className="text-sm text-zinc-500 mt-1">
        每个用户的 key 各自隔离 —— 你在这里配的 key 只对 <strong>{identity?.username}</strong> 生效,
        其他用户用他们自己的。系统 <code>.env</code> 中的全局 key 作为兜底 fallback。
      </p>

      <div className="mt-4 bg-amber-50 border border-amber-200 rounded-md px-4 py-3 text-sm text-amber-800">
        <strong>安全说明：</strong> key 经 base64 编码存到 SQLite (<code>app.sqlite3</code>),
        DB 文件由 OS FS 权限保护,API 永不返回真值（只回末 4 位）。二期升级为 AES-256-GCM。
      </div>

      {msg && (
        <div className="mt-4 px-3 py-2 bg-zinc-100 border border-zinc-200 rounded text-sm">
          {msg}
        </div>
      )}

      <div className="mt-6 bg-white border border-zinc-200 rounded-lg overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-zinc-100 text-zinc-700">
            <tr>
              <th className="px-3 py-2 text-left font-medium">env_var</th>
              <th className="px-3 py-2 text-left font-medium">Provider</th>
              <th className="px-3 py-2 text-left font-medium">关联模型</th>
              <th className="px-3 py-2 text-left font-medium">状态</th>
              <th className="px-3 py-2 text-left font-medium">来源</th>
              <th className="px-3 py-2 text-left font-medium">末 4 位</th>
              <th className="px-3 py-2 text-left font-medium">更新时间</th>
              <th className="px-3 py-2 text-center font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <>
                <tr key={p.env_var} className="border-t border-zinc-100 hover:bg-zinc-50">
                  <td className="px-3 py-2 font-mono">{p.env_var}</td>
                  <td className="px-3 py-2">{p.provider}</td>
                  <td className="px-3 py-2 text-xs text-zinc-500">
                    {p.models.map((m) => (
                      <div key={m} className="font-mono">{m}</div>
                    ))}
                  </td>
                  <td className="px-3 py-2">
                    {p.configured ? (
                      <span className="px-2 py-0.5 rounded bg-emerald-100 text-emerald-700 text-xs">
                        ●已配置
                      </span>
                    ) : (
                      <span className="px-2 py-0.5 rounded bg-amber-100 text-amber-700 text-xs">
                        未配置
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {p.source === "user" ? (
                      <span className="text-brand-600">👤 你自己</span>
                    ) : p.source === "env" ? (
                      <span className="text-zinc-500" title="来自 .env 文件,所有用户共用">
                        🌐 系统 fallback
                      </span>
                    ) : "—"}
                  </td>
                  <td className="px-3 py-2 font-mono text-xs text-zinc-500">
                    {p.tail ? `…${p.tail}` : "—"}
                  </td>
                  <td className="px-3 py-2 text-xs text-zinc-500">
                    {p.updated_at || "—"}
                  </td>
                  <td className="px-3 py-2 text-center">
                    <div className="flex justify-center gap-3">
                      <button
                        onClick={() => { setEditing(p.env_var); setKeyInput(""); }}
                        className="text-brand-600 hover:underline text-xs"
                      >
                        {p.has_personal ? "修改我的" : "配置我的"}
                      </button>
                      {p.configured && (
                        <button
                          onClick={() => test(p.env_var)}
                          disabled={testing === p.env_var}
                          className="text-emerald-600 hover:underline text-xs disabled:opacity-50"
                          title="发一次真实请求验证 key 有效"
                        >
                          {testing === p.env_var ? "测试中…" : "🧪 测试"}
                        </button>
                      )}
                      {p.has_personal && (
                        <button
                          onClick={() => remove(p.env_var)}
                          className="text-red-600 hover:underline text-xs"
                        >
                          清除
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
                {editing === p.env_var && (
                  <tr className="bg-brand-50">
                    <td colSpan={8} className="px-3 py-3">
                      <div className="flex gap-2 items-center">
                        <span className="text-xs text-zinc-600 font-mono whitespace-nowrap">
                          {p.env_var}=
                        </span>
                        <input
                          type="password"
                          value={keyInput}
                          onChange={(e) => setKeyInput(e.target.value)}
                          placeholder={`粘贴你的 ${p.provider} API key`}
                          autoFocus
                          className="flex-1 px-3 py-1.5 border border-zinc-300 rounded text-sm font-mono"
                          onKeyDown={(e) => {
                            if (e.key === "Enter") save(p.env_var);
                            if (e.key === "Escape") { setEditing(null); setKeyInput(""); }
                          }}
                        />
                        <button
                          onClick={() => save(p.env_var)}
                          disabled={busy || !keyInput.trim()}
                          className="px-4 py-1.5 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded disabled:opacity-50"
                        >
                          {busy ? "..." : "保存"}
                        </button>
                        <button
                          onClick={() => { setEditing(null); setKeyInput(""); }}
                          className="px-4 py-1.5 border border-zinc-300 text-sm rounded"
                        >
                          取消
                        </button>
                      </div>
                    </td>
                  </tr>
                )}
                {testResults[p.env_var] && (
                  <tr className={testResults[p.env_var].ok ? "bg-emerald-50" : "bg-red-50"}>
                    <td colSpan={8} className="px-3 py-2 text-sm">
                      {testResults[p.env_var].ok ? (
                        <div className="text-emerald-800">
                          ✅ key 有效 ·
                          模型 <code className="font-mono">{testResults[p.env_var].model}</code> ·
                          耗时 {testResults[p.env_var].latency_ms}ms ·
                          回复 <span className="font-mono">{testResults[p.env_var].reply || "(空)"}</span>
                        </div>
                      ) : (
                        <div className="text-red-800">
                          <div className="font-medium">
                            ❌ 测试失败 ({testResults[p.env_var].category})
                          </div>
                          <div className="text-xs mt-1 font-mono whitespace-pre-wrap">
                            {testResults[p.env_var].error}
                          </div>
                          {testResults[p.env_var].category === "auth" && (
                            <div className="text-xs mt-2 text-red-700 bg-white p-2 rounded border border-red-200">
                              💡 <strong>key 被 provider 拒绝</strong> —— 这不是系统 bug,而是 key 本身无效。请：
                              <ul className="list-disc pl-5 mt-1">
                                <li>确认你从对应的官方控制台复制的 key（千问需要从 dashscope.console.aliyun.com 申请）</li>
                                <li>检查没有多余空格、换行</li>
                                <li>确认账户没欠费 / 没被禁用</li>
                              </ul>
                            </div>
                          )}
                          {testResults[p.env_var].category === "network" && (
                            <div className="text-xs mt-2 text-red-700 bg-white p-2 rounded border border-red-200">
                              💡 <strong>网络不通</strong>。如果公司有防火墙：
                              <ul className="list-disc pl-5 mt-1">
                                <li>千问 / DeepSeek 在国内,通常公司网就能直连</li>
                                <li>Claude / GPT 需要公网,可能要走代理</li>
                              </ul>
                            </div>
                          )}
                          {testResults[p.env_var].category === "not_configured" && (
                            <div className="text-xs mt-1 text-red-600">
                              💡 先点上方的「配置我的」保存 key。
                            </div>
                          )}
                        </div>
                      )}
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-6 text-sm text-zinc-500">
        <strong>各 provider 申请 key 的入口：</strong>
        <ul className="mt-2 space-y-1 list-disc pl-5">
          <li>
            <strong>通义千问 (Dashscope)</strong> ➜
            <a href="https://dashscope.console.aliyun.com/apiKey" target="_blank" rel="noreferrer" className="text-brand-600 hover:underline ml-1">
              dashscope.console.aliyun.com/apiKey
            </a>
            <span className="text-zinc-400 ml-2">— 阿里云账号登录后申请,免费有额度</span>
          </li>
          <li>
            <strong>DeepSeek</strong> ➜
            <a href="https://platform.deepseek.com/api_keys" target="_blank" rel="noreferrer" className="text-brand-600 hover:underline ml-1">
              platform.deepseek.com/api_keys
            </a>
            <span className="text-zinc-400 ml-2">— 国内直连最便宜</span>
          </li>
          <li>
            <strong>Claude</strong> ➜
            <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer" className="text-brand-600 hover:underline ml-1">
              console.anthropic.com
            </a>
            <span className="text-zinc-400 ml-2">— 需公网</span>
          </li>
          <li>
            <strong>OpenAI</strong> ➜
            <a href="https://platform.openai.com/api-keys" target="_blank" rel="noreferrer" className="text-brand-600 hover:underline ml-1">
              platform.openai.com
            </a>
            <span className="text-zinc-400 ml-2">— 需公网</span>
          </li>
        </ul>
      </div>
    </div>
  );
}
