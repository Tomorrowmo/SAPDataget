// HTTP 客户端 —— 与 FastAPI /api/* 对接,统一错误处理
import type {
  Identity, LlmStatus, SkillSummary, SkillDetail, SkillRunResponse,
  ChatResponse, TaskListItem, AuditRow, SensitiveField, SystemStatus, BWService,
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
  chat: (message: string) =>
    request<ChatResponse>("POST", "/api/chat", { message }),

  // ---------- tasks ----------
  listTasks: () =>
    request<{ tasks: TaskListItem[]; total: number }>("GET", "/api/tasks"),
  getTask: (id: string) =>
    request<TaskListItem & { preview?: Record<string, unknown>[] }>(
      "GET",
      `/api/tasks/${id}`,
    ),
  downloadTaskUrl: (id: string) => `/api/tasks/${id}/file`,

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
};

export { ApiError };
