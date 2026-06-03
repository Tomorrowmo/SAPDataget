# SAP BW 7.5 自然语言查询平台 — 设计文档

版本 v0.1 · 草案 · 2026-06-02

> ⚠️ **本文档已被 [需求分析与技术方案.md](需求分析与技术方案.md) (v0.2) 取代**。
> v0.1 把产品定位为「BW 上的 ChatGPT」，v0.2 重新定位为「BW 上的取数副驾驶」——
> Excel-first、引入 Skills 模板系统、任务式交互。保留此文档仅作历史对照。

---

## 1. 项目背景与目标

**背景**：现有 CLI 工具（[chat_bw.py](chat_bw.py)）已能让 BW 管理员用自然语言查询 BW 7.5 报表，
经 Claude Opus 4.7 自动拼装 OData V2 请求并回复结果。但 CLI 只面向技术人员。

**目标**：把这套能力开放给**业务用户**（销售、财务、供应链等），通过 Web 浏览器使用，
让他们**不写 BEx Query、不打开 Analysis for Office** 就能拿到关心的数字。

**非目标**：
- 不替代 BO/Lumira/AfO 做复杂 OLAP 切片。
- 不做数据建模，只查现有 BW Query / DSO / InfoCube 已暴露的 OData 服务。
- 不做权限模型重建 —— 复用 BW 的分析授权（Analysis Authorization）。

**成功指标**：
1. 业务用户从「想知道某数字」到「看到结果」< 30 秒。
2. 80% 常见提问无须管理员介入，Claude 能自主选服务、拼查询。
3. 所有访问可审计（谁、何时、问了什么、生成了什么 OData URL、返回多少行）。

---

## 2. 用户画像与场景

| 角色 | 占比 | 关注点 | 典型提问 |
|---|---|---|---|
| 业务用户（终端） | 90% | 简单、快、能导出 Excel | "上海大区上月销售额前 10 客户"<br>"这季度毛利率比去年同期" |
| BW 管理员 | 5% | 审计、排错、调整 Claude 提示词 | "为什么用户 A 拿到 0 行？" |
| 数据治理 / 合规 | 5% | 谁查了什么、敏感字段是否泄露 | "导出近 30 天所有涉及薪酬字段的查询" |

业务用户**不会写 OData**，也不知道服务技术名。他们只关心：**问—答—下载**。

---

