// HTTP 客户端 —— 与 FastAPI /api/* 对接,统一错误处理
import type {
  Identity, LlmStatus, SkillSummary, SkillDetail, SkillRunResponse,
  ChatResponse, TaskListItem, AuditRow, SensitiveField, SystemStatus, BWService,
  Favorite, TaskMessage, QuotaStatus, AdminQuotaRow, SkillSourceResp,
} from "./types";

class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const opts: RequestInit = {
    method,
    credentials: "include",
    headers: { "Content-Type": "application/json" },
  };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const resp = await fetch(path, opts);
  if (!resp.ok) {
    let msg = `${resp.status}`;
    try {
      const j = await resp.json();
      msg = j?.detail || j?.message || msg;
    } catch {
      // ignore
    }
    throw new ApiError(resp.status, msg);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const api = {
  // ---------- system ----------
  status: () => request<SystemStatus>("GET", "/api/status"),

  // ---------- auth ----------
  login: (username: string, password: string) =>
    request<Identity>("POST", "/api/auth/login", { username, password }),
  logout: () => request<{ ok: boolean }>("POST", "/api/auth/logout"),
  me: () => request<Identity>("GET", "/api/auth/me"),

  // ---------- llm ----------
  listModels: () => request<LlmStatus>("GET", "/api/llm/models"),
  switchModel: (model: string) =>
    request<LlmStatus>("POST", "/api/llm/model", { model }),

  // LLM provider API keys (per-user)
  listLlmKeys: () =>
    request<{ providers: Array<{
      env_var: string;
      provider: string;
      models: string[];
      configured: boolean;
      source: "user" | "env" | null;
      tail: string | null;
      updated_at: string | null;
      has_personal: boolean;
      has_env_fallback: boolean;
    }> }>("GET", "/api/llm/keys"),
  setLlmKey: (env_var: string, value: string) =>
    request<{ ok: boolean; env_var: string; tail: string }>(
      "PUT",
      `/api/llm/keys/${encodeURIComponent(env_var)}`,
      { value },
    ),
  deleteLlmKey: (env_var: string) =>
    request<{ ok: boolean }>("DELETE", `/api/llm/keys/${encodeURIComponent(env_var)}`),
  testLlmKey: (env_var: string) =>
    request<{
      ok: boolean;
      model?: string;
      latency_ms?: number;
      reply?: string;
      error?: string;
      category?: "auth" | "network" | "rate_limit" | "not_configured" | "other";
    }>("POST", `/api/llm/keys/${encodeURIComponent(env_var)}/test`),

  // ---------- skills ----------
  listSkills: (q?: string) =>
    request<{ skills: SkillSummary[]; total: number }>(
      "GET",
      "/api/skills" + (q ? `?q=${encodeURIComponent(q)}` : ""),
    ),
  getSkill: (id: string) => request<SkillDetail>("GET", `/api/skills/${id}`),
  runSkill: (id: string, params: Record<string, unknown>) =>
    request<SkillRunResponse>("POST", `/api/skills/${id}/run`, { params }),

  // ---------- services (BW) ----------
  listServices: (q?: string) =>
    request<{ services: BWService[]; count: number }>(
      "GET",
      "/api/services" + (q ? `?q=${encodeURIComponent(q)}` : ""),
    ),
  getServiceMeta: (name: string) =>
    request<{ entity_sets: { name: string; properties: Array<{ name: string; type: string; label?: string }>; keys?: string[] }[] }>(
      "GET",
      `/api/services/${encodeURIComponent(name)}`,
    ),

  // ---------- chat ----------
  chat: (message: string, task_id?: string) =>
    request<ChatResponse>("POST", "/api/chat", { message, task_id }),

  // ---------- tasks ----------
  listTasks: () =>
    request<{ tasks: TaskListItem[]; total: number }>("GET", "/api/tasks"),
  getTask: (id: string) =>
    request<TaskListItem & { preview?: Record<string, unknown>[] }>(
      "GET",
      `/api/tasks/${id}`,
    ),
  downloadTaskUrl: (id: string) => `/api/tasks/${id}/file`,
  rerunTask: (id: string, params?: Record<string, unknown>) =>
    request<SkillRunResponse>("POST", `/api/tasks/${id}/rerun`, { params: params ?? null }),
  deleteTask: (id: string) =>
    request<{ ok: boolean }>("DELETE", `/api/tasks/${id}`),
  taskMessages: (id: string) =>
    request<{ task_id: string; messages: TaskMessage[] }>("GET", `/api/tasks/${id}/messages`),
  taskStreamUrl: (id: string) => `/api/tasks/${id}/stream`,

  // ---------- favorites ----------
  listFavorites: (kind?: "skill" | "task") =>
    request<{ favorites: Favorite[]; total: number }>(
      "GET", `/api/favorites${kind ? `?kind=${kind}` : ""}`,
    ),
  addFavorite: (kind: "skill" | "task", ref_id: string) =>
    request<{ ok: boolean }>("POST", "/api/favorites", { kind, ref_id }),
  removeFavorite: (kind: "skill" | "task", ref_id: string) =>
    request<{ ok: boolean }>(
      "DELETE",
      `/api/favorites/${kind}/${encodeURIComponent(ref_id)}`,
    ),

  // ---------- quota ----------
  myQuota: () => request<QuotaStatus>("GET", "/api/quota/me"),
  adminListQuota: () =>
    request<{ quota: AdminQuotaRow[]; total: number }>("GET", "/api/admin/quota"),
  adminSetQuota: (username: string, monthly_tokens: number | null) =>
    request<{ ok: boolean }>("PUT", `/api/admin/quota/${encodeURIComponent(username)}`, {
      monthly_tokens,
    }),

  // ---------- admin ----------
  listAudit: (params?: { username?: string; action?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.username) qs.set("username", params.username);
    if (params?.action) qs.set("action", params.action);
    if (params?.limit) qs.set("limit", String(params.limit));
    return request<{ audit: AuditRow[]; total: number }>(
      "GET",
      "/api/audit" + (qs.toString() ? `?${qs}` : ""),
    );
  },
  listSensitive: () =>
    request<{ fields: SensitiveField[] }>("GET", "/api/sensitive-fields"),
  upsertSensitive: (service: string, field: string, mask_mode: SensitiveField["mask_mode"]) =>
    request<{ ok: boolean }>("POST", "/api/sensitive-fields", {
      service, field, mask_mode,
    }),
  deleteSensitive: (service: string, field: string) =>
    request<{ ok: boolean }>(
      "DELETE",
      `/api/sensitive-fields/${encodeURIComponent(service)}/${encodeURIComponent(field)}`,
    ),
  reloadSkills: () =>
    request<{ loaded: number }>("POST", "/api/admin/skills/reload"),

  // ---------- admin: skill CRUD ----------
  adminCreateSkill: (id: string, skill_md: string, service_yaml: string) =>
    request<{ ok: boolean; id: string }>("POST", "/api/admin/skills",
      { id, skill_md, service_yaml }),
  adminUpdateSkill: (id: string, skill_md?: string, service_yaml?: string) =>
    request<{ ok: boolean }>("PUT", `/api/admin/skills/${encodeURIComponent(id)}`,
      { skill_md, service_yaml }),
  adminDeleteSkill: (id: string) =>
    request<{ ok: boolean }>("DELETE", `/api/admin/skills/${encodeURIComponent(id)}`),
  adminSkillSource: (id: string) =>
    request<SkillSourceResp>("GET", `/api/admin/skills/${encodeURIComponent(id)}/source`),
  adminUploadTemplate: async (id: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    const resp = await fetch(`/api/admin/skills/${encodeURIComponent(id)}/files/template`, {
      method: "POST", credentials: "include", body: fd,
    });
    if (!resp.ok) {
      let msg = `${resp.status}`;
      try { const j = await resp.json(); msg = j?.detail || msg; } catch {}
      throw new ApiError(resp.status, msg);
    }
    return (await resp.json()) as { ok: boolean; size_bytes: number };
  },
  adminDeleteTemplate: (id: string) =>
    request<{ ok: boolean }>("DELETE", `/api/admin/skills/${encodeURIComponent(id)}/files/template`),
  adminSetChart: (id: string, chart: Record<string, unknown>) =>
    request<{ ok: boolean }>("PUT", `/api/admin/skills/${encodeURIComponent(id)}/chart`, { chart }),
  adminDeleteChart: (id: string) =>
    request<{ ok: boolean }>("DELETE", `/api/admin/skills/${encodeURIComponent(id)}/chart`),
  adminTestRunSkill: (id: string, params: Record<string, unknown>) =>
    request<{
      status: "done" | "failed"; error?: string | null; row_count: number;
      rows_preview: Record<string, unknown>[]; warnings: string[];
      meta?: Record<string, unknown>;
    }>("POST", `/api/admin/skills/${encodeURIComponent(id)}/test-run`, { params }),
  adminSetSkillStatus: (
    id: string,
    skill_status: "draft" | "active" | "deprecated" | "archived",
  ) => request<{ ok: boolean }>(
    "PATCH",
    `/api/admin/skills/${encodeURIComponent(id)}/status`,
    { status: skill_status },
  ),
};

export { ApiError };
