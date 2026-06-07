# 当前开发进展与 SAP 对接接口清单

日期：2026-06-07

本文档用于把当前项目状态、SAP/BW 对接设计、接口清单和已知问题同步给后续协作方，例如 Claude。

## 1. 当前开发进展

### 1.1 项目运行状态

当前项目是一个 SAP BW 智能取数平台，包含：

- 后端：FastAPI
- 前端：React + Vite + TypeScript
- 数据存储：SQLite
- BW/OData 客户端：`app/bw/live.py`
- LLM 适配：LiteLLM，当前切换到 Qwen 模型

当前后端以 live 模式运行，配置目标 SAP 主机：

```text
BW_MODE=live
BW_BASE_URL=http://sapbd1app01.cn.schneider-electric.com:8000
BW_CLIENT=505
BW_LANGUAGE=EN
BW_VERIFY_SSL=false
LLM_MODEL=dashscope/qwen-plus
```

注意：后端已实现 sap-client 回退逻辑。如果带 `sap-client=505` 请求返回 `401/403/404`，会自动去掉 `sap-client` 再请求一次，以兼容“只需要账号密码即可打开 OData”的系统。

### 1.2 当前 Git 状态

Git 上传尚未成功。

当前检查结果：

```text
HAS_GIT_DIR=NO
where.exe git -> 未找到 git
```

也就是说：

- 当前目录还不是 Git 仓库，没有 `.git` 目录
- 当前 Windows 环境里没有可用的 `git.exe`
- 所以还没有完成 `git init`、`commit` 或 `push`

需要先安装 Git，再执行初始化、提交和推送到：

```text
https://github.com/Tomorrowmo/SAPDataget.git
```

### 1.3 已完成的关键功能

- 后端服务可启动
- 前端 Vite 服务可启动
- pytest 冒烟和关键测试通过
- 登录阶段不再校验 SAP 权限
- 登录后后端保存 BW 用户名和密码
- 真正访问 OData 时再使用保存的 BW 凭据
- 报告/报表清单自然语言快捷分支已接入
- OData JSON 与 XML/Atom 返回均可解析为二维表结构
- Qwen 云端模型 `dashscope/qwen-plus` 已可用

## 2. 登录与 BW 凭据逻辑

### 2.1 登录阶段

文件：`app/server.py`

接口：

```text
POST /api/auth/login
```

当前设计：

1. 前端输入 BW 用户名和密码。
2. 后端不立即访问 SAP，也不做权限校验。
3. 后端创建本地登录会话。
4. 后端把密码加密后保存到 SQLite。
5. 前端进入系统。

这样做是为了满足当前需求：

> 登录页面输入账号和密码时，不做权限控制，直接进入系统；后续访问 OData 时，如果需要账号密码，再把后端保存的账号密码填进去。

### 2.2 凭据保存

涉及文件：

- `app/auth.py`
- `app/crypto.py`
- `app/db.py`

保存逻辑：

- `save_credentials(username, password, db=STATE.db)`
- 使用 AES-256-GCM 加密
- 写入 SQLite 表 `bw_creds`
- JWT cookie 中保存 `cred_id`
- 后续 OData 请求通过 `cred_id` 解密拿回密码

注意事项：

- `BW_CRED_KEY` 未配置时，进程启动会生成临时密钥。
- 如果后端重启且没有固定 `BW_CRED_KEY`，旧凭据可能无法解密。
- 这会导致后端无法使用之前保存的密码，需要用户重新登录。

## 3. SAP/BW OData 对接设计

### 3.1 LiveBWClient

文件：`app/bw/live.py`

核心类：

```python
LiveBWClient
```

职责：

- 使用 HTTP Basic Auth 请求 SAP NetWeaver Gateway OData
- 自动附加语言参数 `sap-language`
- 在配置存在 client 时先附加 `sap-client`
- 如果 `sap-client` 导致失败，自动去掉 client 重试
- 解析 OData JSON
- 解析 OData XML/Atom feed
- 统一返回 `ODataResponse`

