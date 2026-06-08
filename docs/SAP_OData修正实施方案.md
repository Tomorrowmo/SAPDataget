# SAP OData 修正实施方案

> 配套问题分析见 `docs/SAP_OData对接问题分析与修正方案.md`。
> 原则（老板定）：**生产代码不得含任何"针对 mock 数据"的约束/假设**；mock 与 live
> 只在数据**来源**上切换（`BW_MODE` → factory 选择 Live/Mock），其余"正确性逻辑"
> （转义、Edm 类型转换、分页、错误解析、脱敏）一律走**与数据源无关的共享代码**，
> 并让 **mock 去贴近真实 SAP 的行为**（而非让代码迁就 mock 的干净假象）。

## 一、新增共享模块 `app/odata.py`（纯函数，零数据源耦合）
- `odata_quote(v)`：字符串字面量单引号转义（`'`→`''`）并加引号。
- `edm_literal(value, edm_type)`：按 Edm 类型生成字面量（String→转义引号、DateTime→`datetime'...'`、数值/布尔→裸值）。
- `build_filter(conditions, types)`：把结构化条件 `[{field,op,value}]` 安全拼成 `$filter`（op: eq/ne/gt/ge/lt/le/contains/startswith/endswith），按 `types`(来自 `$metadata`) 决定字面量格式。
- `coerce_edm_value` / `coerce_rows`：把 OData V2 编码（`Edm.Decimal`→float、`Edm.Int*`→int、`/Date(ms)/` 与 `datetime'...'`→ISO 日期、`Edm.Boolean`→bool）转成真实类型。
- `parse_odata_error(text, content_type)`：从 SAP 错误体（JSON `error.message.value` / XML `<error><message>`）提取可读信息。
- `prop_types(metadata_json, entity_set)`：从简化 metadata 抽 `{field: edm_type}`。
- `merge_paged_rows(...)`：分页合并的纯逻辑（供 live 跟随 `__next` 用，可离线单测）。

## 二、修正点（对应问题编号）
| 编号 | 文件 | 修正 |
|---|---|---|
| P0-1 | live.py / mock.py / agent_stream.py | 删除两处 `[:200]` 硬截断（**mock 约束**）；live 跟随 `d.__next` 分页到目标行数(上限 `MAX_EXPORT_ROWS`)；agent 拆"样本(小 top 喂 LLM)"与"导出(全量分页)"两路；Excel `row_count` 用实写行数，另列总数。 |
| P0-2 | odata.py / runner.py / agent_stream.py | SkillRunner 渲染前对字符串参数值做 `''` 转义（无需改模板，现模板皆 `'{{x}}'` 形式）；自由模式新增结构化 `where`，后端用 `build_filter` 安全拼装。 |
| P1-1 | odata.py / live.py | live 拿 `$metadata` 类型，对行做 `coerce_rows`；Excel 写入器已按类型渲染数字/日期(无需改)。mock 数据天然有类型。 |
| P1-2 | agent_stream.py | SYSTEM_PROMPT 补 OData 字面量/日期/分页常识 + few-shot；优先让模型给结构化 `where` 而非裸 `$filter`。 |
| P1-3 | excel/builder.py(复用) / agent_stream.py | 复用 `apply_sensitive_mask` 在**喂 LLM 前**对 sample_rows 脱敏；StreamAgent 注入 `sensitive_resolver`。 |
| P1-4 | odata.py / live.py | 错误时用 `parse_odata_error` 提取 SAP 真实报错放进 `ODataResponse.error`。 |
| P2-1 | live.py / config.py | sap-client 回退仅 401/403（不含 404）；`log.warning` 留痕；新增 `BW_CLIENT_FALLBACK`(默认 on) 可关。 |
| P2-2 | live.py | `$metadata` 进程内缓存（key=service）。 |
| P2-4 | server.py | 报告清单 live 分支用 `top_n`（与 mock 路径一致），样本预览仍可只显 5 行。 |
| P2-5 | （文档说明） | 暂不强行合并报告清单 XML 孤岛(无真 SAP 难验)，但其错误解析改走共享 `parse_odata_error`。 |
| P2-3 | （文档/风险） | HTTP 明文凭据：标注风险，建议 HTTPS；不在本批强改端口。 |

## 三、mock 贴近真 SAP（仅测试数据/夹具，不是生产约束）
- 新增一个 **>200 行**的 mock EntitySet，验证分页/全量导出不再被截。
- 新增**含英文撇号**的样本值（如 `O'Brien`），验证转义后端到端可查。
- 保留 mock 的 pandas filter 解析（其 tokenizer 已支持 `''` 转义，天然与真 SAP 对齐）。

## 四、测试
- `tests/test_odata_util.py`(新)：转义/`build_filter`(注入被中和、撇号正常)/`coerce_*`(Decimal 串→float、`/Date(ms)/`→ISO)/`parse_odata_error`(JSON+XML)/分页合并。
- `tests/test_mock_bw.py`(增)：>200 行返回不截断、撇号过滤端到端、$top/$skip。
- 全量 `pytest` + 前端 `tsc -b`/`vite build` 回归。

## 五、明确边界
- 所有 live 侧改动（分页、Edm 编码、错误体结构）属【协议推断】，**接真 SAP 后须用真实 `$metadata`/响应体复核**（见分析文档 §0、§4）。本批用"真 SAP 编码形状的离线夹具"做单测以最大化可信度，但不替代真环境验收(§8.8 12 项)。
