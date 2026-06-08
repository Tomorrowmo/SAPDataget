# 重构方案：LLM 接入改 JSON-action 流式 + 聊天工作台 UI

> 目标：把 SAPDataget 的「大语言接入逻辑 + 聊天 UI」对标 `DataAgent`(Numetrix)，
> 实现**思考逐字可见 + 工具时间线 + 流式答案 + 自愈纠错**，同时**完整保留**
> Skills 模板 / 多用户鉴权 / 配额 / 审计 / 凭据加密 / 报告清单快捷 等业务资产。
>
> 决策（2026-06-08 已与老板确认）：
> 1. LLM 内核换成 **JSON-action 协议**（替代原生 function-calling）
> 2. **渐进改造**，全保留业务资产
> 3. 前端**照搬 ChatView + WorkbenchPanel**，用现有 React+Vite+Tailwind 栈

---

## 一、为什么换协议

DataAgent 的「思考/答案逐字流式」依赖一个事实：LLM 只吐**一个 JSON 文本**
`{thought, action, args}`，后端可以**边收 token 边用正则抠出半成品 `thought` / `args.text`**
推给前端。原生 function-calling 把推理塞进 `tool_calls` 结构里，无法这样流式。

代价：失去原生工具参数 schema 校验 → 用**健壮 JSON 解析 + 三层自愈**补偿（已验证可行）。
收益：模型无关（弱 function-calling 的模型也能用）、思考可见、自愈、断路器。

## 二、动作集（SAP 版）

沿用现有 8 个工具语义，改写成 action：

| action | args | 实现（复用现有同步代码） |
|---|---|---|
| `answer` | `{text}` | 终态，流式输出 |
| `list_skills` | `{keywords?}` | `SkillRegistry.list` |
| `load_skill` | `{skill_id}` | `SkillRegistry.get().to_detail()` |
| `run_skill` | `{skill_id, params}` | `orchestrator.run_skill` → Excel |
| `ask_user` | `{question, options?}` | 流式发 `ask_user` 事件，前端弹按钮（本期可先降级为在 answer 里反问） |
| `list_bw_services` | `{search?}` | `bw.list_services` |
| `get_service_metadata` | `{service}` | `bw.get_metadata` |
| `execute_odata_query` | `{service,entity_set,filter,select,orderby,top,apply}` | `bw.execute_query`（回 5 行样本+总数） |
| `build_excel` | `{...,sheet_title}` | `bw.execute_query` + `orchestrator.run_free_query` → Excel |

**关键**：BW 客户端与 orchestrator 是同步阻塞的，在 async 生成器里必须用
`await asyncio.to_thread(...)` 包裹，避免堵死事件循环。

## 三、后端改动

### 3.1 `app/llm.py` — 加流式
保留现有同步 `complete(messages, tools)`（报告清单快捷 / CLI / 测试仍用）。
新增：
```python
async def stream(self, messages, *, temperature=0.2, max_tokens=2000) -> AsyncIterator[str]:
    # litellm.acompletion(..., stream=True) 跨 provider 流式，逐 token yield delta.content
```

### 3.2 `app/agent_stream.py` — 新建，JSON-action ReAct 异步生成器
移植 DataAgent `chat_agent.py` 的：
- `SYSTEM_PROMPT`（SAP 版：BW 取数目标 + 动作契约 + OData 最佳实践 + 时间换算 + 语言自适应）
- `_extract_partial_thought` / `_extract_partial_answer` / `_safe_parse_json`
- `run_turn(...)` 主循环：心跳 → 流式收 → 解析 → answer 收尾 / 执行动作 → 回灌
- 三层自愈（纯散文回退 / JSON 催重发 / 空答案重答）+ 断路器（同动作连错 3 次停）
- `AgentStep(kind, payload)`；kind ∈ progress|thought_delta|answer_delta|thought|tool_call|tool_result|final|task

新增 SAP 专属：当 `run_skill`/`build_excel` 产出 Excel 时，额外 yield 一个
`kind="task"` 事件，携带 `task_id, excel{filename,download_url,size}, row_count, rows_preview`，
前端据此渲染 ExcelCard + 预览表。

### 3.3 `app/server.py` — 新增 `POST /api/chat/stream`（async）
- 复用现有：配额拦截、建/续 task、落 user 消息、个人 key 解析（`_effective_key`）、
  未配置 key 的友好失败。
- 报告清单快捷（`_is_report_list_query`）：在流里包成 progress→task→final 事件。
- `StreamingResponse(event_stream(), media_type="text/event-stream", headers=SSE_HEADERS)`，
  `SSE_HEADERS` 含 `X-Accel-Buffering: no` / `Cache-Control: no-cache, no-transform`。
- `finally` 块**始终**落 assistant 消息 + 完整 events 到 `task_messages.blocks.events`，
  累加配额、写审计——即使客户端中途断开。
- `_diagnostic_hint(exc)`：apikey/timeout/ratelimit/connection/json → 人话提示。
- 保留旧 `POST /api/chat`（同步，back-compat，CLI/测试/报告清单）。

## 四、前端改动

### 4.1 `web/src/api.ts` + `types.ts`
新增 `streamChat(message, taskId, onEvent, signal)`：`fetch` + `getReader()`，
按 `\n\n` 切 SSE 块，`data:` 前缀的 `JSON.parse` 回调。新增 `AgentEvent` 类型。

### 4.2 `web/src/components/WorkbenchPanel.tsx` — 新建
右侧活动日志：按时间线渲染 progress/tool_call/tool_result；
`resolvedStarts(events)` 配对 llm_call↔llm_done、tool_start↔tool_done 决定 spinner 是否停转；
可展开看工具入参/结果。

### 4.3 `web/src/pages/Chat.tsx` — 重写
- 流式气泡：`thought_delta`→思考 chip 实时刷新；`answer_delta`→答案逐字；`final`→定稿。
- 右侧 WorkbenchPanel（窄屏可折叠）。
- 保留：ExcelCard、DataTable、登录用户 UName 过滤、报告清单快捷按钮、
  模型未配置 banner、多轮历史加载（`taskMessages` → 从 `blocks.events` 复原）。
- 错误人话化（结合后端 `_diagnostic_hint` + 前端兜底）。
- `AbortController` 支持「停止生成」。

## 五、不动的部分（资产保护）
auth / crypto / db / skills / excel / bw / orchestrator / 配额 / 审计 / 敏感字段
/ 报告清单快捷 / Home/Admin 等页面 / CLI / 现有测试，全部保持。新协议仅替换
「自由对话」这一条链路的内核与 UI。

## 六、验收
mock 模式下：`报告清单`走快捷出 Excel；`上月华东大区销售`走 LLM 多轮、思考逐字可见、
工具时间线完整、Excel 卡可下载；断网/错 key 有人话提示；刷新后多轮历史可复原。