### 3.2 请求基础路径

当前 SAP 主机：

```text
http://sapbd1app01.cn.schneider-electric.com:8000
```

OData 服务根路径：

```text
/sap/opu/odata/sap
```

完整业务 OData 示例：

```text
http://sapbd1app01.cn.schneider-electric.com:8000/sap/opu/odata/sap/ZBW_QUERY_LIST_SRV/LtResultSet
```

### 3.3 sap-client 策略

当前 `_get()` 请求策略：

1. 先带默认参数请求：

```text
sap-client=505
sap-language=EN
```

2. 如果返回：

```text
401 / 403 / 404
```

3. 自动去掉 `sap-client` 再请求一次。

目的：

- 兼容某些系统不需要 client 或不接受显式 client 参数的情况。
- 用户只需要输入账号密码即可访问 OData。

## 4. 已接入的 SAP/OData 接口清单

### 4.1 报告/报表清单

业务用途：获取报表清单，包括报告 ID 和报告描述。

服务名：

```text
ZBW_QUERY_LIST_SRV
```

实体集：

```text
LtResultSet
```

完整 URL：

```text
http://sapbd1app01.cn.schneider-electric.com:8000/sap/opu/odata/sap/ZBW_QUERY_LIST_SRV/LtResultSet
```

自然语言触发词：

```text
报告清单
报告列表
报表清单
报表列表
report list
query list
```

后端常量位置：`app/server.py`

```python
REPORT_LIST_SERVICE = "ZBW_QUERY_LIST_SRV"
REPORT_LIST_ENTITY_SET = "LtResultSet"
```

前端输入这些自然语言时，后端会走内置快捷分支：

```text
builtin/report-list
```

该分支不会消耗 LLM token。

### 4.2 报告清单字段结构

根据当前 mock 元数据定义，字段为：

| 字段名 | 中文含义 | 类型 | 说明 |
|---|---|---|---|
| ReportID | 报告 ID | String | 主键 |
| ReportDescription | 报告描述 | String | 报告说明 |

mock 定义文件：

```text
mock_data/services/ZBW_QUERY_LIST_SRV/meta.json
```

mock 数据文件：

```text
mock_data/services/ZBW_QUERY_LIST_SRV/data/LtResultSet.csv
```

样例数据：

| ReportID | ReportDescription |
|---|---|
| ZRPT_SALES_OVERVIEW | 销售总览报表 |
| ZRPT_MARGIN_ANALYSIS | 毛利分析报表 |
| ZRPT_CUSTOMER_TOP | 客户销售排行报表 |
| ZRPT_FACTORY_YIELD | 工厂良率报表 |
| ZRPT_PROC_DELIVERY | 采购交付跟踪报表 |

## 5. OData 返回解析逻辑

文件：`app/bw/live.py`

方法：

```python
execute_query(...)
```

当前支持三类 OData 返回。

### 5.1 OData V2 JSON：`d.results`

示例结构：

```json
{
  "d": {
    "results": [
      {
        "ReportID": "ZRPT_SALES_OVERVIEW",
        "ReportDescription": "销售总览报表"
      }
    ],
    "__count": "1"
  }
}
```

解析后：

```json
{
  "rows": [...],
  "row_count_returned": 1,
  "row_count_total": "1"
}
```

### 5.2 OData JSON：`value` 数组

示例结构：

```json
{
  "value": [
    {
      "ReportID": "R1",
      "ReportDescription": "A"
    }
  ],
  "@odata.count": "1"
}
```

解析后同样转为：

```json
{
  "rows": [...],
  "row_count_returned": 1,
  "row_count_total": "1"
}
```

### 5.3 XML/Atom feed

示例结构：

```xml
<feed>
  <entry>
    <content>
      <m:properties>
        <d:ReportID>ZRPT_SALES_OVERVIEW</d:ReportID>
        <d:ReportDescription>销售总览报表</d:ReportDescription>
      </m:properties>
    </content>
  </entry>
</feed>
```

