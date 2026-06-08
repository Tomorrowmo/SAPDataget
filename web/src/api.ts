// HTTP 客户端 —— 与 FastAPI /api/* 对接,统一错误处理
import type {
  Identity, SkillSummary, SkillDetail, SkillRunResponse,
  ChatResponse, TaskListItem, AuditRow, SensitiveField, SystemStatus, BWService,
  Favorite, TaskMessage, QuotaStatus, AdminQuotaRow, SkillSourceResp, AgentEvent,
  LlmSettings, LlmTestResult, ChatSession,
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

// SSE 解析:fetch + getReader,按 \n\n 切事件块,只取 data: 行。
// 返回这轮的 task_id(从 meta/task 事件嗅探),供前端续聊与跳转。
async function streamChat(
  message: string,
  task_id: string | undefined,
  onEvent: (ev: AgentEvent) => void,
  signal?: AbortSignal,
): Promise<{ task_id: string | null }> {
  const resp = await fetch("/api/chat/stream", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, task_id: task_id ?? null }),
    signal,
  });
  if (!resp.ok || !resp.body) {
    let msg = `${resp.status}`;
    try {
      const j = await resp.json();
      msg = j?.detail || j?.message || msg;
    } catch {
      // ignore
    }
    throw new ApiError(resp.status, msg);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let resolvedTaskId: string | null = task_id ?? null;

  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf("\n\n")) >= 0) {
      const block = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 2);
      if (!block.startsWith("data:")) continue; // 忽略 ": ..." 探活注释
      const payload = block.slice(5).trim();
      if (!payload) continue;
      let ev: AgentEvent;
      try {
        ev = JSON.parse(payload) as AgentEvent;
      } catch {
        continue;
      }
      const tid = (ev.payload as { task_id?: string })?.task_id;
      if (tid) resolvedTaskId = tid;
      onEvent(ev);
    }
  }
  return { task_id: resolvedTaskId };
}

export const api = {
  // ---------- system ----------
  status: () => request<SystemStatus>("GET", "/api/status"),

  // ---------- auth ----------
  login: (username: string, password: string) =>
    request<Identity>("POST", "/api/auth/login", { username, password }),
  logout: () => request<{ ok: boolean }>("POST", "/api/auth/logout"),
  me: () => request<Identity>("GET", "/api/auth/me"),

  // ---------- llm 设置 (DataAgent 式 BYOK: key + base_url + model 三元组) ----------
  getLlmSettings: () => request<LlmSettings>("GET", "/api/llm/settings"),
  saveLlmSettings: (body: { api_key?: string | null; base_url: string; model: string }) =>
    request<LlmSettings>("PUT", "/api/llm/settings", body),
  testLlmSettings: () =>
    request<LlmTestResult>("POST", "/api/llm/settings/test"),

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
  // 流式对话:POST /api/chat/stream 返回 text/event-stream,逐事件回调。
  // 返回最终建立/沿用的 task_id(从 task / final 事件里嗅探)。
  streamChat: (
    message: string,
    task_id: string | undefined,
    onEvent: (ev: AgentEvent) => void,
    signal?: AbortSignal,
  ) => streamChat(message, task_id, onEvent, signal),

  // ---------- chat sessions (会话历史) ----------
  listChatSessions: () =>
    request<{ sessions: ChatSession[]; total: number }>("GET", "/api/chat/sessions"),
  renameChatSession: (id: string, title: string) =>
    request<{ ok: boolean; id: string; title: string }>(
      "PATCH", `/api/chat/sessions/${encodeURIComponent(id)}`, { title },
    ),

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