## 3. 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│  Browser (业务用户 PC / Windows 11)                              │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ React SPA  (Vite + TypeScript + Tailwind + shadcn/ui)    │   │
│  │   - 对话视图  - 结果表格  - 图表  - 历史侧栏  - 登录页    │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                         │ HTTPS · Cookie(JWT) · SSE
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  Backend  (FastAPI · Uvicorn · 单 Windows Server / Docker)       │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ /api/auth           登录 (BW Basic Auth 验证一次)          │   │
│  │ /api/conversations  对话历史 CRUD                          │   │
│  │ /api/chat/stream    问答主流程（SSE 流式）                  │   │
│  │ /api/services       OData 服务目录（带缓存）                │   │
│  │ /api/export/{msg}   导出结果为 CSV / XLSX                   │   │
│  │ /api/audit          审计日志（管理员）                       │   │
│  │                                                            │   │
│  │ 核心：agent.run_turn_stream()  — 改造现有 agent.py          │   │
│  │   ├→ Claude API (Opus 4.7, adaptive thinking, tools)       │   │
│  │   └→ BWClient (现有 bw_client.py,加 metadata 缓存层)        │   │
│  └──────────────────────────────────────────────────────────┘   │
│         │                              │                          │
│         ▼                              ▼                          │
│  ┌─────────────┐              ┌──────────────────┐                │
│  │ SQLite      │              │ Metadata Cache    │                │
│  │ users       │              │ (内存 + 文件 24h) │                │
│  │ conversations│             └──────────────────┘                │
│  │ messages    │                                                   │
│  │ tool_calls  │                                                   │
│  │ audit_log   │                                                   │
│  │ bw_creds*   │  *加密存储,见 §9                                 │
│  └─────────────┘                                                   │
└─────────────────────────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────┐
│  SAP BW 7.5  (内网)                                              │
│   - NetWeaver Gateway: /sap/opu/odata/sap/* 服务                 │
│   - 服务目录:           /sap/opu/odata/iwfnd/CATALOGSERVICE;v=2  │
│   - Auth: Basic Auth (用户登录时输入 BW 用户名密码)               │
└─────────────────────────────────────────────────────────────────┘
                         ▲
                         │ HTTPS
┌─────────────────────────────────────────────────────────────────┐
│  Anthropic API (外网, 公网出口)                                  │
│   - claude-opus-4-7, adaptive thinking, tool use                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 4. 技术选型

| 层 | 选型 | 理由 | 备选 |
|---|---|---|---|
| 前端框架 | **React 18 + Vite + TypeScript** | 主流、招聘容易、生态足 | Vue 3（若团队已有积累） |
| UI 组件库 | **shadcn/ui + Tailwind CSS** | 可控、可主题化、不锁死 | Ant Design（更"企业"） |
| 图表 | **Apache ECharts** | 国内最熟、堆叠/钻取强 | Recharts（轻但能力弱） |
| 表格 | **TanStack Table** | 大数据虚拟滚动 | AG Grid Community |
| 流式 | **EventSource (SSE)** | 单向流够用、比 WS 简单 | WebSocket |
| 后端 | **FastAPI + Uvicorn** | 复用现有 Python 代码、原生异步 | Flask、Django |
| 持久化 | **SQLite（启动期）→ PostgreSQL（扩容时）** | 内网部署、少运维 | SQLite + Litestream |
| 鉴权 | **JWT Cookie（HttpOnly + SameSite=Strict）** | 抗 XSS、跨域简单 | Session in DB |
| 部署 | **Docker Compose（单机）** | 一行起停 | 直接 Windows Service |
| 包管理 | 前端 pnpm · 后端 uv | 比 npm/pip 快很多 | npm / pip |

---

## 5. 后端 API 设计

### 5.1 鉴权

#### `POST /api/auth/login`
```jsonc
// Request
{
  "username": "ZUSER01",           // BW 用户名
  "password": "***",               // BW 密码（HTTPS 传输）
  "remember": false
}
// Response 200
{
  "token": "<JWT>",                // 也通过 HttpOnly Cookie 下发
  "user": { "username": "ZUSER01", "displayName": "张三" },
  "expires_at": "2026-06-02T18:00:00Z"
}
// Response 401  -> BW 拒绝该凭据
```

**实现**：后端用收到的用户名密码对 BW `/sap/opu/odata/iwfnd/CATALOGSERVICE;v=2/ServiceCollection?$top=1`
发起一次 GET。返回 200 → 凭据有效；其他 → 401。
凭据**加密后**存入 `bw_creds` 表（详见 §9），JWT 仅保存 username + cred_id。

#### `POST /api/auth/logout` — 删 cred、撤 JWT

#### `GET /api/auth/me` — 当前用户信息

### 5.2 对话

#### `GET /api/conversations` — 列出我的对话
```jsonc
[
  { "id": "c_abc", "title": "上海销售对比", "updated_at": "...", "message_count": 8 },
  ...
]
```

#### `POST /api/conversations` — 新建空对话，返回 `id`

#### `GET /api/conversations/{id}` — 取该对话所有消息（含工具调用记录）

#### `DELETE /api/conversations/{id}` — 删除

### 5.3 问答（核心）

#### `POST /api/chat/stream` — **SSE 流**
```jsonc
// Request body
{
  "conversation_id": "c_abc",      // 不传则新建
  "message": "上海大区上月销售额前 10"
}
```

**响应：text/event-stream**，事件类型如下：

| 事件 | 数据 | 说明 |
|---|---|---|
| `message_start` | `{ message_id, conversation_id }` | 一次回合开始 |
| `thinking_delta` | `{ text }` | （可选）Claude 思考流（仅管理员可见，业务用户隐藏） |
| `tool_use_start` | `{ id, name, input }` | 工具调用开始（前端显示"正在查询 BW…"） |
| `tool_result` | `{ id, ok, summary, url? }` | 工具返回。`url` 是 OData URL，仅管理员可见 |
| `text_delta` | `{ text }` | 助手文本片段（Claude 最终回复） |
| `table` | `{ columns, rows, total }` | **结构化结果**：用真表格渲染而非 markdown |
| `chart_suggestion` | `{ kind, x, y, series }` | 后端基于结果自动建议图表配置 |
| `message_end` | `{ message_id, usage }` | 含 tokens/cost |
| `error` | `{ code, message }` | 失败 |

**关键改动 vs 现有 [agent.py](agent.py)**：
- `run_turn` → `run_turn_stream`：用 `client.messages.stream(...)` 并 yield 上述事件。
- 工具结果中提取 `data.rows`，作为单独的 `table` 事件直接发给前端，避免 Claude 把数千行渲染成 markdown。

### 5.4 服务目录（性能优化）

#### `GET /api/services?search=sales`
返回**缓存的**服务列表 + 元数据摘要。后台任务每 24h 刷新。
这样 Claude 不必每次都调 `list_bw_services` —— 系统提示词里可以预置常用服务清单。

### 5.5 导出

#### `GET /api/export/{message_id}?format=csv|xlsx`
后端从 `messages.table_data` 读出，流式生成文件。
XLSX 用 `openpyxl`；CSV 用标准库 `csv`。

### 5.6 审计（仅管理员）

#### `GET /api/audit?user=&from=&to=&format=csv`
返回字段：`timestamp, user, question, service, entity_set, odata_url, row_count, latency_ms, claude_tokens`。

---

## 6. 前端 UI 设计

### 6.1 信息架构

```
/login                  登录页（用户名+密码）
/                       重定向到最近对话或新建
/c/:conversation_id     主聊天界面
/history                历史列表（移动端用）
/admin                  管理员视图：审计、服务清单、提示词调优
```

### 6.2 主界面线框

```
┌────────────────────────────────────────────────────────────────────────┐
│ BW 智能查询助手        服务: 86 个 (24h前同步)        张三 ▼  [设置]    │
├────────────┬───────────────────────────────────────────────────────────┤
│ + 新对话    │   你 · 14:32                                              │
│            │   ┌─────────────────────────────────────────────────────┐│
│ 今天        │   │ 上海大区上月销售额前 10 客户                          ││
│ ▸ 上海销售   │   └─────────────────────────────────────────────────────┘│
│   对比      │                                                            │
│ ▸ 库存周转   │   助手 · 14:32                                            │
│            │   ⚙ 已查询服务 ZBW_SALES_SRV  ▾                           │
│ 昨天        │   ┌─────────────────────────────────────────────────────┐│
│ ▸ 毛利分析   │   │ 以下是 2026 年 5 月上海大区销售额前 10 客户：         ││
│            │   │                                                       ││
│            │   │ ┌────┬──────────────┬────────────┬──────────┐        ││
│            │   │ │排名 │ 客户          │ 销售额(万) │ 同比      │        ││
│            │   │ ├────┼──────────────┼────────────┼──────────┤        ││
│            │   │ │ 1  │ 张江实业       │   852.4    │ +12.3%   │        ││
│            │   │ │ 2  │ ...           │   ...      │ ...      │        ││
│            │   │ └────┴──────────────┴────────────┴──────────┘        ││
│            │   │ [⬇ 下载 CSV] [⬇ 下载 Excel] [📊 画柱状图]              ││
│            │   └─────────────────────────────────────────────────────┘│
│            │                                                            │
│            │   ┌───────────────────────────────────────────────┐ [发送]│
│            │   │ 继续问点什么…                                  │       │
│            │   └───────────────────────────────────────────────┘       │
└────────────┴───────────────────────────────────────────────────────────┘
```

### 6.3 关键 UI 决策

1. **工具调用默认折叠**：业务用户只看「⚙ 已查询服务 X」一行；点开看 OData URL（管理员才显示）。
2. **表格不用 Markdown**：用 TanStack Table，支持排序、固定列、虚拟滚动、单元格右键复制。
3. **画图按钮**：点了之后前端弹窗，让用户选 X/Y 轴和图表类型，调 ECharts 渲染。
4. **流式打字**：`text_delta` 一到就追加，业务用户看到「正在打字」减少焦虑。
5. **错误友好化**：BW 返回 500/超时不要把 SAP 堆栈直接吐出来 —— 后端转译成「查询失败：字段 X 不存在，建议重新提问」。
6. **历史对话标题**：第一次回复后让 Claude 起个 ≤12 字的中文标题，自动写到 `conversations.title`。
7. **键盘**：Enter 发送、Shift+Enter 换行、↑ 召回上一条提问。

### 6.4 移动端

二期再做。一期用 Tailwind 的 `md:` 断点做基本响应式，但不优化触屏交互。

---

## 7. 关键流程（时序）

### 7.1 一次问答（业务用户视角）

```
用户       前端SPA          后端FastAPI            Claude          BW Gateway
 │ "上月销售" │                  │                    │                │
 │──输入──→ │                  │                    │                │
 │          │──POST /chat/stream→│                  │                │
 │          │                  │──messages.stream(tools)→            │
 │          │                  │←──tool_use_start────│                │
 │          │←─event:tool_use──│  (Claude 决定调 list_services)        │
 │ "查询中" │                  │                    │                │
 │          │                  │  缓存命中 → 直接返回(免一次 BW 往返)   │
 │          │                  │──tool_result──→Claude               │
 │          │                  │←──tool_use_start────                 │
 │          │←─event:tool_use──│  (调 execute_odata_query)             │
 │ "查BW中" │                  │──GET /sap/opu/odata/...─────────→ │
 │          │                  │←─────JSON rows─────────────────── │
 │          │                  │──tool_result──→Claude               │
 │          │                  │←──text_delta×N──────                 │
 │          │←─event:text_delta│                                       │
 │          │←─event:table──── │  (后端从结果剥出表格)                  │
 │ 看到表格 │←─event:msg_end── │                                       │
```

### 7.2 元数据缓存刷新

后台 APScheduler 任务，每天凌晨 2 点：
1. 调 `list_services` 拿全量服务
2. 对每个服务调 `get_metadata`，存到 `service_metadata` 表
3. 失败的服务记录到 `service_health` 表

启动时若缓存空，则立即跑一次（同步等待，登录页显示「初始化中…」）。

---

## 8. 鉴权与多用户

### 关键决策：**每用户独立 BW 凭据**

理由：BW 的**分析授权（Analysis Authorization）**靠登录用户决定能看哪些行（如华东大区销售只能看自己区域）。
若全平台用一个服务账号，则所有人能看所有数据，违反 BW 既定权限模型。

**实现**：
1. 登录页让用户输入「BW 用户名 + BW 密码」（同 SAP Logon 那对凭据）。
2. 后端验证后，密码用 **AES-256-GCM 加密**，密钥从 Windows DPAPI / 环境变量 `BW_CRED_KEY` 取（32 字节，启动时校验）。
3. 加密后的密文存到 `bw_creds` 表，主键 `cred_id`（UUID）。
4. JWT payload = `{ sub: username, cred_id, exp }`。
5. 每次请求，后端按 `cred_id` 解密，构造 `BWClient` 实例（不复用，避免线程问题）。
6. JWT 8 小时过期，过期后需重新输 BW 密码。
7. 登出或会话过期立即从 `bw_creds` 删行。

**二期升级**：SAML / OAuth2 SSO + Principal Propagation（BW 端配合启用 SAP Logon Ticket / X.509），免去用户重复输密码。

---

## 9. 数据持久化

### 9.1 表结构（SQLite, 一期）

```sql
CREATE TABLE users (
  username       TEXT PRIMARY KEY,
  display_name   TEXT,
  role           TEXT NOT NULL DEFAULT 'user',  -- user | admin
  created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
  last_login_at  DATETIME
);

CREATE TABLE bw_creds (
  cred_id        TEXT PRIMARY KEY,             -- UUID
  username       TEXT NOT NULL REFERENCES users(username),
  ciphertext     BLOB NOT NULL,                -- AES-256-GCM(password)
  nonce          BLOB NOT NULL,
  expires_at     DATETIME NOT NULL,
  created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE conversations (
  id             TEXT PRIMARY KEY,             -- c_xxx
  username       TEXT NOT NULL REFERENCES users(username),
  title          TEXT,
  created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
  updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_conv_user_updated ON conversations(username, updated_at DESC);

CREATE TABLE messages (
  id             TEXT PRIMARY KEY,             -- m_xxx
  conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role           TEXT NOT NULL,                -- user | assistant
  text           TEXT,
  table_data     TEXT,                         -- JSON: {columns, rows}
  created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE tool_calls (
  id             TEXT PRIMARY KEY,
  message_id     TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  tool_name      TEXT NOT NULL,
  tool_input     TEXT NOT NULL,                -- JSON
  tool_output    TEXT,                         -- 截断到 8KB
  odata_url      TEXT,                         -- 便于审计
  status_code    INTEGER,
  latency_ms     INTEGER,
  created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE service_metadata (
  service        TEXT PRIMARY KEY,
  title          TEXT,
  description    TEXT,
  metadata_json  TEXT NOT NULL,                -- 简化后的 entity_sets
  raw_size       INTEGER,
  refreshed_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE audit_log (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  username       TEXT NOT NULL,
  action         TEXT NOT NULL,                -- chat | export | login | logout
  question       TEXT,
  service        TEXT,
  odata_url      TEXT,
  row_count      INTEGER,
  latency_ms     INTEGER,
  claude_tokens  INTEGER,
  ip             TEXT,
  created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_audit_time ON audit_log(created_at DESC);
CREATE INDEX idx_audit_user ON audit_log(username, created_at DESC);
```

### 9.2 数据保留

| 数据 | 保留期 | 清理方式 |
|---|---|---|
| `bw_creds` | 至 JWT 过期 + 1h | 后台任务每小时清理 |
| `conversations` / `messages` | 90 天（可配置） | 软删除 + cron |
| `tool_calls.tool_output` | 30 天 | 30 天后只保留 url + status |
| `audit_log` | 2 年（合规） | 满期归档到冷存储 CSV |

---

## 10. 安全与合规

| 风险 | 对策 |
|---|---|
| BW 密码泄露 | HTTPS only、HttpOnly Cookie、AES-256-GCM、密钥不入库、登录失败延迟 |
| Claude 看到敏感字段 | **字段黑名单**：管理员配置 `sensitive_fields`（如 SALARY_BASE、ID_CARD），后端在把行送给 Claude 前自动 mask 成 `***` |
| Prompt Injection | Claude 不直接执行用户输入的 OData —— 工具的 schema 强约束 `entity_set`/`filter` 等参数,且后端对 `filter` 字段做白名单字符校验（拒绝换行、分号） |
| 数据外发 | Claude API 走外网。一期：默认所有结果**只前 50 行**送给 Claude，业务用户看到的完整结果不经过 Claude 二次处理。二期：若有合规要求，可走 Anthropic 的[企业数据保留方案](https://www.anthropic.com/legal/privacy)或部署到 AWS Bedrock 内网 |
| 跨用户数据穿透 | 每请求构造独立 `BWClient`、独立 cred；前端 `/api/conversations/{id}` 后端二次校验 `ownerUsername == sub` |
| 慢查询拖垮 BW | 请求层超时 60s + 默认 `$top=100` + 后端速率限制 30 req/min/user |
| 审计完整 | 每个 chat / export / login 写 `audit_log`，不依赖 Claude 工具调用记录 |

---

## 11. 错误处理与降级

| 故障 | 降级行为 |
|---|---|
| Claude API 不可用 | 显示「智能助手暂时不可用，可直接点服务清单浏览」+ 提供原 OData 直查页面（二期） |
| BW Gateway 不可用 | 弹窗「BW 系统离线」，可读历史结果但不能新查 |
| Metadata 缓存为空 | 首次启动时 Claude 调 `list_services`/`get_metadata` 实时拉，慢但能用 |
| Claude 拼错 filter | BW 返回 400 → 工具结果带原文 → Claude 自纠重试 ≤ 2 次 → 仍失败则向用户解释 |
| 用户问超大查询 | 后端检测 `row_count_total > 10000` → 不下发完整结果，仅给 top 50 并提示「结果过多，请加限制条件」 |

---

## 12. 部署架构

### 一期：单 Windows Server (内网)
```
docker-compose.yml
├── backend (FastAPI + uvicorn, 端口 8000)
├── frontend-static (Nginx 托管 React build, 端口 80/443 + 反代 /api → backend)
└── 卷: ./data → /app/data  (SQLite, metadata cache)
```

**反代关键 SSE 配置**（Nginx）：
```nginx
location /api/chat/stream {
    proxy_pass http://backend:8000;
    proxy_buffering off;            # 关键：禁用缓冲才能流式
    proxy_read_timeout 300s;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
}
```

### 二期：高可用
- 后端水平扩容（无状态化前提：把 `bw_creds` 和 JWT 黑名单迁到 Redis）
- SQLite → PostgreSQL
- Anthropic API 走代理或 [Claude on AWS Bedrock](https://aws.amazon.com/bedrock/claude/)

---

## 13. 开发里程碑

| 里程碑 | 工作量(人日) | 交付物 |
|---|---|---|
| M0 后端骨架 | 3 | FastAPI 启动、登录、对话 CRUD、SQLite |
| M1 SSE 流式问答 | 5 | `agent.run_turn_stream`、`/api/chat/stream`、最小前端连通 |
| M2 前端主界面 | 6 | 对话视图、流式渲染、表格、历史侧栏 |
| M3 元数据缓存 | 2 | 后台任务、`/api/services`、Claude 提示词嵌入服务清单 |
| M4 导出 + 画图 | 3 | CSV/XLSX 下载、ECharts 弹窗 |
| M5 鉴权加固 + 审计 | 4 | AES 加密、审计页、字段黑名单 |
| M6 错误友好化 + 文档 | 2 | 业务用户使用手册、管理员手册 |
| M7 联调 + UAT | 5 | 5 个真实业务用户灰度,采集反馈 |
| **合计** | **30 人日** | 1.5 人月，单人开发 ≈ 6 周 |

---

## 14. 待确认事项

| # | 问题 | 默认决策 | 需谁拍板 |
|---|---|---|---|
| 1 | 是否启用 SSO（SAML/Kerberos）？ | 一期用 Basic Auth；二期接 SSO | IT 安全 |
| 2 | 字段黑名单清单？ | 管理员事后配置；先空 | 数据治理 |
| 3 | Anthropic API 是否允许外网？ | 假设允许；若否走 Bedrock | 网络安全 |
| 4 | Claude 思考链是否对业务用户可见？ | 默认隐藏，管理员可见 | 产品 |
| 5 | 业务用户能否直接看 OData URL？ | 否，仅管理员 | 产品 |
| 6 | 部署单机还是高可用？ | 一期单机 | 运维 |
| 7 | 数据保留期是否符合公司策略？ | 默认 90 天对话 / 2 年审计 | 合规 |

---

## 附录 A：与现有 CLI 工程的对应

| 现有文件 | 改造方向 |
|---|---|
| [bw_client.py](bw_client.py) | 保留；加 `MetadataCache` 装饰器，从 SQLite 读 `service_metadata` |
| [agent.py](agent.py) | 增 `run_turn_stream(...)` 异步生成器,yield SSE 事件 |
| [config.py](config.py) | 拆为 `server_config.py`（运行时配置）+ `user_credentials.py`（运行时从 DB 取） |
| [chat_bw.py](chat_bw.py) | 保留作为运维诊断工具 |
| _(新)_ `server/main.py` | FastAPI app 入口 |
| _(新)_ `server/db.py` | SQLAlchemy / 原生 sqlite3 + 迁移 |
| _(新)_ `web/` | React + Vite 工程 |

---

文档结束。  评审通过后进入 M0 编码。
