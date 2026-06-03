// Skill 在线编辑 (P1-8): 源码编辑 + 模板上传 + chart 配置 + 试运行 + 状态切换
import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import type { SkillSourceResp, SkillDetail } from "../types";
import DataTable from "../components/DataTable";

const DEFAULT_SKILL_MD = `---
id: my_skill_id
title: 新模板标题
owner: BW 团队
version: 1
keywords: [示例]
visible_to: []
params:
  - name: month
    required: true
    description: YYYYMM
  - name: region
    required: true
    description: 大区代码
    enum: [HD, HN, HB, HX, XB, DB]
---

# 新模板

## 适用场景
...

## 给 LLM 的指引
- ...
`;

const DEFAULT_SERVICE_YAML = `service: ZBW_SALES_SRV
entity_set: SalesByOfficeView
filter_template: "Region eq '{{ region }}' and CALMONTH eq '{{ month }}'"
select:
  - OfficeCode
  - NETWR_F
orderby: NETWR_F desc
top: 100
sheet_title: 数据
`;

export default function AdminSkillEdit() {
  const { skill_id = "" } = useParams();
  const isNew = skill_id === "_new";
  const navigate = useNavigate();
  const [newId, setNewId] = useState("");
  const [skillMd, setSkillMd] = useState(isNew ? DEFAULT_SKILL_MD : "");
  const [serviceYaml, setServiceYaml] = useState(isNew ? DEFAULT_SERVICE_YAML : "");
  const [source, setSource] = useState<SkillSourceResp | null>(null);
  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const [chartJson, setChartJson] = useState("");
  const [testParams, setTestParams] = useState<Record<string, string>>({});
  const [testResult, setTestResult] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const load = async () => {
    if (isNew) return;
    setErr("");
    try {
      const src = await api.adminSkillSource(skill_id);
      setSource(src);
      setSkillMd(src.skill_md);
      setServiceYaml(src.service_yaml);
      const det = await api.getSkill(skill_id);
      setSkill(det);
      const init: Record<string, string> = {};
      for (const p of det.params) init[p.name] = p.default != null ? String(p.default) : "";
      setTestParams(init);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  };

  useEffect(() => { void load(); }, [skill_id]);

  const create = async () => {
    if (!/^[a-z][a-z0-9_]{1,40}$/.test(newId)) {
      alert("id 必须 ^[a-z][a-z0-9_]{1,40}$");
      return;
    }
    setBusy(true);
    try {
      await api.adminCreateSkill(newId, skillMd, serviceYaml);
      navigate(`/admin/skills/${newId}`);
    } catch (e) {
      alert("创建失败: " + (e instanceof Error ? e.message : e));
    } finally { setBusy(false); }
  };

  const save = async () => {
    setBusy(true);
    try {
      await api.adminUpdateSkill(skill_id, skillMd, serviceYaml);
      await load();
      alert("已保存");
    } catch (e) {
      alert("保存失败: " + (e instanceof Error ? e.message : e));
    } finally { setBusy(false); }
  };

  const del = async () => {
    if (!confirm(`确认删除 Skill ${skill_id}? (文件夹会被整个删除)`)) return;
    try {
      await api.adminDeleteSkill(skill_id);
      navigate("/admin/skills");
    } catch (e) {
      alert("删除失败: " + (e instanceof Error ? e.message : e));
    }
  };

  const uploadTpl = async () => {
    const f = fileRef.current?.files?.[0];
    if (!f) return;
    try {
      const r = await api.adminUploadTemplate(skill_id, f);
      alert(`✓ 已上传 (${r.size_bytes} bytes)`);
      await load();
    } catch (e) {
      alert("上传失败: " + (e instanceof Error ? e.message : e));
    }
  };

  const delTpl = async () => {
    if (!confirm("删除 template.xlsx?")) return;
    await api.adminDeleteTemplate(skill_id);
    await load();
  };

  const setChart = async () => {
    try {
      const obj = JSON.parse(chartJson) as Record<string, unknown>;
      await api.adminSetChart(skill_id, obj);
      alert("✓ chart.json 已保存");
      await load();
    } catch (e) {
      alert("无效 JSON 或保存失败: " + (e instanceof Error ? e.message : e));
    }
  };

  const delChart = async () => {
    if (!confirm("删除 chart.json?")) return;
    await api.adminDeleteChart(skill_id);
    await load();
  };

  const testRun = async () => {
    setBusy(true); setTestResult(null);
    try {
      const params: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(testParams)) {
        if (v === "") continue;
        params[k] = /^\d+$/.test(v) ? Number(v) : v;
      }
      const r = await api.adminTestRunSkill(skill_id, params);
      setTestResult(r);
    } catch (e) {
      alert("试运行失败: " + (e instanceof Error ? e.message : e));
    } finally { setBusy(false); }
  };

  const changeStatus = async (s: "draft" | "active" | "deprecated" | "archived") => {
    try {
      await api.adminSetSkillStatus(skill_id, s);
      alert("状态已更新为 " + s);
    } catch (e) {
      alert("失败: " + (e instanceof Error ? e.message : e));
    }
  };

  if (err && !isNew) {
    return <div className="bg-red-50 border border-red-200 p-4 rounded text-red-700">{err}</div>;
  }

  return (
    <div className="max-w-6xl space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <button onClick={() => navigate(-1)} className="text-sm text-zinc-500 hover:text-brand-600">← 返回</button>
          <h1 className="text-2xl font-semibold text-zinc-900 mt-1">
            {isNew ? "+ 新建 Skill" : `编辑 Skill: ${skill_id}`}
          </h1>
        </div>
        <div className="flex items-center gap-2">
          {!isNew && (
            <>
              <select
                onChange={(e) => changeStatus(e.target.value as never)}
                defaultValue=""
                className="px-2 py-1.5 text-sm border border-zinc-300 rounded"
              >
                <option value="" disabled>切状态...</option>
                <option value="draft">draft</option>
                <option value="active">active</option>
                <option value="deprecated">deprecated</option>
                <option value="archived">archived</option>
              </select>
              <button onClick={del} className="px-3 py-1.5 text-sm border border-red-300 text-red-600 rounded hover:bg-red-50">
                删除 Skill
              </button>
            </>
          )}
        </div>
      </div>

      {isNew && (
        <div className="bg-amber-50 border border-amber-200 rounded p-3">
          <label className="block text-sm font-medium text-amber-800 mb-1">skill id (^[a-z][a-z0-9_]{`{1,40}`}$)</label>
          <input
            value={newId}
            onChange={(e) => setNewId(e.target.value)}
            placeholder="例: monthly_sales_region"
            className="w-full px-3 py-2 border border-amber-300 rounded text-sm font-mono"
          />
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <div className="bg-white border border-zinc-200 rounded-lg p-3">
          <div className="text-sm font-medium text-zinc-700 mb-2">SKILL.md</div>
          <textarea
            value={skillMd}
            onChange={(e) => setSkillMd(e.target.value)}
            rows={22}
            className="w-full px-3 py-2 border border-zinc-300 rounded text-xs font-mono"
          />
        </div>
        <div className="bg-white border border-zinc-200 rounded-lg p-3">
          <div className="text-sm font-medium text-zinc-700 mb-2">service.yaml</div>
          <textarea
            value={serviceYaml}
            onChange={(e) => setServiceYaml(e.target.value)}
            rows={22}
            className="w-full px-3 py-2 border border-zinc-300 rounded text-xs font-mono"
          />
        </div>
      </div>

      <div className="flex items-center gap-2">
        {isNew ? (
          <button onClick={create} disabled={busy} className="px-4 py-2 bg-brand-600 text-white rounded hover:bg-brand-700 disabled:opacity-50 text-sm">
            ✨ 创建
          </button>
        ) : (
          <button onClick={save} disabled={busy} className="px-4 py-2 bg-brand-600 text-white rounded hover:bg-brand-700 disabled:opacity-50 text-sm">
            💾 保存
          </button>
        )}
      </div>

      {!isNew && (
        <>
          {/* 模板 / 图表 */}
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-white border border-zinc-200 rounded-lg p-4">
              <div className="text-sm font-medium text-zinc-700 mb-2">📄 template.xlsx</div>
              <div className="text-xs text-zinc-500 mb-2">
                {source?.has_template ? "✓ 已有模板" : "未上传"}
                <span className="text-amber-600 ml-2">.xlsm 含宏会被拒绝</span>
              </div>
              <input ref={fileRef} type="file" accept=".xlsx,.xlsm" className="text-xs" />
              <div className="flex gap-2 mt-2">
                <button onClick={uploadTpl} className="px-3 py-1 text-sm border border-zinc-300 rounded hover:bg-zinc-50">上传</button>
                {source?.has_template && (
                  <button onClick={delTpl} className="px-3 py-1 text-sm border border-red-300 text-red-600 rounded hover:bg-red-50">删除</button>
                )}
              </div>
            </div>
            <div className="bg-white border border-zinc-200 rounded-lg p-4">
              <div className="text-sm font-medium text-zinc-700 mb-2">📊 chart.json</div>
              <div className="text-xs text-zinc-500 mb-2">
                {source?.has_chart ? "✓ 已有图表配置" : "无"}
              </div>
              <textarea
                placeholder='{"kind":"bar","x":"OfficeCode","y":["NETWR_F"],"title":"销售"}'
                value={chartJson}
                onChange={(e) => setChartJson(e.target.value)}
                rows={4}
                className="w-full px-2 py-1 border border-zinc-300 rounded text-xs font-mono"
              />
              <div className="flex gap-2 mt-2">
                <button onClick={setChart} className="px-3 py-1 text-sm border border-zinc-300 rounded hover:bg-zinc-50">保存</button>
                {source?.has_chart && (
                  <button onClick={delChart} className="px-3 py-1 text-sm border border-red-300 text-red-600 rounded hover:bg-red-50">删除</button>
                )}
              </div>
            </div>
          </div>

          {/* 试运行 */}
          {skill && (
            <div className="bg-white border border-zinc-200 rounded-lg p-4">
              <div className="text-sm font-medium text-zinc-700 mb-3">▶ 试运行 (不写审计)</div>
              <div className="grid grid-cols-3 gap-3 mb-3">
                {skill.params.map((p) => (
                  <div key={p.name}>
                    <label className="block text-xs text-zinc-500 mb-1">
                      {p.name}{p.required && <span className="text-red-500"> *</span>}
                    </label>
                    {p.enum ? (
                      <select
                        value={testParams[p.name] || ""}
                        onChange={(e) => setTestParams({ ...testParams, [p.name]: e.target.value })}
                        className="w-full px-2 py-1 border border-zinc-300 rounded text-sm"
                      >
                        <option value="">(选)</option>
                        {p.enum.map((v) => <option key={v} value={v}>{v}</option>)}
                      </select>
                    ) : (
                      <input
                        value={testParams[p.name] || ""}
                        onChange={(e) => setTestParams({ ...testParams, [p.name]: e.target.value })}
                        className="w-full px-2 py-1 border border-zinc-300 rounded text-sm font-mono"
                      />
                    )}
                  </div>
                ))}
              </div>
              <button onClick={testRun} disabled={busy} className="px-4 py-2 text-sm bg-zinc-800 text-white rounded hover:bg-zinc-700 disabled:opacity-50">
                {busy ? "运行中..." : "▶ 试运行"}
              </button>

              {testResult ? (
                <TestResultBlock result={testResult as never} />
              ) : null}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function TestResultBlock({ result }: { result: {
  status: "done" | "failed"; error?: string | null; row_count: number;
  rows_preview: Record<string, unknown>[]; warnings: string[];
} }) {
  return (
    <div className="mt-3 p-3 rounded border bg-zinc-50">
      <div className="text-sm mb-2">
        状态: <span className={result.status === "done" ? "text-green-600" : "text-red-600"}>{result.status}</span>
        {" · "}行数: {result.row_count}
      </div>
      {result.error && (
        <div className="text-sm text-red-600 mb-2">{result.error}</div>
      )}
      {result.warnings && result.warnings.length > 0 && (
        <div className="text-sm text-amber-700 mb-2">
          ⚠ {result.warnings.join("; ")}
        </div>
      )}
      {result.rows_preview && result.rows_preview.length > 0 && (
        <DataTable rows={result.rows_preview} maxRows={10} />
      )}
    </div>
  );
}
