// 对应后端 Pydantic / Schema 的 TS 类型 (与 §13 数据模型一致)

export interface Identity {
  username: string;
  display_name: string;
  role: "user" | "admin";
}

export interface ModelInfo {
  id: string;
  display: string;
  provider: string;
  location: string;
  cost: string;
  notes: string;
  ready: boolean;
}

export interface LlmStatus {
  current: string;
  current_display: string;
  current_ready: boolean;
  models: ModelInfo[];
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
