---
id: report_list
title: 报告清单
description: 查询 SAP 报告目录清单（ReportID + ReportDescription），支持条数控制并导出 Excel。
owner: BW 团队
version: 1
keywords: [报告清单, 报告列表, 报表清单, report list, query list]
visible_to: []
params:
  - name: top_n
    required: false
    default: 200
    description: 返回条数上限，默认 200
---

# 报告清单

## 适用场景
用户输入“报告清单/报告列表”时，快速返回可用报表目录。

## 给 LLM 的指引
- 若用户未指定条数，默认 top_n=200。
- 若用户说“前 N 条”，提取 N 填入 top_n。
- 输出优先给出 Excel 下载链接，并展示前若干行预览。
