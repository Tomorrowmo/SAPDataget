# SAP OData 对接问题分析与修正方案

> 作者视角：OData V2 / SAP NetWeaver Gateway + Agent 专家
> 日期：2026-06-08
> 范围：仅分析"与 SAP OData 对接 + 取数 Agent"相关的接口与设计逻辑问题，不含前端样式/无关模块。

---

## 0. 取证边界声明（重要）

本地**没有 SAP 环境**。本文所有关于 SAP 端的事实**只来自项目已有产物**（代码、skills 配置、mock_data、对接清单文档、ABAP demo），**不臆造**。每条问题分为两类证据，已分别标注：

- **【已证实】**：直接来自项目代码/文档/数据的事实（带 `文件:行号`）。
- **【协议推断】**：来自 OData V2 / SAP Gateway 的**公开协议规范**（非项目臆造），且**项目文档自己已承认**这类风险——见 `需求分析与技术方案.md §8.9「字段命名漂移：Mock 字段结构 ≠ 真实 BW 元数据」`。凡标【协议推断】者，须在接真 SAP 后用真实 `$metadata`/响应体复核。

---

## 1. 已确认的对接事实（速览）

| 项 | 值 | 出处 |
|---|---|---|
| 生产 BW 主机 | `http://sapbd1app01.cn.schneider-electric.com:8000`（**HTTP 明文**，非 HTTPS） | [server.py:143](../app/server.py#L143) |
| OData 服务根 | `/sap/opu/odata/sap` | [live.py:23](../app/bw/live.py#L23) |
| 目录服务 | `/sap/opu/odata/iwfnd/CATALOGSERVICE;v=2/ServiceCollection`（V2） | [live.py:22](../app/bw/live.py#L22) |
| 报告清单服务 | `ZBW_QUERY_LIST_SRV` / EntitySet `LtResultSet` | [server.py:138-144](../app/server.py#L138) |
| 认证 | HTTP Basic Auth + `sap-client` + `sap-language` | [live.py:32-47](../app/bw/live.py#L32) |
| sap-client | `505`，`BW_VERIFY_SSL=false` | 对接清单文档 |
| 查询格式 | 通用查询 `$format=json`；报告清单走 **Atom/XML** | [live.py:158](../app/bw/live.py#L158)、[server.py:250](../app/server.py#L250) |
| 业务服务 | `ZBW_SALES_SRV`/`ZBW_PROD_SRV`/`ZBW_INV_SRV`/`ZBW_FIN_SRV`/`ZBW_PROC_SRV` 等 | `mock_data/catalog.json` |
| 字段风格 | `NETWR_F`/`GROSS_PROFIT`/`CALMONTH`(YYYYMM)/`KUNNR`/`WERKS` 等 BW 风格 | `mock_data/services/*/meta.json` |

---

## 2. 问题清单（按严重度）

### 🔴 P0-1　全量导出被硬截断到 200 行（核心功能失效）【已证实】

**现象**：LiveBWClient 把**任何**查询结果在返回前截断到 200 行：

- JSON 路径：[live.py:192](../app/bw/live.py#L192) `"rows": _strip_metadata(rows[:200])`
- XML 路径：[live.py:391](../app/bw/live.py#L391) `"rows": rows[:200]`

而 `row_count_returned = len(rows)`（截断前长度）、`row_count_total = __count`（全表总数）照常返回。

**后果链**（全链路已证实）：
1. Agent `build_excel` 用 `top=1000`（[agent_stream.py](../app/agent_stream.py) `build_excel` 分支）调 `execute_query`；
2. live 客户端只回 ≤200 行；
3. `orchestrator.run_free_query(rows=rows)` 拿到 ≤200 行写进 Excel；
4. 但 `info["row_count"] = row_count_total`（可能数千）。

→ **用户要"导出全部/前1000条"，实际 Excel 只有 200 行，却显示"共 N 条"**。这直接违背产品核心承诺（NL→可下载的完整 Excel）。Mock 模式因数据量小掩盖了该问题。

**为什么是错的（专家视角）**：`[:200]` 把"喂给 LLM 的样本上限"和"导出给用户的全量"混为一谈。SAP Gateway 本身还有**服务端分页**（默认 page size / `$top` 上限 / 返回 `d.__next`），客户端当前**完全不分页**，所以即使去掉 200，也只拿到一页。

**修正**：
1. 拆分两条路径：**给 LLM 的样本**（5~20 行，省 token）vs **给 Excel 的全量**（不截断）。
2. 实现**分页拉取**：循环 `$skip += pageSize` 或跟随 `d.__next`，直到取满 `row_count_total` 或达到安全上限（如 `MAX_EXPORT_ROWS`，可配）。
3. `execute_query` 增加 `purpose: "sample" | "export"` 或 `max_rows` 参数，导出路径传全量。
4. Excel 的 `row_count` 用**实际写入行数**，与"总数"分开显示（"已导出 X / 共 N"）。

---

### 🔴 P0-2　$filter 拼接无单引号转义 → OData 注入 + 正常数据报错【已证实 + 协议推断】

**现象**：两处把外部输入直接拼进 OData 字符串字面量，**未对单引号做 OData 转义（`'` → `''`）**：

- **Skill 模板**：[runner.py:15](../app/skills/runner.py#L15) `SandboxedEnvironment(autoescape=False)`，[runner.py:39-42](../app/skills/runner.py#L39) 用 jinja2 渲染 `filter_template`，例如 `Region eq '{{ region }}'`。`region` 来自 LLM/用户。
- **自由模式**：Agent 直接产出 `$filter` 整串（`execute_odata_query`/`build_excel` 的 `filter` 参数），SYSTEM_PROMPT 只说"字符串值用单引号"，**没有任何转义**。
- **目录搜索**：[live.py:87-89](../app/bw/live.py#L87) `substringof('{search}',Title)`，`search` 同样未转义。

**后果**（协议推断，符合 OData V2 规范）：
- **正常数据就报错**：客户名/描述里**带英文撇号**（如 `O'Brien`、`Shanghai Int'l`）会让 `$filter` 语法破裂 → 400。
- **OData 注入**：`region = "HD' or RegionName ne '"` 渲染成 `Region eq 'HD' or RegionName ne ''` → **绕过过滤拿到越权数据**。虽有 BW Analysis Authorization 兜底，但**字段级/行级过滤被破坏**，且可触发昂贵全表扫描。

**修正**：
1. 写一个 **OData 字面量转义器** `odata_str(v) -> "'" + v.replace("'", "''") + "'"`，所有进入 `$filter`/`substringof` 的字符串值统一走它。
2. Skill 模板**不要让 jinja2 直接吐引号**：把参数当**值**注入（模板里写 `Region eq {{ region | odata_quote }}`），由过滤器负责加引号+转义；或干脆模板只声明字段+操作符，由代码按参数类型安全拼装。
3. 自由模式：不让 LLM 直接给整串 `$filter`，改为让它给**结构化条件**（field/op/value 列表），由后端安全拼装并按 `$metadata` 的 Edm 类型决定字面量格式。这同时解决 P1-2。
4. 强化 `enum` 校验（[runner.py:81](../app/skills/runner.py#L81) 已对声明参数校验 enum，但**透传的额外参数**[runner.py:88-91](../app/skills/runner.py#L88) 未校验就进模板）。

---

### 🟠 P1-1　Edm 类型不转换 → Excel 数字变文本，统计/图表失效【已证实 + 协议推断】

**现象**：`_strip_metadata`（[live.py:305](../app/bw/live.py#L305)）只剥 `__metadata`/`__deferred`，**不做任何类型转换**；XML 路径直接 `prop.text`（字符串）。

**后果**（协议推断，OData V2 标准编码）：
- `Edm.Decimal`（`NETWR_F`/`GROSS_PROFIT`/`GP_RATIO` 等金额、比率）在 OData V2 JSON 里是**字符串**（`"1221.4"`），XML 里也是文本。
- `Edm.DateTime` 在 V2 JSON 里是 `"/Date(1714521600000)/"`，在 `$filter` 里要 `datetime'2026-05-01T00:00:00'`。
- 直接写进 Excel → **金额/比率是"文本数字"**，无法求和、排序、做图表、统计分析。本项目主打"可定制表格 + ECharts 图 + 统计"，这条直接废掉下游价值。Mock 用干净 CSV 数字，**掩盖**了该问题（文档 §8.9 已预警 mock≠real）。

**修正**：
1. 用 `get_metadata` 拿到每个 EntitySet 的 `properties[].type`（Edm 类型），在行解析后按类型**强转**：`Edm.Decimal/Double/Int*` → number，`/Date(ms)/` 与 `datetime'...'` → date。
2. 缓存 `$metadata`（见 P2-2），避免每次查询重拉。
3. ExcelBuilder 写单元格时按类型设置 number/date 格式。

---

### 🟠 P1-2　Agent 缺少 OData 字面量/日期/分页的"协议常识" → 自由模式高频 400【已证实】

**现象**：`SYSTEM_PROMPT`（[agent_stream.py](../app/agent_stream.py)）只教了"字符串用单引号、多条件 and/or、top 默认值"，**没教**：
- 单引号转义（`''`）；
- `Edm.DateTime` 字面量语法 `datetime'YYYY-MM-DDTHH:MM:SS'`；
- `CALMONTH` 是 `Edm.String`（`'202605'`）而非日期——日期/月份字段类型不一致；
- 服务端分页 / `$inlinecount` 语义；
- 字段名必须严格来自 `$metadata`（虽提了"不要臆造"，但无强校验）。

**后果**：自由模式下模型对日期范围、特殊字符、聚合（`$apply`）极易拼错 → 反复 400，断路器 3 次后放弃。

**修正**：
1. 把上述 OData V2 约定写进 SYSTEM_PROMPT，并按本系统真实字段给 **1~2 个 few-shot**（如 `CALMONTH eq '202605'`、`ERDAT ge datetime'2026-05-01T00:00:00'`）。
2. 更稳的做法（推荐）：**收口自由度**——不让模型直接写 `$filter` 整串，改让它产出结构化 `{field, op, value}`，后端按 `$metadata` 类型安全生成字面量（与 P0-2 修正合并）。
3. `get_service_metadata` 结果里**显式标注每个字段的 Edm 类型与是否 key**，让模型有据可依。

---

### 🟠 P1-3　发给 LLM 的样本行未脱敏 → 敏感数据外泄给模型/厂商【已证实】

**现象**：脱敏只发生在 **Excel 生成**时（`orchestrator._resolve_sensitive` → ExcelBuilder）。但 Agent 的 `execute_odata_query` 把**前 5 行真实样本**回灌给 LLM（[agent_stream.py](../app/agent_stream.py) `execute_odata_query` 分支 `sample_rows`），**未经任何脱敏**。

**后果**：`需求分析与技术方案.md §14` 承诺"敏感字段发 LLM 前 mask 成 `***`"，但实际**样本行绕过了脱敏直达模型**（尤其用云端 DeepSeek/Qwen/OpenAI 时数据出企业）。这是**合规缺口**，不是样式问题。

**修正**：
1. 把脱敏下沉到 **BWClient 返回边界**或 Agent 回灌前：`execute_odata_query` 的 `sample_rows` 按 `sensitive_fields` 配置 mask 后再喂 LLM。
2. 统一一个 `mask_rows(rows, service)` 工具，样本路径与 Excel 路径都调用，避免两套口径。

---

### 🟠 P1-4　SAP OData 错误体未解析 → Agent 拿不到真因，难自纠【已证实 + 协议推断】

**现象**：出错时客户端只回 `error=f"HTTP {status}"` + 原始 `text[:2000]`（[live.py:199/218/233](../app/bw/live.py#L199)、metadata [live.py:131/139](../app/bw/live.py#L131)）。

**后果**（协议推断）：SAP Gateway 的错误体是结构化的——JSON `{"error":{"code":...,"message":{"value":"Property 'Regionx' not found"}}}`，XML `<error><message>...`。当前**没解析**，Agent 只看到"HTTP 400 + 一坨 XML"，**无法精准自纠**（它需要"哪个字段不存在/哪个值非法"才能改）。

**修正**：在客户端解析 OData 错误体，提取 `error.message.value`（JSON）或 `<error><message>`（XML），归一化成简短中文/原文要点放进 `ODataResponse.error`，再回灌给 Agent。

---

### 🟡 P2-1　sap-client 静默回退可能查错 client / 掩盖真错【已证实】

**现象**：[live.py:69-77](../app/bw/live.py#L69) 与报告清单 [server.py:262-269](../app/server.py#L262)：当带 `sap-client=505` 且返回 **401/403/404** 时，**去掉 sap-client 重试一次**。

**风险**：
- `404` 触发"去 client 重试"在语义上不对（404 是资源不存在，不是 client 问题）。
- 去掉 `sap-client` 后请求会落到 **Gateway 默认 client**——可能是**另一个 client 的数据**，违背"用户只看自己 client/授权范围数据"的前提，且**静默**发生、无日志告警。
- 还会把同一明文凭据**再发一次**（见 P2-3）。

**修正**：仅对**确属 client 相关**的信号回退（且最好可配置 `BW_CLIENT_FALLBACK=off`）；回退**必须 `log.warning` 留痕**；404 不应触发回退。

---

### 🟡 P2-2　每次查询重拉 $metadata / build_excel 二次往返 SAP【已证实】

**现象**：
- 没有 `$metadata` 缓存：列中文 label（`orchestrator._column_labels` → `bw.get_metadata`）和（建议新增的）类型转换都需要 metadata，每次现拉。
- `build_excel` **重新执行一遍查询**（[agent_stream.py](../app/agent_stream.py) build_excel 再调 `execute_query`），而非复用 `execute_odata_query` 已取到的数据 → **对 SAP 双倍负载**，且两次间数据可能变化导致不一致。

**修正**：进程内缓存 `$metadata`（key=service，带 TTL）；导出路径复用已取数据或一次性按导出语义取全量（与 P0-1 合并）。

---

### 🟡 P2-3　HTTP 明文传输 Basic Auth 凭据【已证实】

**现象**：`BW_BASE_URL=http://...:8000`（明文）+ Basic Auth，且 `BW_VERIFY_SSL=false`。凭据在网络上**明文传输**（回退重试再发一次）。

**修正**：能上 HTTPS（Gateway 通常有 8443/443）就切 HTTPS；如确属内网 HTTP-only，需在文档/风险登记中明确标注，并尽量减少凭据重发次数。

---

### 🟡 P2-4　报告清单"前N条"被忽略，live 恒为 5 行；与 mock 路径不一致【已证实】

**现象**：`_run_report_list_shortcut` 先 `top_n = _extract_report_list_top(req.message)`（[server.py:303](../app/server.py#L303)），但 **live 分支硬编码 `rows = rows_all[:5]`**（[server.py:408](../app/server.py#L408)）导出，`top_n` 没用上；而 **mock 分支**用 `execute_query(top=top_n)`（[server.py:608](../app/server.py#L608)）。

**后果**："报告清单前100条"在 live 下仍只导 5 行；mock/live 行为不一致，难调试。

**修正**：live 分支用 `rows_all[:top_n]` 导出（样本预览仍可只显示 5 行）；统一 mock/live 口径。

---

### 🟡 P2-5　JSON/XML 双格式分叉、`$format=json` 假设脆弱【已证实】

**现象**：通用查询请求 `$format=json`（[live.py:158](../app/bw/live.py#L158)），但报告清单单独走 `Accept: application/atom+xml`（[server.py:250](../app/server.py#L250)）。说明真实环境里**至少一个服务的 JSON 不可靠**，才被迫为它写了 XML 专用通道。

**修正**：统一走一条解析链（客户端已能 JSON+XML 兜底解析，[live.py:221](../app/bw/live.py#L221)），把报告清单也并入通用 `execute_query` 路径，消除"固定 URL + 专用 XML 解析"的孤岛；保留 XML 兜底即可。

---

## 3. 修正优先级与建议路线

| 优先级 | 问题 | 一句话修正 | 影响面 |
|---|---|---|---|
| P0-1 | 200 行硬截断 | 拆样本/导出两路 + 服务端分页 | 核心功能 |
| P0-2 | $filter 无转义/可注入 | OData 字面量转义器 + 结构化条件 | 正确性+安全 |
| P1-1 | Edm 类型不转换 | 按 $metadata 强转 number/date | 下游统计图表 |
| P1-3 | 样本未脱敏喂 LLM | 回灌前 mask | 合规 |
| P1-4 | 错误体不解析 | 解析 OData error.message | Agent 自纠 |
| P1-2 | Agent 缺 OData 常识 | 收口为结构化条件 + few-shot | 自由模式可靠性 |
| P2-1 | sap-client 静默回退 | 限条件+留痕+404 不回退 | 数据正确性 |
| P2-2 | metadata 重拉/二次往返 | 缓存 + 复用 | SAP 负载 |
| P2-4 | 报告清单 topN 失效 | live 用 top_n | 一致性 |
| P2-5 | JSON/XML 分叉 | 并入通用路径 | 可维护性 |
| P2-3 | HTTP 明文凭据 | 尽量 HTTPS / 标注风险 | 安全 |

**建议第一刀**：P0-2（转义器，小而稳，先堵注入与撇号崩溃）→ P1-1（类型转换，盘活下游）→ P0-1（分页与全量导出）。三者是"正确性地基"。

---

## 4. 无 SAP 环境下如何验证修正（关键）

因本地无 SAP，**不能**靠连真系统验证。采用三层防护，全部可在 mock/离线下跑：

1. **让 mock 更"像"真 SAP**（暴露被掩盖的问题）：
   - mock 的 `Edm.Decimal` 字段输出**字符串**、`DateTime` 输出 `/Date(ms)/`，复现 P1-1；
   - 在某个 mock 服务故意返回 **OData V2 错误体 JSON/XML**，验证 P1-4 解析；
   - 提供一个 **>200 行** 的 mock EntitySet，验证 P0-1 分页与全量导出；
   - 提供含**撇号**的客户名样本，验证 P0-2 转义。
2. **契约/单元测试**（不连网）：针对转义器、Edm 类型转换、错误体解析、分页拼接 `$skip/$top` 写纯函数测试；用**录制的真实响应样本**（若 `tests/test_live_bw.py` 或对接清单里留有真实 XML/JSON 片段）做回放断言。
3. **接真 SAP 时的最小复核清单**（沿用 `需求分析与技术方案.md §8.8` 的 12 项验收，重点补充）：真实 `$metadata` 的 Edm 类型、真实 Decimal/DateTime 编码、真实 `__next` 分页字段名、真实错误体结构——**这些必须用真响应复核，本文标【协议推断】处以此为准**。

---

## 5. 一句话结论

对接的"管道"（认证、sap-client 回退、JSON/XML 兜底、metadata 简化）骨架是对的；**真正的风险集中在"数据正确性"四件事**：①200 行截断让全量导出失真、②$filter 不转义既崩正常数据又可注入、③Edm 类型不转换让下游统计图表失效、④样本未脱敏的合规缺口。这四件都是**已证实**或**有协议依据且文档自承风险**的硬问题，建议优先修复；其余为可靠性/可维护性优化。所有【协议推断】项在接真 SAP 后须用真实 `$metadata`/响应体复核，不替代真环境验证。
