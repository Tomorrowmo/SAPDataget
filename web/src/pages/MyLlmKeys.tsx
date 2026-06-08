// 🔑 LLM 设置 —— 完全参照 DataAgent SettingsView：
// provider 预设(DeepSeek/Qwen/OpenAI/Custom)一键填好 Base URL + Model,
// 用户通常只需粘贴 API key。Model 可下拉选,也可自定义;Base URL 有默认值。
import { useEffect, useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import type { LlmSettings, LlmTestResult } from "../types";

type PresetId = "deepseek" | "qwen" | "openai" | "custom";

interface Preset {
  id: PresetId;
  label: string;
  base_url: string;
  default_model: string;
  model_options: string[];
  key_hint: string;
  docs: string;
}

const PRESETS: Preset[] = [
  {
    id: "deepseek",
    label: "DeepSeek",
    base_url: "https://api.deepseek.com/v1",
    default_model: "deepseek-chat",
    model_options: ["deepseek-chat", "deepseek-reasoner"],
    key_hint: "sk-...",
    docs: "https://platform.deepseek.com/api_keys",
  },
  {
    id: "qwen",
    label: "通义千问 (DashScope)",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    default_model: "qwen-plus",
    model_options: ["qwen-plus", "qwen-max", "qwen-turbo", "qwen3-coder-plus"],
    key_hint: "sk-...",
    docs: "https://dashscope.console.aliyun.com/apiKey",
  },
  {
    id: "openai",
    label: "OpenAI",
    base_url: "https://api.openai.com/v1",
    default_model: "gpt-4o-mini",
    model_options: ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini", "gpt-4.1"],
    key_hint: "sk-...",
    docs: "https://platform.openai.com/api-keys",
  },
  {
    id: "custom",
    label: "自定义 (任意 OpenAI 兼容端点)",
    base_url: "",
    default_model: "",
    model_options: [],
    key_hint: "Bearer token",
    docs: "",
  },
];

function presetFor(base_url: string): PresetId {
  if (!base_url) return "deepseek";
  const hit = PRESETS.find((p) => p.id !== "custom" && p.base_url === base_url);
  return hit?.id ?? "custom";
}

export default function MyLlmKeys() {
  const { identity, refreshStatus } = useAuth();
  const [s, setS] = useState<LlmSettings | null>(null);
  const [presetId, setPresetId] = useState<PresetId>("deepseek");
  const [baseUrl, setBaseUrl] = useState("");
  const [model, setModel] = useState("");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const [test, setTest] = useState<LlmTestResult | null>(null);
  const [testing, setTesting] = useState(false);

  const preset = PRESETS.find((p) => p.id === presetId)!;

  useEffect(() => {
    api.getLlmSettings().then((data) => {
      setS(data);
      const detected = presetFor(data.base_url || "");
      setPresetId(detected);
      if (data.base_url || data.model) {
        // 用户已存过 → 用其值
        setBaseUrl(data.base_url);
        setModel(data.model);
      } else {
        // 全新用户 → 预填 DeepSeek 预设(用户只需粘贴 key)
        const p = PRESETS.find((x) => x.id === detected)!;
        setBaseUrl(p.base_url);
        setModel(p.default_model);
      }
    }).catch(() => {});
  }, []);

  function pickPreset(id: PresetId) {
    setPresetId(id);
    setSaved(false);
    setError("");
    const p = PRESETS.find((x) => x.id === id)!;
    if (id !== "custom") {
      setBaseUrl(p.base_url);
      if (!model || !p.model_options.includes(model)) setModel(p.default_model);
    } else {
      setBaseUrl("");
      setModel("");
    }
  }

  async function save(clearKey = false) {
    setBusy(true); setError(""); setSaved(false); setTest(null);
    try {
      const data = await api.saveLlmSettings({
        api_key: clearKey ? "" : (key.trim() ? key.trim() : null),
        base_url: baseUrl.trim(),
        model: model.trim(),
      });
      setS(data);
      setKey("");
      setSaved(true);
      await refreshStatus();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function runTest() {
    setTesting(true); setTest(null);
    try {
      setTest(await api.testLlmSettings());
    } catch (e) {
      setTest({ ok: false, error: e instanceof Error ? e.message : String(e), category: "other" });
    } finally {
      setTesting(false);
    }
  }

  if (!s) return <div className="text-sm text-zinc-500 p-6">加载中…</div>;

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-semibold text-zinc-900">🔑 LLM 设置</h1>
      <p className="text-sm text-zinc-500 mt-1">
        选一个服务商,通常只需粘贴你的 API Key 即可（Base URL 与 Model 已自动填好,可改)。
        配置只对 <strong>{identity?.username}</strong> 生效,留空回退系统 <code>.env</code> 默认。
      </p>

      {/* 当前生效状态 */}
      <div className={`mt-4 px-4 py-2.5 rounded-lg border text-sm ${
        s.effective_ready ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                          : "bg-amber-50 border-amber-200 text-amber-800"
      }`}>
        {s.effective_ready ? "● 已就绪" : "○ 未就绪"} · 当前生效模型{" "}
        <code className="font-mono">{s.effective_model || "(未设置)"}</code> · key 来源{" "}
        {s.key_source === "user" ? "👤 你自己" : s.key_source === "env" ? "🌐 系统 .env" : "无"}
      </div>

      <div className="mt-5 bg-white border border-zinc-200 rounded-xl p-5">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-brand-600">🔑</span>
          <h3 className="font-medium text-zinc-800">大语言模型接入</h3>
        </div>
        <p className="text-sm text-zinc-500 mb-4">
          当前你的 key:<strong>{s.has_key ? " 已设置" : (s.env_has_key ? " 未设置(用 .env 兜底)" : " 未设置")}</strong>
        </p>

        {/* provider 预设 */}
        <label className="block text-xs font-medium text-zinc-700 mb-1">服务商</label>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-4">
          {PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => pickPreset(p.id)}
              className={`text-left text-xs px-3 py-2 rounded-lg border transition ${
                presetId === p.id
                  ? "border-brand-500 bg-brand-50 text-brand-700 font-medium"
                  : "border-zinc-200 hover:bg-zinc-50 text-zinc-700"
              }`}
            >
              {p.label}
            </button>
          ))}
        </div>

        {/* Base URL(有默认) */}
        <label className="block text-xs font-medium text-zinc-700 mb-1">Base URL</label>
        <input
          type="text"
          value={baseUrl}
          onChange={(e) => { setBaseUrl(e.target.value); setSaved(false); }}
          placeholder={preset.id === "custom" ? "https://your-endpoint/v1" : preset.base_url}
          className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm font-mono mb-3 focus:outline-none focus:ring-2 focus:ring-brand-500"
        />

        {/* Model(可选 也可自定义) */}
        <label className="block text-xs font-medium text-zinc-700 mb-1">Model</label>
        {preset.model_options.length > 0 ? (
          <div className="flex gap-2 items-center mb-3">
            <select
              value={preset.model_options.includes(model) ? model : "__custom"}
              onChange={(e) => { if (e.target.value !== "__custom") setModel(e.target.value); setSaved(false); }}
              className="border border-zinc-300 rounded-lg px-2 py-2 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-brand-500"
            >
              {preset.model_options.map((m) => <option key={m} value={m}>{m}</option>)}
              <option value="__custom">自定义…</option>
            </select>
            <input
              type="text"
              value={model}
              onChange={(e) => { setModel(e.target.value); setSaved(false); }}
              placeholder="model id"
              className="flex-1 border border-zinc-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
          </div>
        ) : (
          <input
            type="text"
            value={model}
            onChange={(e) => { setModel(e.target.value); setSaved(false); }}
            placeholder="e.g. qwen2.5:72b（本地 Ollama）"
            className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm font-mono mb-3 focus:outline-none focus:ring-2 focus:ring-brand-500"
          />
        )}

        {/* API Key(主输入) */}
        <label className="block text-xs font-medium text-zinc-700 mb-1">API Key</label>
        <input
          type="password"
          value={key}
          onChange={(e) => { setKey(e.target.value); setSaved(false); }}
          placeholder={s.has_key ? "（已设置，留空=保持不变）" : preset.key_hint}
          className="w-full border border-zinc-300 rounded-lg px-3 py-2 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-brand-500"
        />
        <div className="text-xs text-zinc-500 mt-2">
          {preset.docs && (
            <>申请 key:<a className="text-brand-600 hover:underline" href={preset.docs} target="_blank" rel="noreferrer">{preset.docs}</a>。</>
          )}
          {" "}key 经 base64 暂存于本地 SQLite，API 永不回读真值。
          {s.has_key && (
            <button onClick={() => save(true)} disabled={busy} className="ml-2 text-red-600 hover:underline">清除我的 key</button>
          )}
        </div>

        <div className="flex gap-2 mt-4">
          <button
            onClick={() => save(false)}
            disabled={busy}
            className={`px-5 py-2 rounded-lg font-medium text-white disabled:opacity-50 ${
              saved ? "bg-emerald-600" : "bg-brand-600 hover:bg-brand-700"
            }`}
          >
            {busy ? "保存中…" : saved ? "✓ 已保存" : "保存"}
          </button>
          <button
            onClick={runTest}
            disabled={testing || !s.effective_ready}
            title={s.effective_ready ? "用当前生效配置发一次真实请求" : "先保存好配置再测试"}
            className="px-5 py-2 border border-emerald-300 text-emerald-700 hover:bg-emerald-50 rounded-lg font-medium disabled:opacity-50"
          >
            {testing ? "测试中…" : "🧪 测试"}
          </button>
        </div>

        {error && <div className="mt-3 text-sm text-red-600">⚠ {error}</div>}

        {test && (
          <div className={`mt-3 px-3 py-2 rounded text-sm ${test.ok ? "bg-emerald-50 text-emerald-800" : "bg-red-50 text-red-800"}`}>
            {test.ok ? (
              <>✅ key 有效 · 模型 <code className="font-mono">{test.model}</code> · 耗时 {test.latency_ms}ms · 回复 <span className="font-mono">{test.reply || "(空)"}</span></>
            ) : (
              <>
                <div className="font-medium">❌ 测试失败（{test.category}）</div>
                <div className="text-xs mt-1 font-mono whitespace-pre-wrap">{test.error}</div>
                {test.category === "auth" && (
                  <div className="text-xs mt-2 bg-white p-2 rounded border border-red-200">
                    💡 key 被服务商拒绝（不是系统 bug）：确认从官方控制台复制、无多余空格、账户没欠费。
                  </div>
                )}
                {test.category === "network" && (
                  <div className="text-xs mt-2 bg-white p-2 rounded border border-red-200">
                    💡 网络不通：千问 / DeepSeek 国内多可直连；OpenAI 需公网（可能要走代理）。
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
