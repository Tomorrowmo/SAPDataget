// 模型选择下拉框 —— 顶栏关键 UI,带 API key 配置
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { LlmStatus, ModelInfo } from "../types";
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

export default function ModelSelector() {
  const { status, refreshStatus } = useAuth();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [llm, setLlm] = useState<LlmStatus | null>(status?.llm ?? null);
  const [keyMeta, setKeyMeta] = useState<Record<string, ProviderKeyMeta>>({});
  const [editing, setEditing] = useState<string | null>(null); // env_var being edited
  const [keyInput, setKeyInput] = useState("");
  const [testing, setTesting] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; latency_ms?: number; error?: string; category?: string }>>({});
  const popRef = useRef<HTMLDivElement>(null);

  const loadAll = async () => {
    const [models, keys] = await Promise.all([
      api.listModels(),
      api.listLlmKeys().catch(() => ({ providers: [] as ProviderKeyMeta[] })),
    ]);
    setLlm(models);
    const map: Record<string, ProviderKeyMeta> = {};
    keys.providers.forEach((p) => { map[p.env_var] = p; });
    setKeyMeta(map);
  };

  useEffect(() => {
    if (status?.llm) setLlm(status.llm);
  }, [status]);

  useEffect(() => {
    if (!open) return;
    loadAll().catch(console.error);
    const onClick = (e: MouseEvent) => {
      if (popRef.current && !popRef.current.contains(e.target as Node)) {
        setOpen(false);
        setEditing(null);
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const select = async (m: ModelInfo) => {
    if (busy) return;
    setBusy(true);
    try {
      const next = await api.switchModel(m.id);
      setLlm(next);
      await refreshStatus();
      setOpen(false);
    } catch (e) {
      alert(`切换失败: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };

  const startEdit = (envVar: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setEditing(envVar);
    setKeyInput("");
  };

  const saveKey = async (envVar: string) => {
    if (!keyInput.trim()) return;
    setBusy(true);
    try {
      await api.setLlmKey(envVar, keyInput.trim());
      setKeyInput("");
      setEditing(null);
      await loadAll();
      await refreshStatus();
    } catch (e) {
      alert(`保存失败: ${e instanceof Error ? e.message : e}`);
    } finally {
      setBusy(false);
    }
  };

  const removeKey = async (envVar: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm(`确认清除 ${envVar}?`)) return;
    await api.deleteLlmKey(envVar);
    setTestResults((t) => { const { [envVar]: _, ...rest } = t; return rest; });
    await loadAll();
    await refreshStatus();
  };

  const testKey = async (envVar: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setTesting(envVar);
    try {
      const r = await api.testLlmKey(envVar);
      setTestResults((t) => ({ ...t, [envVar]: r }));
    } catch (err) {
      setTestResults((t) => ({
        ...t,
        [envVar]: { ok: false, error: err instanceof Error ? err.message : String(err) },
      }));
    } finally {
      setTesting(null);
    }
  };

  const current = llm?.models.find((m) => m.id === llm.current);

  return (
    <div className="relative" ref={popRef}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 px-3 py-1.5 rounded-md border border-zinc-200 bg-white hover:border-brand-500 hover:bg-brand-50 transition text-sm"
        title="切换 LLM 模型 / 配置 API key"
      >
        <span className="text-zinc-400">🤖</span>
        <span className="font-medium text-zinc-800">
          {current?.display ?? llm?.current ?? "未配置"}
        </span>
        {current && !current.ready && (
          <span className="text-amber-600 text-xs">⚠ 未配置 key</span>
        )}
        <span className="text-zinc-400">▾</span>
      </button>

      {open && llm && (
        <div className="absolute right-0 mt-2 w-[560px] bg-white border border-zinc-200 rounded-lg shadow-xl z-50 max-h-[80vh] overflow-y-auto">
          <div className="px-4 py-2 border-b border-zinc-100 text-xs text-zinc-500 sticky top-0 bg-white flex items-center justify-between">
            <span>选择 LLM 模型 (可运行时切换 · 每人独立 key)</span>
            <a href="/llm-keys" className="text-brand-600 hover:underline">🔑 我的 keys</a>
          </div>
          {llm.models.map((m) => {
            const km = Object.values(keyMeta).find((k) => k.models.includes(m.id));
            return (
              <div
                key={m.id}
                className={`border-b border-zinc-50 last:border-b-0 ${
                  llm.current === m.id ? "bg-brand-50" : ""
                }`}
              >
                <button
                  type="button"
                  onClick={() => select(m)}
                  disabled={busy}
                  className="w-full text-left px-4 py-3 hover:bg-zinc-50 transition disabled:opacity-50"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-medium text-zinc-900">{m.display}</span>
                        {llm.current === m.id && (
                          <span className="text-brand-600 text-xs">● 当前</span>
                        )}
                        {m.ready ? (
                          <span className="text-emerald-600 text-xs">✓ 就绪</span>
                        ) : (
                          <span className="text-amber-600 text-xs">⚠ 缺 key</span>
                        )}
                      </div>
                      <div className="mt-0.5 text-xs text-zinc-500 font-mono">{m.id}</div>
                      <div className="mt-1 text-xs text-zinc-600">{m.notes}</div>
                    </div>
                    <div className="text-right text-xs text-zinc-500 whitespace-nowrap shrink-0">
                      <div>{m.location}</div>
                      <div className="font-mono">{m.cost}</div>
                    </div>
                  </div>
                </button>

                {/* key 配置区 —— 每个用户可改自己的 */}
                {km && (
                  <div className="px-4 py-2 bg-zinc-50 border-t border-zinc-100">
                    <div className="flex items-center justify-between gap-2 text-xs">
                      <div className="flex items-center gap-2 min-w-0 flex-wrap">
                        <span className="font-mono text-zinc-600">{km.env_var}</span>
                        {km.configured ? (
                          <>
                            <span className="text-emerald-700">●已配置</span>
                            {km.tail && (
                              <span className="font-mono text-zinc-500">…{km.tail}</span>
                            )}
                            {km.source === "user" && (
                              <span className="text-brand-600">👤 你的</span>
                            )}
                            {km.source === "env" && (
                              <span className="text-zinc-400">🌐 系统 fallback</span>
                            )}
                          </>
                        ) : (
                          <span className="text-amber-600">未配置</span>
                        )}
                      </div>
                      {editing !== km.env_var && (
                        <div className="flex gap-3 shrink-0">
                          <button
                            onClick={(e) => startEdit(km.env_var, e)}
                            className="text-brand-600 hover:underline"
                          >
                            🔑 {km.has_personal ? "修改" : "配置我的"}
                          </button>
                          {km.configured && (
                            <button
                              onClick={(e) => testKey(km.env_var, e)}
                              disabled={testing === km.env_var}
                              className="text-emerald-600 hover:underline disabled:opacity-50"
                            >
                              {testing === km.env_var ? "测试中…" : "🧪 测试"}
                            </button>
                          )}
                          {km.has_personal && (
                            <button
                              onClick={(e) => removeKey(km.env_var, e)}
                              className="text-red-600 hover:underline"
                            >
                              清除
                            </button>
                          )}
                        </div>
                      )}
                    </div>

                    {testResults[km.env_var] && editing !== km.env_var && (
                      <div className={`mt-2 px-2 py-1 rounded text-xs ${
                        testResults[km.env_var].ok ? "bg-emerald-100 text-emerald-800" : "bg-red-100 text-red-800"
                      }`}>
                        {testResults[km.env_var].ok ? (
                          <>✅ key 有效 · 耗时 {testResults[km.env_var].latency_ms}ms</>
                        ) : (
                          <>❌ {testResults[km.env_var].category}: <span className="font-mono">{testResults[km.env_var].error?.substring(0, 80)}</span></>
                        )}
                      </div>
                    )}

                    {editing === km.env_var && (
                      <div className="mt-2 flex gap-2" onClick={(e) => e.stopPropagation()}>
                        <input
                          type="password"
                          value={keyInput}
                          onChange={(e) => setKeyInput(e.target.value)}
                          placeholder={`粘贴 ${km.env_var} 的值`}
                          autoFocus
                          className="flex-1 px-2 py-1 border border-zinc-300 rounded text-xs font-mono"
                          onKeyDown={(e) => {
                            if (e.key === "Enter") saveKey(km.env_var);
                            if (e.key === "Escape") { setEditing(null); setKeyInput(""); }
                          }}
                        />
                        <button
                          onClick={() => saveKey(km.env_var)}
                          disabled={busy || !keyInput.trim()}
                          className="px-3 py-1 bg-brand-600 hover:bg-brand-700 text-white text-xs rounded disabled:opacity-50"
                        >
                          保存
                        </button>
                        <button
                          onClick={() => { setEditing(null); setKeyInput(""); }}
                          className="px-3 py-1 border border-zinc-300 text-xs rounded"
                        >
                          取消
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
          <div className="px-4 py-2 text-xs text-zinc-500 bg-zinc-50 border-t border-zinc-100">
            💡 你保存的 key 只对你自己生效;系统 .env 中的 key 是兜底 fallback
          </div>
        </div>
      )}
    </div>
  );
}