解析函数：

```python
_parse_odata_xml_rows(xml_text)
```

解析后也统一转为：

```json
{
  "rows": [...],
  "row_count_returned": 1,
  "row_count_total": null
}
```

## 6. 前端展示逻辑

主要文件：

- `web/src/pages/Chat.tsx`
- `web/src/components/DataTable.tsx`

后端返回：

```json
{
  "task": {
    "status": "done",
    "row_count": 5,
    "rows_preview": [
      {
        "ReportID": "ZRPT_SALES_OVERVIEW",
        "ReportDescription": "销售总览报表"
      }
    ]
  }
}
```

前端会使用 `DataTable` 将 `rows_preview` 直接渲染成二维表。

## 7. 当前已知问题

### 7.1 报表清单仍可能 HTTP 401

现象：

```text
查询报告清单失败: HTTP 401
builtin/report-list · in 0 / out 0 tokens
```

原因：

- 这是 SAP 网关在真正访问 OData 时拒绝了当前保存的 BW 凭据。
- 登录阶段按需求不校验密码，因此即使密码输错也能进入系统。
- 错误会延后到 OData 请求阶段出现。

建议排查：

1. 确认当前前端登录时输入的是正确 BW 用户名和密码。
2. 确认该账号能直接打开：

```text
http://sapbd1app01.cn.schneider-electric.com:8000/sap/opu/odata/sap/ZBW_QUERY_LIST_SRV/LtResultSet
```

3. 如果浏览器能打开但系统 401，重点检查：
   - 后端保存的密码是否是最新的
   - `BW_CRED_KEY` 是否重启后变化导致旧凭据不可解密
   - 是否需要清理旧登录 cookie 后重新登录

### 7.2 根目录存在导出文件

当前根目录有一些运行产物：

```text
full_odata_response.json
full_odata_response_all_fields.csv
full_odata_response_all_fields.xlsx
full_odata_field_dictionary.csv
full_odata_field_dictionary.xlsx
```

如果后续推送 GitHub，需要决定是否上传这些文件。它们可能包含业务数据，建议默认不要上传，或先人工确认内容。

## 8. LLM 对接状态

当前云端 Qwen 已切换为：

```text
LLM_MODEL=dashscope/qwen-plus
```

用户态验证结果：

```text
CURRENT=dashscope/qwen-plus
QWEN_PLUS_READY=True
```

之前本地 Qwen：

```text
ollama/qwen2.5:72b
```

失败原因：

```text
OllamaException - [WinError 10061] 目标计算机积极拒绝，无法连接
```

含义：本机 Ollama 服务未启动。

## 9. 给 Claude 的接手建议

如果 Claude 接手，建议优先关注以下路径：

1. `app/server.py`
   - 登录接口 `/api/auth/login`
   - 聊天接口 `/api/chat`
   - 报表清单快捷分支 `builtin/report-list`

2. `app/auth.py`
   - 凭据保存、解密、JWT 相关逻辑

3. `app/crypto.py`
   - AES-GCM 密钥与加解密逻辑

4. `app/bw/live.py`
   - SAP OData 请求逻辑
   - sap-client 回退逻辑
   - JSON/XML 转二维表逻辑

5. `web/src/pages/Chat.tsx`
   - 自然语言输入和结果展示

6. `web/src/components/DataTable.tsx`
   - 表格展示组件

7. `tests/test_live_bw.py`
   - OData XML/JSON 解析与 sap-client 回退测试

## 10. 下一步建议

1. 安装 Git 并初始化仓库。
2. 决定是否忽略根目录导出的真实数据文件。
3. 固定 `BW_CRED_KEY`，避免重启后旧 BW 凭据无法解密。
4. 在前端重新登录真实 BW 账号密码。
5. 再次输入“报表清单有什么”。
6. 如果仍 401，将 SAP 返回的原始错误体透传到前端，便于判断是密码错误、权限不足还是服务策略限制。
