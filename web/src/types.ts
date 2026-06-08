// 对应后端 Pydantic / Schema 的 TS 类型 (与 §13 数据模型一致)

export interface Identity {
  username: string;
  display_name: string;
  role: "user" | "admin";
}

// /api/status 里的 llm 块(保留 current/current_ready 字段名,models 已弃用)
export interface LlmStatus {
  current: string;
  current_display: string;
  current_ready: boolean;
  models: unknown[];
}

// 模型示例(供设置页下拉,非强制)
export interface ModelSuggestion {
  id: string;
  display: string;
  notes: string;
  location: string;
  cost: string;
}

// DataAgent 式每用户 LLM 设置三元组 (GET /api/llm/settings)
export interface LlmSettings {
  has_key: boolean;            // 用户是否设了私有 key
  base_url: string;            // 用户设的 base_url(空=用 .env)
  model: string;               // 用户设的 model(空=用 .env)
  effective_model: string;     // 实际生效的模型
  effective_ready: boolean;    // 是否就绪(可发起对话)
  key_source: "user" | "env" | null;
  env_has_key: boolean;
  env_model: string;
  env_base_url: string;
  updated_at?: string | null;
  suggestions: ModelSuggestion[];
}

export interface LlmTestResult {
  ok: boolean;
  model?: string;
  key_source?: "user" | "env" | null;
  latency_ms?: number;
  reply?: string;
  error?: string;
  category?: "auth" | "network" | "rate_limit" | "not_configured" | "other";
}

export interface SkillParam {
  name: string;
  required: boolean;
  description?: string;
  default?: unknown;
  enum?: string[] | null;
}

export interface SkillSummary {
  id: string;
  title: string;
  description: string;
  keywords?: string[];
  params: SkillParam[];
  status?: "draft" | "active" | "deprecated" | "archived";
  favorite?: boolean;
}

export interface SkillDetail extends SkillSummary {
  owner?: string;
  instructions?: string;
  service?: string;
  entity_set?: string;
  filter_template?: string;
  select?: string[];
  orderby?: string;
  top?: string | number;
  apply?: string;
  sheet_title?: string;
}

export interface BWService {
  TechnicalServiceName: string;
  Title?: string;
  Description?: string;
  Version?: string;
  ServiceUrl?: string;
}

export interface ExcelRef {
  filename: string;
  size_bytes: number;
  download_url: string;
}

export interface SkillRunResponse {
  task_id: string;
  status: "done" | "failed";
  error?: string | null;
  row_count: number;
  rows_preview: Record<string, unknown>[];
  excel: ExcelRef | null;
  meta?: Record<string, unknown>;
}

export interface ToolCallTrace {
  name: string;
  arguments: Record<string, unknown>;
  is_error: boolean;
}

export interface ChatResponse {
  task_id: string;
  answer: string;
  iterations: number;
  tool_calls: ToolCallTrace[];
  input_tokens: number;
  output_tokens: number;
  llm_model: string;
  task: {
    status: "done" | "failed";
    row_count: number;
    rows_preview: Record<string, unknown>[];
    excel: ExcelRef | null;
  } | null;
}

export interface TaskListItem {
  id: string;
  username: string;
  source: string;
  skill_id: string | null;
  question: string;
  params: string;
  status: string;
  error: string | null;
  row_count: number | null;
  latency_ms: number | null;
  llm_model?: string | null;
  llm_input_tokens?: number | null;
  llm_output_tokens?: number | null;
  created_at: string;
  finished_at: string | null;
  filename?: string | null;
  file_path?: string | null;
  file_size?: number | null;
}

export interface AuditRow {
  id: number;
  username: string;
  action: string;
  task_id: string | null;
  question: string | null;
  service: string | null;
  odata_url: string | null;
  row_count: number | null;
  latency_ms: number | null;
  llm_model: string | null;
  llm_tokens: number | null;
  ip: string | null;
  created_at: string;
}

export interface SensitiveField {
  service: string;
  field: string;
  mask_mode: "redact" | "partial" | "hash";
  added_by: string;
  created_at: string;
}

export interface SystemStatus {
  version: string;
  bw_mode: "mock" | "live";
  bw: string;
  llm: LlmStatus;
  skills_count: number;
}

export interface Favorite {
  username: string;
  kind: "skill" | "task";
  ref_id: string;
  created_at: string;
}

export interface TaskMessage {
  id: string;
  task_id: string;
  role: "user" | "assistant" | "system";
  text: string | null;
  blocks: {
    tool_calls?: ToolCallTrace[];
    events?: AgentEvent[];
    task?: {
      status: string;
      excel_filename?: string | null;
      row_count?: number;
    } | null;
  } | null;
  created_at: string;
}

// ---------- 流式 SSE 事件 (对标 DataAgent AgentStep) ----------
// kind: progress | thought_delta | answer_delta | thought | tool_call | tool_result | task | final
export interface AgentEvent {
  kind: string;
  payload: Record<string, unknown>;
}

// task 事件 / 历史复原时的任务块
export interface ChatTaskPayload {
  task_id?: string;
  status?: "done" | "failed" | string;
  row_count?: number;
  rows_preview?: Record<string, unknown>[];
  excel?: ExcelRef | null;
}

export interface QuotaStatus {
  month: string;
  usage: { input_tokens: number; output_tokens: number; call_count: number };
  limit_tokens: number | null;
  remaining: number | null;
}

export interface AdminQuotaRow {
  username: string;
  input_tokens: number;
  output_tokens: number;
  call_count: number;
  limit_tokens: number | null;
}

export interface SkillSourceResp {
  id: string;
  skill_md: string;
  service_yaml: string;
  has_template: boolean;
  has_chart: boolean;
}
