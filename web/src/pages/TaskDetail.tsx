// 任务详情:预览 + 改参数重跑 + 下载 + 收藏 + 删除 (P0-5, F5/F7/F14)
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, ApiError } from "../api";
import type { SkillDetail, SkillRunResponse, TaskListItem } from "../types";
import DataTable from "../components/DataTable";
import ExcelCard from "../components/ExcelCard";

type FullTask = TaskListItem & { preview?: Record<string, unknown>[] };

export default function TaskDetail() {
  const { task_id = "" } = useParams();
  const navigate = useNavigate();
  const [task, setTask] = useState<FullTask | null>(null);
  const [skill, setSkill] = useState<SkillDetail | null>(null);
  const [error, setError] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [rerunParams, setRerunParams] = useState<Record<string, string>>({});
  const [favorite, setFavorite] = useState(false);

  const load = async () => {
    setError("");
    try {
      const t = await api.getTask(task_id);
      setTask(t);
      if (t.skill_id) {
        const s = await api.getSkill(t.skill_id);
        setSkill(s);
        // 初始化重跑参数 = 原任务的参数
        try {
          const original = JSON.parse(t.params || "{}") as Record<string, unknown>;
          const init: Record<string, string> = {};
          for (const p of s.params) {
            const v = original[p.name];
            init[p.name] = v == null ? (p.default != null ? String(p.default) : "") : String(v);
          }
          setRerunParams(init);
        } catch {
          // ignore
        }
      }
      // 收藏状态
      const favs = await api.listFavorites("task");
      setFavorite(favs.favorites.some((f) => f.ref_id === task_id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };

  useEffect(() => { void load(); }, [task_id]);

  const toggleFav = async () => {
    try {
      if (favorite) {
        await api.removeFavorite("task", task_id);
        setFavorite(false);
      } else {
        await api.addFavorite("task", task_id);
        setFavorite(true);
      }
    } catch (e) {
      alert("收藏失败: " + (e instanceof Error ? e.message : e));
    }
  };

  const rerun = async () => {
    setBusy(true);
    try {
      // 把字符串参数按 skill.params 类型还原 (数字尝试转,其他保持字符串)
      const params: Record<string, unknown> = {};
      for (const p of skill?.params || []) {
        const raw = rerunParams[p.name];
        if (raw === undefined || raw === "") {
          if (p.required) throw new Error(`必填参数 ${p.name} 不能空`);
          continue;
        }
        if (typeof p.default === "number" || /^\d+$/.test(raw)) {
          const n = Number(raw);
          params[p.name] = Number.isNaN(n) ? raw : n;
        } else {
          params[p.name] = raw;
        }
      }
      const resp: SkillRunResponse = await api.rerunTask(task_id, params);
      if (resp.status === "failed") {
        alert(`重跑失败: ${resp.error}`);
      } else {
        navigate(`/tasks/${resp.task_id}`, { replace: false });
      }
    } catch (e) {
      alert("重跑失败: " + (e instanceof Error ? e.message : e));
    } finally {
      setBusy(false);
    }
  };

  const del = async () => {
    if (!confirm("确定删除该任务?(连同 Excel 文件)")) return;
    try {
      await api.deleteTask(task_id);
      navigate("/tasks");
    } catch (e) {
      alert("删除失败: " + (e instanceof Error ? e.message : e));
    }
  };

  if (error) {
    return (
      <div className="max-w-4xl">
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
          {error}
        </div>
      </div>
    );
  }
  if (!task) return <div className="text-zinc-400 text-sm">加载中...</div>;

  return (
    <div className="max-w-5xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <button
            onClick={() => navigate(-1)}
            className="text-sm text-zinc-500 hover:text-brand-600"
          >
            ← 返回
          </button>
          <h1 className="text-2xl font-semibold text-zinc-900 mt-1">
            任务详情 <span className="text-zinc-400 text-base font-mono">{task.id}</span>
          </h1>
          <p className="text-sm text-zinc-500 mt-1">{task.question}</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={toggleFav}
            className={`px-3 py-1.5 text-sm rounded-md border ${
              favorite ? "border-yellow-400 bg-yellow-50 text-yellow-700"
                       : "border-zinc-300 text-zinc-500 hover:bg-zinc-50"
            }`}
          >
            {favorite ? "★ 已收藏" : "☆ 收藏"}
          </button>
          {task.filename && (
            <a
              href={api.downloadTaskUrl(task.id)}
              className="px-3 py-1.5 text-sm bg-brand-600 text-white rounded-md hover:bg-brand-700"
            >
              ⬇ 下载 Excel
            </a>
          )}
          <button
            onClick={del}
            className="px-3 py-1.5 text-sm border border-red-300 text-red-600 rounded-md hover:bg-red-50"
          >
            删除
          </button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div className="bg-white border border-zinc-200 rounded-lg p-4 space-y-1 text-sm">
          <div className="text-xs uppercase text-zinc-400 mb-2">基础信息</div>
          <Row k="状态" v={<StatusBadge status={task.status} />} />
          <Row k="模板" v={task.skill_id || "(自由对话)"} />
          <Row k="行数" v={task.row_count ?? 0} />
          <Row k="耗时" v={`${task.latency_ms ?? 0} ms`} />
          {task.llm_model && <Row k="LLM" v={task.llm_model} />}
          {task.llm_input_tokens != null && (
            <Row k="Token" v={`in ${task.llm_input_tokens} / out ${task.llm_output_tokens ?? 0}`} />
          )}
          <Row k="创建" v={task.created_at} />
          {task.finished_at && <Row k="结束" v={task.finished_at} />}
        </div>
        {task.filename && (
          <ExcelCard
            filename={task.filename}
            size_bytes={task.file_size ?? 0}
            download_url={api.downloadTaskUrl(task.id)}
            row_count={task.row_count ?? undefined}
          />
        )}
      </div>

      {task.error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4 text-sm text-red-700">
          {task.error}
        </div>
      )}

      {/* 改参数重跑 */}
      {skill && task.skill_id && (
        <div className="bg-white border border-zinc-200 rounded-lg p-4">
          <div className="text-sm font-medium text-zinc-700 mb-3">🔄 改参数重跑</div>
          <div className="grid grid-cols-2 gap-3">
            {skill.params.map((p) => (
              <div key={p.name}>
                <label className="block text-xs text-zinc-500 mb-1">
                  {p.name}{p.required && <span className="text-red-500"> *</span>}
                  {p.description && <span className="ml-2 text-zinc-400">{p.description}</span>}
                </label>
                {p.enum && p.enum.length > 0 ? (
                  <select
                    value={rerunParams[p.name] || ""}
                    onChange={(e) =>
                      setRerunParams({ ...rerunParams, [p.name]: e.target.value })}
                    className="w-full px-2 py-1.5 border border-zinc-300 rounded text-sm"
                  >
                    <option value="">(选)</option>
                    {p.enum.map((v) => <option key={v} value={v}>{v}</option>)}
                  </select>
                ) : (
                  <input
                    value={rerunParams[p.name] || ""}
                    onChange={(e) =>
                      setRerunParams({ ...rerunParams, [p.name]: e.target.value })}
                    className="w-full px-2 py-1.5 border border-zinc-300 rounded text-sm font-mono"
                  />
                )}
              </div>
            ))}
          </div>
          <button
            onClick={rerun}
            disabled={busy}
            className="mt-4 px-4 py-2 bg-brand-600 text-white rounded-md hover:bg-brand-700 disabled:opacity-50 text-sm"
          >
            {busy ? "运行中..." : "✨ 用新参数生成"}
          </button>
        </div>
      )}

      {/* 预览前 50 行 */}
      {task.preview && task.preview.length > 0 && (
        <div className="bg-white border border-zinc-200 rounded-lg p-4">
          <div className="text-sm font-medium text-zinc-700 mb-3">
            👁 预览 (前 {task.preview.length} 行)
          </div>
          <DataTable rows={task.preview as Record<string, unknown>[]} />
        </div>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex">
      <div className="w-16 text-zinc-400 text-xs pt-0.5">{k}</div>
      <div className="flex-1 text-sm">{v}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    done: "bg-green-100 text-green-700",
    failed: "bg-red-100 text-red-700",
    running: "bg-blue-100 text-blue-700",
    pending: "bg-zinc-100 text-zinc-700",
  };
  return (
    <span className={`px-2 py-0.5 rounded text-xs ${colors[status] || colors.pending}`}>
      {status}
    </span>
  );
}
