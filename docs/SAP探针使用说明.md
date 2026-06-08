# SAP 探针使用说明（tools/sap_probe.py）

本地没有 SAP，无法验证真实 OData 行为。这个探针**在你的生产/测试 SAP 环境跑一次**，
自动把开发需要的真实信息抓成 `tools/sap_probe_report.md`，你拿回来贴给我即可（不臆造）。

## 怎么跑
在仓库根目录：
```
python tools/sap_probe.py --user 你的SAP用户名
```
- 密码：交互输入（getpass，不回显）；或 `--password`，或环境变量 `BW_PASSWORD`。
- `base_url` / `client` 默认读 `.env`（`BW_BASE_URL` / `BW_CLIENT`）；也可 `--base-url` / `--client` 覆盖。
- 想顺便抓某业务服务的真实 Decimal/日期编码：
  ```
  python tools/sap_probe.py --user U --service ZBW_SALES_SRV --entityset SalesByOfficeView
  ```

## 它只做只读 GET，安全。抓这些：
1. **报告清单原始响应**（不带 $top）：`<m:count>` 总数、单页实际返回多少条、有没有分页 next 链接
   —— 直接解释"8506 / 200 / 0"那个现象。
2. **$metadata**：每个字段的 Edm 类型 + 主键 → 定位"归属用户"字段的真实名字。
3. **服务端按用户过滤是否生效**：对候选归属字段试 `$filter=<字段> eq '你的用户'`，看是否成功 + 你的真实条数。
4. **分页是否生效**：客户端 `top=1000` 看能否跟 `__next` 拿多页。
5. **SAP 错误体格式**：故意查不存在的字段，抓真实报错结构。
6. **目录服务**：前 10 个 OData 服务。
7. （可选）任意业务服务的原始 JSON：看金额是否字符串、日期是否 `/Date(ms)/`。

## ⚠ 安全
报告里含少量样本值。**发我之前过一眼，把敏感业务数据打码**。生成的
`tools/sap_probe_report.md` 已加入 `.gitignore`，不会被提交。

## 拿回来之后
我据此把"报告清单"和通用查询的**按用户过滤改成服务端 `$filter` 注入**、确认分页/类型/错误体，
让"8506 / 返回 / Excel"三个数字归一致。
